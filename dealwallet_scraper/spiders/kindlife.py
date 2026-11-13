import asyncio

import json

import csv
import scrapy

import re
import logging
import sys

from concurrent.futures import ProcessPoolExecutor

from datetime import datetime, timezone

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from crawl4ai import (

    AsyncWebCrawler,

    BrowserConfig,

    CrawlerRunConfig,

    CacheMode,

)







CATEGORIES = {

    "Food":"https://www.kindlife.in/wellness/wellness-protein/",

    "Makeup": "https://www.kindlife.in/makeup/face/",

    "Beauty":"https://www.kindlife.in/bath-and-body/fragrance/?features_hash=25-2782-2778-2780",

    "Medical & Pharmacy": "https://www.kindlife.in/wellness/wellness-health/",

    "Health & Beauty": "https://www.kindlife.in/wellness/wellness-beauty/",

}



PRODUCTS_PER_CATEGORY = 40

PRODUCT_DELAY = 0.5

PAGE_LOAD_DELAY = 4

PRODUCT_PAGE_TIMEOUT = 45



JSON_FILE = "kindlife_products.json"

CSV_FILE = "kindlife_products.csv"



ORGANIZATION_ID = "Dealwallet"

STORE_ID = "Kindlife"



FIELDS = [

    "name",

    "price",

    "currency",

    "original_price",

    "discount",

    "ratings",

    "description",

    "image_link",

    "product_link",

    "organization_id",

    "store_id",

    "categories_id",

    "created_at",

]







def clean_text(value):

    if not value:

        return None



    value = str(value)

    value = value.replace("\xa0", " ")

    value = re.sub(r"\s+", " ", value)



    return value.strip() or None





def to_int(value):

    if value is None:

        return None



    value = str(value).replace(",", "")

    match = re.search(r"\d+(?:\.\d+)?", value)



    if not match:

        return None



    try:

        return int(float(match.group()))

    except (ValueError, OverflowError):

        return None





def to_float(value):

    if value is None:

        return None



    match = re.search(r"\d+(?:\.\d+)?", str(value))



    if not match:

        return None



    return float(match.group())





def get_element_value(element):

    if not element:

        return None



    return (

        element.get("content")

        or element.get("data-price")

        or element.get_text(" ", strip=True)

    )





def extract_price(soup, selectors):

    """Extract a numeric price from the first matching price element."""



    for selector in selectors:

        try:

            elements = soup.select(selector)

        except Exception:

            continue



        for element in elements:

            value = to_int(get_element_value(element))



            if value is not None and value > 0:

                return value



    return None





def extract_discount(soup):

    element = soup.select_one(

        ".ty-product-block__discount, "

        ".bk_price_discount"

    )



    if element:

        return to_int(element.get_text(" ", strip=True))



    return None





def extract_rating(soup):

    selectors = [

        ".pro_rat_scr",

        "[itemprop='ratingValue']",

        "[data-ca-product-review-reviews-stars-rating]",

    ]



    for selector in selectors:

        element = soup.select_one(selector)



        if not element:

            continue



        value = (

            element.get("content")

            or element.get(

                "data-ca-product-review-reviews-stars-rating"

            )

            or element.get_text(" ", strip=True)

        )



        rating = to_float(value)



        if rating is not None:

            return None if rating == 0 else rating



    return None









def is_invalid_image_url(image_url):

    if not image_url:

        return True



    return "chatgpt_image" in str(image_url).lower()





def extract_image(soup, product_url, fallback=None):



    selectors = [

        "meta[property='og:image']",

        ".ty-product-img img",

        ".ty-product-image img",

        "img[itemprop='image']",

        ".bk-product-img-light-layer img",

    ]



    for selector in selectors:



        for element in soup.select(selector):



            image_url = (

                element.get("content")

                or element.get("data-ca-image-path")

                or element.get("data-src")

                or element.get("data-lazy-src")

                or element.get("data-original")

                or element.get("src")

            )



            if not image_url or image_url.startswith("data:"):

                continue



            image_url = urljoin(product_url, image_url)



            if is_invalid_image_url(image_url):

                continue



            return image_url



    if fallback:



        fallback = urljoin(product_url, fallback)



        if not is_invalid_image_url(fallback):

            return fallback



    return None









