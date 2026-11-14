import asyncio

import csv

import json

import logging

import re

import sys

from datetime import datetime, timezone

from concurrent.futures import ProcessPoolExecutor

from urllib.parse import urljoin, urlparse, urlunparse



import scrapy

from bs4 import BeautifulSoup

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode




BASE_URL = "https://www.fabartcraft.com"

COLLECTION_URLS = ["https://www.fabartcraft.com/mukhwas"]

CURRENCY = "₹"

ORGANIZATION_ID = "Dealwallet"

STORE_ID = "FabArtCraft"

CATEGORY = "Food"

MAX_PRODUCTS = 100

MAX_PAGES = 30

OUTPUT_JSON = "fabartcraft_products.json"

OUTPUT_CSV = "fabartcraft_products.csv"

SPIDER_JSON = "scrape_fabartcraft.json"



HEADERS = {

    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",

    "Accept-Language": "en-IN,en;q=0.9",

}

CSV_FIELDS = [

    "name", "price", "currency", "original_price", "discount", "ratings",

    "description", "image_link", "product_link", "organization_id", "store_id",

    "categories_id", "created_at",

]





def clean_text(value):

    if not value:

        return ""

    value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)

    value = value.replace("/", "").replace('"', "")

    value = re.sub(r"\\+", "", value)

    return re.sub(r"\s+", " ", value).strip()





def price_to_int(value):

    if value is None:

        return None

    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", str(value).replace("₹", ""))

    if not match:

        return None

    try:

        return int(float(match.group(0).replace(",", "")))

    except (TypeError, ValueError):

        return None





def normalize_url(url, base_url=BASE_URL):

    if not url:

        return None

    url = str(url).strip()

    if url.startswith("//"):

        url = "https:" + url

    url = urljoin(base_url, url)

    parsed = urlparse(url)

    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))





def calculate_discount(price, original_price):

    if price is None or original_price is None or original_price <= 0 or price >= original_price:

        return 0

    return int(round((original_price - price) / original_price * 100))





def extract_rating(card_or_soup):

    for selector in (".product-star", ".rating", ".product-rating", "[itemprop='ratingValue']"):

        element = card_or_soup.select_one(selector)

        if element:

            raw = element.get("content") or element.get_text(" ", strip=True)

            match = re.search(r"\d+(?:\.\d+)?", raw or "")

            if match:

                try:

                    value = float(match.group(0))

                    return round(value, 1) if value > 0 else None

                except ValueError:

                    pass

    return None





def extract_listing_products(html, page_url):

    soup = BeautifulSoup(html or "", "html.parser")

   

    cards = soup.select("li.col-sx-12.col-sm-4.col-xs-6")

    if not cards:

        cards = soup.select("li:has(.product-container), .product-container")



    products, seen = [], set()

    for card in cards:

        container = card.select_one(".product-container") or card

        link_el = (

            container.select_one(".left-block a[href*='/product/']")

            or container.select_one("h5.product-name a[href*='/product/']")

            or container.select_one("a[href*='/product/']")

        )

        if not link_el:

            continue

        product_link = normalize_url(link_el.get("href"), page_url)

        if not product_link or product_link in seen or "/product/" not in urlparse(product_link).path:

            continue

        seen.add(product_link)



        title_el = container.select_one("h5.product-name strong") or container.select_one("h5.product-name")

        name = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""

        image_el = container.select_one(".left-block img.img-responsive") or container.select_one("img")

        image_link = normalize_url((image_el.get("src") or image_el.get("data-src")) if image_el else None, page_url)



        price_el = container.select_one(".content_price span.price.product-price")

        old_price_el = container.select_one(".content_price span.price.old-price")

        price = price_to_int(price_el.get_text(" ", strip=True)) if price_el else None

        original_price = price_to_int(old_price_el.get_text(" ", strip=True)) if old_price_el else None



        desc_el = container.select_one(".info-orther .product-desc") or container.select_one(".product-desc")

        description = clean_text(desc_el.get_text(" ", strip=True)) if desc_el else ""

        availability_el = container.select_one("p.availability span")

        availability = clean_text(availability_el.get_text(" ", strip=True)).lower() if availability_el else ""



        # Skip explicit out-of-stock items.

        card_text = container.get_text(" ", strip=True).lower()

        if "out of stock" in card_text or availability in {"out of stock", "unavailable"}:

            print(f"[SKIP OUT OF STOCK] {name}")

            continue

        if not name or not product_link:

            continue



        discount = calculate_discount(price, original_price)

        products.append({

            "name": name,

            "product_link": product_link,

            "image_link": image_link,

            "ratings": extract_rating(container),

            "price": price,

            "original_price": original_price,

            "discount": discount,

            "description": description,

            "availability": availability,

        })

    return products





