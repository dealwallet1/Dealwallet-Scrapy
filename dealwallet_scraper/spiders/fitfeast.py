import asyncio

import csv

import json

import logging

import re

import sys

from datetime import datetime, timezone

from concurrent.futures import ProcessPoolExecutor

from decimal import Decimal, ROUND_HALF_UP

from urllib.parse import urljoin, urlparse, urlunparse



import scrapy

from bs4 import BeautifulSoup

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode





BASE_URL = "https://fitfeast.in"

COLLECTION_URLS = ["https://fitfeast.in/collections/all?page=1"]

CURRENCY = "₹"

ORGANIZATION_ID = "Dealwallet"

STORE_ID = "FitFeast"

CATEGORY = "Food"

MAX_PRODUCTS = 100

MAX_PAGES = 30



OUTPUT_JSON = "fitfeast_products.json"

OUTPUT_CSV = "fitfeast_products.csv"

SPIDER_JSON = "scrape_fitfeast.json"



HEADERS = {

    "User-Agent": (

        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

        "AppleWebKit/537.36 (KHTML, like Gecko) "

        "Chrome/130.0.0.0 Safari/537.36"

    ),

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
    value = re.sub(r"[✅☑✔✓]", "", value)

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





def normalize_rating(value):

    """Round ratings to one decimal using conventional half-up rounding."""

    if value is None:

        return None

    match = re.search(r"\d+(?:\.\d+)?", str(value))

    if not match:

        return None

    try:

        rating = Decimal(match.group(0))

        if rating <= 0:

            return None

        return float(rating.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    except Exception:

        return None





def extract_rating(card_or_soup):

    # FitFeast Judge.me review badge stores the rating in a data attribute.

    for selector in (".jdgm-prev-badge", ".jdgm-prev-badge__stars", ".rating", "[itemprop='ratingValue']"):

        element = card_or_soup.select_one(selector)

        if element:

            raw = (

                element.get("data-average-rating")

                or element.get("data-score")

                or element.get("content")

                or element.get_text(" ", strip=True)

            )

            rating = normalize_rating(raw)

            if rating is not None:

                return rating

    return None





def extract_image_url(image_el, page_url):

    if not image_el:

        return None

    image_url = image_el.get("src") or image_el.get("data-src") or image_el.get("data-original")

    if not image_url:

        srcset = image_el.get("srcset") or image_el.get("data-srcset") or ""

        candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]

        if candidates:

            image_url = candidates[-1]

    return normalize_url(image_url, page_url)





def extract_listing_products(html, page_url):

    soup = BeautifulSoup(html or "", "html.parser")

    # FitFeast collection card selector supplied by the user.

    cards = soup.select("li.grid__item")

    products, seen = [], set()



    for card in cards:

        link_el = (

            card.select_one(".product-title-price h3.card__heading a[href*='/products/']")

            or card.select_one(".card__information a[href*='/products/']")

            or card.select_one("a[href*='/products/']")

        )

        if not link_el:

            continue



        product_link = normalize_url(link_el.get("href"), page_url)

        if not product_link or product_link in seen or "/products/" not in urlparse(product_link).path:

            continue

        seen.add(product_link)



        name = clean_text(link_el.get_text(" ", strip=True))

        if not name:

            title_el = card.select_one("h3.card__heading")

            name = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""



        # Image selector from the supplied card HTML.

        image_el = card.select_one(".card__media img") or card.select_one("img")

        image_link = extract_image_url(image_el, page_url)



        # In the supplied HTML, compare-price is MRP and original-price is sale price.

        selling_el = card.select_one(".price .original-price.price-item--sale")

        original_el = card.select_one(".price .compare-price.price-item--regular")

        price = price_to_int(selling_el.get_text(" ", strip=True)) if selling_el else None

        original_price = price_to_int(original_el.get_text(" ", strip=True)) if original_el else None



        ratings = extract_rating(card)



        # Variant data can reveal availability. Skip only when all listed variants are unavailable.

        variant_options = card.select("select.popular-product-variants option[data-available]")

        all_variants_unavailable = bool(variant_options) and all(

            str(option.get("data-available", "")).lower() == "false"

            for option in variant_options

        )

        card_text = card.get_text(" ", strip=True).lower()

        sold_out_badge = bool(card.select_one(".badge--sold-out, .sold-out, [data-available='false']"))

        if all_variants_unavailable or (sold_out_badge and "add to cart" not in card_text):

            print(f"[SKIP OUT OF STOCK] {name}")

            continue



        if not name or not product_link:

            continue



        products.append({

            "name": name,

            "product_link": product_link,

            "image_link": image_link,

            "ratings": ratings,

            "price": price,

            "original_price": original_price,

            "discount": calculate_discount(price, original_price),

            "description": "",

        })



    return products





async def enrich_from_product_page(crawler, product, config):

    """Open each FitFeast product page and fill fields missing from the collection card."""

    try:

        result = await crawler.arun(url=product["product_link"], config=config)

        if not result.success:

            print(f"[PRODUCT PAGE ERROR] {product['product_link']}: {result.error_message}")

            return product



        soup = BeautifulSoup(result.html or "", "html.parser")



        # Product page title; only replace the collection title if a full title is found.

        title = (

            soup.select_one("h1.product__title")

            or soup.select_one("h1[itemprop='name']")

            or soup.select_one("h1")

        )

        if title:

            full_name = clean_text(title.get_text(" ", strip=True))

            if full_name:

                product["name"] = full_name



        # Use product-page price data only if listing-page price was not found.

        if product.get("price") is None:

            for selector in (

                ".price__sale .price-item--sale",

                ".price .price-item--sale",

                ".product__info-container .price",

                "[itemprop='price']",

            ):

                element = soup.select_one(selector)

                if element:

                    raw = element.get("content") or element.get_text(" ", strip=True)

                    value = price_to_int(raw)

                    if value is not None:

                        product["price"] = value

                        break



        if product.get("original_price") is None:

            for selector in (

                ".price__sale .price-item--regular",

                ".price .price-item--regular",

                "s.price-item",

                "del",

            ):

                element = soup.select_one(selector)

                if element:

                    value = price_to_int(element.get_text(" ", strip=True))

                    if value is not None:

                        product["original_price"] = value

                        break



        if not product.get("description"):

            for selector in (

                ".product__description",

                "[itemprop='description']",

                ".product__accordion .accordion__content",

                ".rte",

            ):

                element = soup.select_one(selector)

                if element:

                    description = clean_text(element.get_text(" ", strip=True))

                    if description:

                        product["description"] = description

                        break



        if not product.get("image_link"):

            image_el = (

                soup.select_one(".product__media img")

                or soup.select_one(".product__main-photos img")

                or soup.select_one("img[alt]")

            )

            product["image_link"] = extract_image_url(image_el, product["product_link"])



        if product.get("ratings") is None:

            product["ratings"] = extract_rating(soup)



        product["discount"] = calculate_discount(

            product.get("price"), product.get("original_price")

        )

    except Exception as exc:

        print(f"[PRODUCT ENRICH ERROR] {product.get('product_link')}: {exc}")

    return product





def is_valid_http_url(value):
    if not value:
        return False
    parsed = urlparse(str(value).strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def make_output_product(item):
    name = clean_text(item.get("name"))
    price = item.get("price")
    original_price = item.get("original_price")
    description = clean_text(item.get("description"))
    image_link = normalize_url(item.get("image_link"), item.get("product_link") or BASE_URL)
    product_link = normalize_url(item.get("product_link"), BASE_URL)

    # Discount 0 is valid. Calculate it from the two validated prices.
    if price is None or original_price is None or price <= 0 or original_price <= price:
        print(f"[SKIPPED INVALID PRICE] {name} | price={price} | original_price={original_price}")
        return None

    discount = calculate_discount(price, original_price)

    missing_fields = []
    if not name:
        missing_fields.append("name")
    if not description:
        missing_fields.append("description")
    if not image_link or not is_valid_http_url(image_link):
        missing_fields.append("image_link")
    if not product_link or not is_valid_http_url(product_link):
        missing_fields.append("product_link")
    if discount is None:
        missing_fields.append("discount")

    if missing_fields:
        print(f"[SKIPPED MISSING MANDATORY FIELDS] {name}: {', '.join(missing_fields)}")
        return None

    return {
        "name": name,
        "price": int(price),
        "currency": CURRENCY,
        "original_price": int(original_price),
        "discount": int(discount),
        "ratings": normalize_rating(item.get("ratings")),
        "description": description,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": CATEGORY,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_json(products):

    with open(OUTPUT_JSON, "w", encoding="utf-8") as file:

        json.dump(products, file, ensure_ascii=False, indent=2)

    print(f"JSON generated: {OUTPUT_JSON}")





def save_csv(products):

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as file:

        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")

        writer.writeheader()

        writer.writerows(products)

    print(f"CSV generated: {OUTPUT_CSV}")





async def scrape_collection(crawler, collection_url, seen):

    products = []

    # Wait for the page body, not product cards. The final collection page may

    # not exist, so waiting for li.grid__item there would cause a long timeout.

    listing_config = CrawlerRunConfig(

        cache_mode=CacheMode.BYPASS,

        wait_for="css:body",

        page_timeout=120000,

    )

    product_config = CrawlerRunConfig(

        cache_mode=CacheMode.BYPASS,

        wait_for="css:body",

        page_timeout=120000,

    )



    for page in range(1, MAX_PAGES + 1):

        if len(products) >= MAX_PRODUCTS:

            break



        # First URL already contains page=1; subsequent pages use page=2, 3, etc.

        parsed = urlparse(collection_url)

        base_path = parsed.path

        page_url = f"{parsed.scheme}://{parsed.netloc}{base_path}?page={page}"

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

            print("[STOP] No FitFeast product cards found")

            break



        new_candidates = [item for item in candidates if item["product_link"] not in seen]

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

                print(

                    f"[SAVED] {final_item['name']} | ₹{final_item['price']} | "

                    f"MRP ₹{final_item['original_price']} | {final_item['discount']}% OFF"

                )



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

    print("\nFITFEAST SCRAPING COMPLETED")

    print(f"Total valid products: {len(all_products)}")

    return all_products





def run_fitfeast_scraper():

    logging.basicConfig(

        level=logging.INFO,

        format="%(asctime)s [%(levelname)s] %(message)s",

        stream=sys.stdout,

        force=True,

    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    return asyncio.run(main())





class FitFeastSpider(scrapy.Spider):

    name = "fitfeast"

    allowed_domains = ["fitfeast.in", "www.fitfeast.in"]



    async def start(self):

        self.logger.info("Starting FitFeast scraper through Scrapy...")

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:

            products = await loop.run_in_executor(executor, run_fitfeast_scraper)



        with open(SPIDER_JSON, "w", encoding="utf-8") as file:

            json.dump(products, file, ensure_ascii=False, indent=2)



        for product in products:

            if isinstance(product, dict) and product.get("product_link"):

                yield product

        self.logger.info("FitFeast spider completed: %s products", len(products))





if __name__ == "__main__":

    data = run_fitfeast_scraper()

    with open(SPIDER_JSON, "w", encoding="utf-8") as file:

        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"Saved in {SPIDER_JSON}")