def clean_description(value):



    if not value:

        return None



    value = BeautifulSoup(

        str(value), "html.parser"

    ).get_text(" ", strip=True)



    # Remove Read More at the end

    value = re.sub(

        r"\s*read\s+more\s*$",

        "",

        value,

        flags=re.IGNORECASE

    )



    # Remove unwanted symbols

    value = value.replace("/", "")

    value = value.replace('"', "")



    value = re.sub(r"\s+", " ", value).strip()



    return value or None





def extract_description(soup):



    full_description_selectors = [

        ".pro_product_tab_23_content .pdp-detail-content .ty-wysiwyg-content",

        ".pro_product_tab_23_content .pdp-detail-content",

        "#content_description",

        ".ty-product-description",

        ".ty-wysiwyg-content",

        ".ty-product-block__description",

        "[itemprop='description']",

        ".product-description",

        ".ty-product-feature",

    ]



    for selector in full_description_selectors:



        elements = soup.select(selector)



        if not elements:

            continue



        parts = []



        for element in elements:



            for unwanted in element.select(

                "script, style, noscript"

            ):

                unwanted.decompose()



            text = element.get_text(" ", strip=True)



            if text:

                parts.append(text)



        description = clean_description(" ".join(parts))



        if description:

            return description



    meta = soup.select_one(

        "meta[property='og:description'], "

        "meta[name='description']"

    )



    if meta:



        description = clean_description(meta.get("content"))



        if description:

            return description



    return None









def extract_product_cards(html, category_url):



    soup = BeautifulSoup(html, "html.parser")

    products = []



    cards = soup.select("div.bk-product-list")



    for card in cards:



        name_element = card.select_one("a.product-title")



        if not name_element:

            continue



        name = clean_text(

            name_element.get("title")

            or name_element.get_text(" ", strip=True)

        )



        product_link = name_element.get("href")



        if not product_link:

            continue



        product_link = urljoin(category_url, product_link)



        if not product_link.startswith(("http://", "https://")):

            continue



        image_element = card.select_one("img.ty-pict")

        image_link = None



        if image_element:



            srcset = image_element.get("srcset")



            if srcset:

                image_link = (

                    srcset.split(",")[0].strip().split()[0]

                )

            else:

                image_link = (

                    image_element.get("data-src")

                    or image_element.get("data-lazy-src")

                    or image_element.get("src")

                )



            if image_link:



                image_link = urljoin(

                    category_url,

                    image_link

                )



                if is_invalid_image_url(image_link):

                    image_link = None



        price = extract_price(

            card,

            ["[id^='sec_discounted_price_']"],

        )



        original_price = extract_price(

            card,

            ["[id^='sec_list_price_']"],

        )



        discount = extract_discount(card)

        ratings = extract_rating(card)



        products.append({

            "name": name,

            "product_link": product_link,

            "image_link": image_link,

            "price": price,

            "original_price": original_price,

            "discount": discount,

            "ratings": ratings,

        })



    return products





def get_next_page(html, current_url):



    soup = BeautifulSoup(html, "html.parser")



    next_link = (

        soup.select_one("a.pagination-next")

        or soup.select_one("a.ty-pagination__next")

        or soup.select_one("a[rel='next']")

    )



    if next_link and next_link.get("href"):

        return urljoin(current_url, next_link["href"])



    return None








def extract_product_details(html, product, category_name):



    soup = BeautifulSoup(html, "html.parser")



    # NAME



    name_element = soup.select_one(

        "h1.ty-product-block__title"

    )



    name = (

        clean_text(name_element.get_text(" ", strip=True))

        if name_element

        else product["name"]

    )



    


    product_block = soup.select_one(".ty-product-block")



    if product_block:



        # CURRENT PRICE



        price = extract_price(

            product_block,

            ["[id^='sec_discounted_price_']"],

        )



    



        original_price = extract_price(

            product_block,

            [

                "[id^='sec_list_price_']",

                ".ty-product-prices .ty-strike",

                ".ty-product-prices .bk-list-price",

                ".ty-product-prices .ty-list-price",

                ".ty-list-price",

                ".bk-list-price",

                ".ty-strike",

                ".old-price",

                ".price-old",

                ".list-price",

                "[class*='old-price']",

                "[class*='list-price']",

                "[class*='strike']",

                "del",

                "s",

            ],

        )






        if (

            original_price is not None

            and price is not None

            and original_price <= price

        ):

            original_price = None



    



        discount = extract_discount(product_block)



    else:



        price = None

        original_price = None

        discount = None



    



    if (

        discount is None

        and price is not None

        and original_price is not None

        and original_price > 0

    ):



        discount = round(

            ((original_price - price) / original_price) * 100

        )



   



    description = extract_description(soup)







    image_link = extract_image(

        soup,

        product["product_link"],

        product.get("image_link"),

    )







    ratings = extract_rating(soup)



    if ratings is None:

        ratings = product.get("ratings")



    if ratings == 0:

        ratings = None



    return {

        "name": name,

        "price": price,

        "currency": "₹",

        "original_price": original_price,

        "discount": discount,

        "ratings": ratings,

        "description": description,

        "image_link": image_link,

        "product_link": urljoin(product["product_link"], ""),

        "organization_id": ORGANIZATION_ID,

        "store_id": STORE_ID,

        "categories_id": category_name,

        "created_at": datetime.now(timezone.utc).isoformat(),

    }