async def enrich_from_product_page(crawler, product, config):

    """Use product page to fill any missing fields while retaining listing values."""

    try:

        result = await crawler.arun(url=product["product_link"], config=config)

        if not result.success:

            print(f"[PRODUCT PAGE ERROR] {product['product_link']}: {result.error_message}")

            return product

        soup = BeautifulSoup(result.html or "", "html.parser")



        # Prefer the full product-page name over the shortened listing-card name.

        title = (

            soup.select_one("h1.product-name.desk_only[itemprop='name']")

            or soup.select_one("h1.product-name[itemprop='name']")

            or soup.select_one("h1.product-name")

            or soup.select_one("h1, .page-heading")

        )

        if title:

            full_name = clean_text(title.get_text(" ", strip=True))

            if full_name:

                product["name"] = full_name



        # Product-page price fallback only if listing data was missing.

        if product.get("price") is None:

            for selector in (".content_price span.price.product-price", ".product-price", ".price"):

                el = soup.select_one(selector)

                if el:

                    product["price"] = price_to_int(el.get_text(" ", strip=True))

                    if product["price"] is not None:

                        break

        if product.get("original_price") is None:

            for selector in (".content_price span.price.old-price", ".old-price", "del", "s"):

                el = soup.select_one(selector)

                if el:

                    product["original_price"] = price_to_int(el.get_text(" ", strip=True))

                    if product["original_price"] is not None:

                        break



        if not product.get("description"):

            for selector in (".product-desc", ".product-description", "[itemprop='description']", ".rte"):

                el = soup.select_one(selector)

                if el:

                    product["description"] = clean_text(el.get_text(" ", strip=True))

                    if product["description"]:

                        break

        if not product.get("image_link"):

            img = soup.select_one(".product-cover img, .product-image img, img")

            if img:

                product["image_link"] = normalize_url(img.get("src") or img.get("data-src"), product["product_link"])

        if product.get("ratings") is None:

            product["ratings"] = extract_rating(soup)

    except Exception as exc:

        print(f"[PRODUCT ENRICH ERROR] {product.get('product_link')}: {exc}")

    return product