async def scrape_kindlife_async():



    browser_config = BrowserConfig(

        browser_type="chromium",

        headless=True,

        verbose=True,

    )



    run_config = CrawlerRunConfig(

        cache_mode=CacheMode.BYPASS,

        page_timeout=90000,

        delay_before_return_html=2,

    )



    final_products = []

    all_seen_links = set()



    async with AsyncWebCrawler(

        config=browser_config

    ) as crawler:



        for category_name, start_url in CATEGORIES.items():



            print("\n" + "=" * 65)

            print("CATEGORY:", category_name)

            print("URL:", start_url)

            print("=" * 65)



            category_products = []

            visited_pages = set()

            listing_url = start_url



            



            while (

                listing_url

                and listing_url not in visited_pages

                and len(category_products) < PRODUCTS_PER_CATEGORY

            ):



                visited_pages.add(listing_url)



                print("\nLoading listing:", listing_url)



                try:



                    result = await crawler.arun(

                        url=listing_url,

                        config=run_config,

                    )



                    if not result.success:



                        print(

                            "Listing failed:",

                            result.error_message

                        )



                        break



                    html = result.html or result.cleaned_html or ""



                    products = extract_product_cards(

                        html,

                        listing_url,

                    )



                    print("Cards found:", len(products))



                    for product in products:



                        link = product["product_link"]



                        if link in all_seen_links:

                            continue



                        all_seen_links.add(link)



                        product["category_name"] = category_name

                        category_products.append(product)



                        if (

                            len(category_products)

                            >= PRODUCTS_PER_CATEGORY

                        ):

                            break



                    if len(category_products) >= PRODUCTS_PER_CATEGORY:

                        break



                    listing_url = get_next_page(

                        html,

                        listing_url,

                    )



                except Exception as error:



                    print("Listing error:", error)

                    break



            print(

                f"\n{category_name}: "

                f"{len(category_products)}/{PRODUCTS_PER_CATEGORY} "

                "products collected"

            )



          



            for index, product in enumerate(

                category_products,

                start=1,

            ):



                print(

                    f"\n[{index}/{len(category_products)}] "

                    f"{product['name']}"

                )



                try:



                    await asyncio.sleep(PRODUCT_DELAY)



                    print(

                        "Loading product page:",

                        product["product_link"]

                    )



                    try:



                        result = await asyncio.wait_for(

                            crawler.arun(

                                url=product["product_link"],

                                config=run_config,

                            ),

                            timeout=PRODUCT_PAGE_TIMEOUT,

                        )



                    except asyncio.TimeoutError:



                        print(

                            f"TIMEOUT: Product page took over "

                            f"{PRODUCT_PAGE_TIMEOUT} seconds. "

                            "Skipping and continuing."

                        )



                        continue



                    await asyncio.sleep(PAGE_LOAD_DELAY)



                    if not result.success:



                        print(

                            "Product page failed:",

                            result.error_message,

                        )



                        continue



                    html = result.html or result.cleaned_html or ""



                    if not html.strip():



                        print("SKIPPED: empty product page")

                        continue



                    if (

                        not product.get("product_link")

                        or not product["product_link"].startswith(

                            ("http://", "https://")

                        )

                    ):



                        print("SKIPPED: Missing or invalid product link")

                        continue



                    item = extract_product_details(

                        html,

                        product,

                        category_name,

                    )






                    if (

                        item["price"] is None

                        or item["price"] <= 0

                        or item["original_price"] is None

                        or item["original_price"] <= 0

                    ):



                        print(

                            "SKIPPED: price missing | "

                            f"price={item['price']} | "

                            f"original_price={item['original_price']}"

                        )



                        continue



                   

                    if not item.get("name") or not str(item.get("name")).strip():
                        print("SKIPPED: product name missing")
                        continue

                    if item.get("discount") is None:
                        print("SKIPPED: discount missing")
                        continue

                    if not item.get("description"):
                        print("SKIPPED: description missing")
                        continue

                    if not item.get("image_link"):
                        print("SKIPPED: image link missing")
                        continue

                    if (
                        not item.get("product_link")
                        or not item["product_link"].startswith(("http://", "https://"))
                    ):
                        print("SKIPPED: missing or invalid product link")
                        continue

                    final_products.append(item)



                    print(

                        f"SUCCESS | "

                        f"Price: {item['price']} | "

                        f"Original: {item['original_price']} | "

                        f"Discount: {item['discount']}% | "

                        f"Rating: {item['ratings']}"

                    )



                except Exception as error:



                    print(

                        f"PRODUCT ERROR: {product['product_link']} | "

                        f"{error}"

                    )



                    continue



    return final_products




def save_outputs(products):
    with open(JSON_FILE, "w", encoding="utf-8") as file:
        json.dump(products, file, indent=4, ensure_ascii=False)

    with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(products)

    logging.info("JSON generated: %s", JSON_FILE)
    logging.info("CSV generated: %s", CSV_FILE)
    logging.info("Total products saved: %s", len(products))



def run_kindlife_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    logging.info("Kindlife Crawl4AI worker started.")
    logging.info("Python: %s", sys.version)
    logging.info("Platform: %s", sys.platform)

    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except AttributeError:
            pass

    try:
        products = asyncio.run(scrape_kindlife_async())
        logging.info("Kindlife worker finished | Products=%s", len(products))
        return products
    except Exception:
        logging.exception("Kindlife Crawl4AI worker failed.")
        raise




class KindlifeSpider(scrapy.Spider):
    name = "kindlife"
    allowed_domains = ["kindlife.in", "www.kindlife.in"]

    async def start(self):
        self.logger.info("=" * 70)
        self.logger.info("Starting Kindlife Crawl4AI scraper...")
        self.logger.info("=" * 70)

        loop = asyncio.get_running_loop()
        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                products = await loop.run_in_executor(
                    executor,
                    run_kindlife_scraper,
                )
        except Exception as exc:
            self.logger.exception("Kindlife Crawl4AI worker failed: %s", exc)
            return

        self.logger.info("Crawl4AI returned %s products.", len(products))

        valid_products = []
        skipped = 0

        for product in products:
         
            if (
                product.get("price") is None
                or product.get("price") <= 0
                or product.get("original_price") is None
                or product.get("original_price") <= 0
                or product.get("discount") is None
                or not product.get("name")
                or not str(product.get("name", "")).strip()
                or not product.get("description")
                or not product.get("image_link")
                or not product.get("product_link")
                or not product.get("product_link", "").startswith(("http://", "https://"))
            ):
                skipped += 1
                self.logger.warning(
                    "Skipping Kindlife product because mandatory field is missing | name=%r",
                    product.get("name"),
                )
                continue

            valid_products.append(product)

        save_outputs(valid_products)

        for product in valid_products:
            yield product

        self.logger.info("=" * 70)
        self.logger.info("Kindlife spider completed.")
        self.logger.info("Valid products: %s", len(valid_products))
        self.logger.info("Skipped products: %s", skipped)
        self.logger.info("JSON: %s", JSON_FILE)
        self.logger.info("CSV: %s", CSV_FILE)
        self.logger.info("=" * 70)




if __name__ == "__main__":
    data = run_kindlife_scraper()

    valid_products = [
        product for product in data
        if (
            product.get("price") is not None
            and product.get("price") > 0
            and product.get("original_price") is not None
            and product.get("original_price") > 0
            and product.get("discount") is not None
            and product.get("name")
            and str(product.get("name", "")).strip()
            and product.get("description")
            and product.get("image_link")
            and product.get("product_link")
            and product.get("product_link", "").startswith(("http://", "https://"))
        )
    ]

    save_outputs(valid_products)

    print("=" * 70)
    print("KINDLIFE SCRAPING COMPLETED")
    print("Valid products:", len(valid_products))
    print("Skipped products:", len(data) - len(valid_products))
    print("JSON:", JSON_FILE)
    print("CSV:", CSV_FILE)
    print("=" * 70)