def make_output_product(item):
    name = clean_text(item.get("name"))
    price = item.get("price")
    original_price = item.get("original_price")
    discount = calculate_discount(price, original_price)
    description = clean_text(item.get("description"))
    image_link = normalize_url(item.get("image_link"), item.get("product_link") or BASE_URL)
    product_link = normalize_url(item.get("product_link"), BASE_URL)

    # All seven fields are mandatory. A numeric discount of 0 is valid.
    required_fields = {
        "name": name,
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "description": description,
        "image_link": image_link,
        "product_link": product_link,
    }
    missing_fields = [field for field, value in required_fields.items() if value is None or value == ""]
    valid_image_url = bool(
        image_link and urlparse(image_link).scheme in {"http", "https"} and urlparse(image_link).netloc
    )
    valid_product_url = bool(
        product_link and urlparse(product_link).scheme in {"http", "https"} and urlparse(product_link).netloc
    )

    if (
        missing_fields
        or price <= 0
        or original_price <= 0
        or not valid_image_url
        or not valid_product_url
    ):
        print(
            f"[SKIPPED INVALID PRODUCT] {name} | missing={missing_fields} | "
            f"price={price} | original_price={original_price} | "
            f"image_valid={valid_image_url} | product_valid={valid_product_url}"
        )
        return None

    return {
        "name": name,
        "price": int(price),
        "currency": CURRENCY,
        "original_price": int(original_price),
        "discount": int(discount),
        "ratings": item.get("ratings"),
        "description": description,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": CATEGORY,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_json(products):

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:

        json.dump(products, f, ensure_ascii=False, indent=2)

    print(f"JSON generated: {OUTPUT_JSON}")





def save_csv(products):

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:

        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")

        writer.writeheader()

        writer.writerows(products)

    print(f"CSV generated: {OUTPUT_CSV}")





async def scrape_collection(crawler, collection_url, seen):

    products = []

    listing_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, wait_for="css:body", page_timeout=120000)

    product_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, wait_for="css:body", page_timeout=120000)



    for page in range(1, MAX_PAGES + 1):

        if len(products) >= MAX_PRODUCTS:

            break

        page_url = collection_url if page == 1 else f"{collection_url}?page={page}"

        print(f"\n[COLLECTION PAGE {page}] {page_url}")

        try:

            result = await crawler.arun(url=page_url, config=listing_config)

        except Exception as exc:

            print(f"[LISTING ERROR] {page_url}: {exc}")

            break

        if not result.success:

            print(f"[LISTING ERROR] {page_url}: {result.error_message}")

            break



        candidates = extract_listing_products(result.html or "", page_url)

        print(f"[CARDS FOUND] {len(candidates)}")

        if not candidates:

            break

        new_candidates = [p for p in candidates if p["product_link"] not in seen]

        if not new_candidates:

            print("[STOP] No new product links on this page")

            break



        for item in new_candidates:

            if len(products) >= MAX_PRODUCTS:

                break

            seen.add(item["product_link"])

            print(f"[PRODUCT {len(products) + 1}/{MAX_PRODUCTS}] {item['name']}")

            item = await enrich_from_product_page(crawler, item, product_config)

            final_item = make_output_product(item)

            if final_item:

                products.append(final_item)

                print(f"[SAVED] {final_item['name']} | ₹{final_item['price']} | MRP ₹{final_item['original_price']} | {final_item['discount']}% OFF")

        print(f"[VALID TOTAL] {len(products)}/{MAX_PRODUCTS}")

    return products





async def main():

    browser_config = BrowserConfig(headless=True, verbose=False, headers=HEADERS)

    all_products, seen = [], set()

    async with AsyncWebCrawler(config=browser_config) as crawler:

        for collection_url in COLLECTION_URLS:

            remaining = MAX_PRODUCTS - len(all_products)

            if remaining <= 0:

                break

            print("\n========================================")

            print(f"SCRAPING: {collection_url}")

            print(f"GLOBAL TOTAL: {len(all_products)}/{MAX_PRODUCTS}")

            print("========================================")

            products = await scrape_collection(crawler, collection_url, seen)

            all_products.extend(products[:remaining])



    save_json(all_products)

    save_csv(all_products)

    print("\nFABARTCRAFT SCRAPING COMPLETED")

    print(f"Total valid products: {len(all_products)}")

    return all_products





def run_fabartcraft_scraper():

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout, force=True)

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    return asyncio.run(main())





class FabArtCraftSpider(scrapy.Spider):

    name = "fabartcraft"

    allowed_domains = ["fabartcraft.com", "www.fabartcraft.com"]



    async def start(self):

        self.logger.info("Starting FabArtCraft scraper through Scrapy...")

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:

            products = await loop.run_in_executor(executor, run_fabartcraft_scraper)

        with open(SPIDER_JSON, "w", encoding="utf-8") as f:

            json.dump(products, f, ensure_ascii=False, indent=2)

        for product in products:

            if isinstance(product, dict) and product.get("product_link"):

                yield product

        self.logger.info("FabArtCraft spider completed: %s products", len(products))





if __name__ == "__main__":

    data = run_fabartcraft_scraper()

    with open(SPIDER_JSON, "w", encoding="utf-8") as f:

        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved in {SPIDER_JSON}")
