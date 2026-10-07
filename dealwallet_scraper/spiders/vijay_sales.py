import asyncio
import json
import logging
import re
import hashlib
import sys

from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin

import scrapy
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CATEGORY URLS
# ============================================================

BASE_URLS = {
    # "Mobiles": [
    #     "https://www.vijaysales.com/c/smartphones?brand=Redmi",

    #     "https://www.vijaysales.com/c/smartphones",
    #     "https://www.vijaysales.com/c/smartphones?brand=OnePlus",
    #     "https://www.vijaysales.com/c/smartphones?brand=Nothing",
    #     "https://www.vijaysales.com/c/smartphones?brand=Samsung",
    #     "https://www.vijaysales.com/c/smartphones?brand=realme",
    #     "https://www.vijaysales.com/c/smartphones?brand=Oppo",
    #     "https://www.vijaysales.com/c/smartphones?brand=Google",
    # ],

    # "Laptops": [
    #     "https://www.vijaysales.com/c/laptops",
    #     "https://www.vijaysales.com/c/laptops?brand=Apple",
    #     "https://www.vijaysales.com/c/laptops?brand=Redmi",
    #     "https://www.vijaysales.com/c/laptops?brand=HP",
    #     "https://www.vijaysales.com/c/laptops?brand=Lenovo",
    #     "https://www.vijaysales.com/c/laptops?brand=ASUS",
    #     "https://www.vijaysales.com/c/laptops?brand=Acer",
    #     "https://www.vijaysales.com/c/laptops?brand=Dell",
    #     "https://www.vijaysales.com/c/laptops?brand=MSI",
    # ],

    "Beauty": [
        "https://www.vijaysales.com/c/personal-care",
        "https://www.vijaysales.com/c/personal-care?categories=Trimmers",
        "https://www.vijaysales.com/c/personal-care?categories=Hair%20Dryers",
        "https://www.vijaysales.com/c/personal-care?categories=Hair%20Starighteners",
        "https://www.vijaysales.com/c/personal-care?categories=Hair%20Stylers",
        "https://www.vijaysales.com/c/personal-care?categories=Shavers",
        "https://www.vijaysales.com/c/personal-care?categories=Massager",
        "https://www.vijaysales.com/c/personal-care?categories=Epilators",
        "https://www.vijaysales.com/c/personal-care?categories=Callus%20Remover",
    ],

    "electronics": [
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Large%20Audio",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Audio%20Accessories",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Playstation",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Televisions",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Projectors",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=TV%20Accessories",
        "https://www.vijaysales.com/c/television-and-entertainment?categories=Musical%20Instruments",
        "https://www.vijaysales.com/c/headphones-and-speakers?categories=Speakers",
        "https://www.vijaysales.com/c/headphones-and-speakers?categories=Headphones%20and%20Headsets",
    ],

    "computer accesories": [
        "https://www.vijaysales.com/c/laptops-and-accessories?categories=Computer%20Accessories",
        "https://www.vijaysales.com/c/laptops-and-accessories?categories=Printing",
        "https://www.vijaysales.com/c/laptops-and-accessories?categories=Desktops",
        "https://www.vijaysales.com/c/laptops-and-accessories?categories=Storage%20Devices",
        "https://www.vijaysales.com/c/laptops-and-accessories?categories=Networking",
    ],

    "home appliences": [
        "https://www.vijaysales.com/c/home-appliances?categories=Vacuum%20Cleaners",
        "https://www.vijaysales.com/c/home-appliances?categories=Iron",
        "https://www.vijaysales.com/c/home-appliances?categories=Water%20Heater",
        "https://www.vijaysales.com/c/home-appliances?categories=Air%20Conditioners",
        "https://www.vijaysales.com/c/home-appliances?categories=Refrigerators",
        "https://www.vijaysales.com/c/home-appliances?categories=Inverters%20and%20Stabilizer",
        "https://www.vijaysales.com/c/home-appliances?categories=Fans",
    ],

    "mobile accesories": [
        "https://www.vijaysales.com/c/adapters",
        "https://www.vijaysales.com/c/power-bank",
        "https://www.vijaysales.com/c/cables",
        "https://www.vijaysales.com/c/cases-and-covers",
        "https://www.vijaysales.com/c/mobiles-tablets-and-accessories?categories=Mobile%20Accessories",
        "https://www.vijaysales.com/c/mobiles-tablets-and-accessories?categories=Mobile%20Accessories&brand=boAt",
    ],

    "gaming": [
        "https://www.vijaysales.com/c/gaming?categories=Gaming%20Controllers",
        "https://www.vijaysales.com/c/gaming?categories=Games",
        "https://www.vijaysales.com/c/gaming?categories=Gaming%20Consoles",
        "https://www.vijaysales.com/c/gaming?categories=Gaming%20Accessories",
        "https://www.vijaysales.com/c/gaming?categories=Gift%20Cards",
    ],
}


# ============================================================
# CATEGORY LABELS
# ============================================================

CATEGORY_LABELS = {
    "Mobiles": "Mobile",
    "Laptops": "Laptops",
    "Beauty": "Beauty",
    "computer accesories": "Computers Gadgets",
    "home appliences": "Home Appliances",
    "gaming": "Toys Games",
    "mobile accesories": "Mobile Accessories",
    "electronics": "Electronics",
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_to_int(text):
    if not text:
        return 0

    txt = (
        str(text)
        .replace("₹", "")
        .replace(",", "")
        .strip()
    )

    match = re.search(r"(\d+)", txt)

    return int(match.group(1)) if match else 0


def discount_to_int(value):
    if not value:
        return None

    match = re.search(r"(\d+)", str(value))

    return int(match.group(1)) if match else None


def parse_rating_to_float(rating_str: str):
    if not rating_str:
        return None

    match = re.search(
        r"\d+(\.\d+)?",
        str(rating_str)
    )

    return float(match.group()) if match else None


# ============================================================
# PRODUCT DETAIL PAGE
# ============================================================

async def fetch_product_details(context, product_url):
    data = {
        "description": ""
    }

    try:
        page = await context.new_page()

        await page.goto(
            product_url,
            wait_until="domcontentloaded",
            timeout=10000,
        )

        await page.wait_for_timeout(1200)

        html = await page.content()

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        features = []

        features_ul = soup.select_one(
            "ul.product__keyfeatures--list"
        )

        if features_ul:
            for li in features_ul.select("li"):
                text = li.get_text(
                    " ",
                    strip=True,
                )

                if text:
                    features.append(text)

        data["description"] = (
            ", ".join(features)
            if features
            else "N/A"
        )

        await page.close()

        return data

    except Exception:
        return data


# ============================================================
# VIJAY SALES ASYNC SCRAPER
# ============================================================

async def VijaySales_Scraper(max_products=2000):

    unique_products = {}

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            viewport={
                "width": 1400,
                "height": 900,
            },
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "Chrome/120 Safari/537.36"
            ),
        )

        page = await context.new_page()

        try:

            for category_id, urls in BASE_URLS.items():

                for base_url in urls:

                    for page_no in range(1, 2):

                        if "?" in base_url:
                            url = (
                                f"{base_url}"
                                f"&page={page_no}"
                            )
                        else:
                            url = (
                                f"{base_url}"
                                f"?page={page_no}"
                            )

                        print(
                            f"\n[INFO] Opening: {url}"
                        )

                        try:

                            await page.goto(
                                url,
                                wait_until="domcontentloaded",
                                timeout=90000,
                            )

                            await page.wait_for_timeout(
                                3000
                            )

                        except Exception as exc:

                            print(
                                f"[ERROR] Failed to open URL: "
                                f"{url}"
                            )

                            print(
                                f"[ERROR] {exc}"
                            )

                            continue

                        html = await page.content()

                        soup = BeautifulSoup(
                            html,
                            "html.parser",
                        )

                        product_divs = soup.select(
                            "div.product-card"
                        )

                        print(
                            f"[INFO] Product cards found: "
                            f"{len(product_divs)}"
                        )

                        if not product_divs:

                            print(
                                "[END] No more products found"
                            )

                            break

                        for div in product_divs:

                            if (
                                len(unique_products)
                                >= max_products
                            ):
                                break

                            try:

                                # --------------------------------
                                # PRODUCT NAME
                                # --------------------------------

                                title_tag = div.select_one(
                                    "div.product-card__title > "
                                    "div.product-name"
                                )

                                name = (
                                    title_tag.get_text(
                                        strip=True
                                    )
                                    if title_tag
                                    else ""
                                )

                                # --------------------------------
                                # PRICE
                                # --------------------------------

                                price_tag = div.select_one(
                                    "div.discountedPrice "
                                    "span:nth-of-type(2)"
                                )

                                # --------------------------------
                                # ORIGINAL PRICE / MRP
                                # --------------------------------

                                original_price_tag = (
                                    div.select_one(
                                        "div.originalPrice "
                                        "span:nth-of-type(2)"
                                    )
                                )

                                # --------------------------------
                                # DISCOUNT
                                # --------------------------------

                                discount_tag = div.select_one(
                                    "div.discountPercentage"
                                )

                                # --------------------------------
                                # RATING
                                # --------------------------------

                                rating_tag = div.select_one(
                                    "div.product__title--reviews-star"
                                )

                                # --------------------------------
                                # IMAGE
                                # --------------------------------

                                img_tag = div.select_one(
                                    "img.product__image"
                                )

                                # --------------------------------
                                # PRODUCT LINK
                                # --------------------------------

                                link_tag = div.select_one(
                                    "a.product-card__link"
                                )

                                if not link_tag:
                                    continue

                                href = (
                                    link_tag.get("href")
                                    or ""
                                ).strip()

                                product_link = urljoin(
                                    "https://www.vijaysales.com",
                                    href,
                                )

                                if not product_link:
                                    continue

                                # --------------------------------
                                # PRODUCT ID
                                # --------------------------------

                                product_id = hashlib.md5(
                                    product_link.encode(
                                        "utf-8"
                                    )
                                ).hexdigest()

                                if product_id in unique_products:
                                    continue

                                # --------------------------------
                                # PRICE PARSING
                                # --------------------------------

                                price = clean_to_int(
                                    price_tag.get_text()
                                    if price_tag
                                    else ""
                                )

                                # --------------------------------
                                # ORIGINAL PRICE
                                # --------------------------------

                                original_price = clean_to_int(
                                    original_price_tag.get_text()
                                    if original_price_tag
                                    else ""
                                )

                                # --------------------------------
                                # DISCOUNT
                                # --------------------------------

                                discount = discount_to_int(
                                    discount_tag.get_text()
                                    if discount_tag
                                    else ""
                                )

                                # --------------------------------
                                # RATING
                                # --------------------------------

                                rating_style = (
                                    rating_tag.get(
                                        "style",
                                        ""
                                    )
                                    if rating_tag
                                    else ""
                                )

                                match = re.search(
                                    r"--rating:\s*([\d.]+)",
                                    rating_style,
                                )

                                rating = (
                                    parse_rating_to_float(
                                        match.group(1)
                                    )
                                    if match
                                    else None
                                )

                                # --------------------------------
                                # IMAGE URL
                                # --------------------------------

                                image_link = ""

                                if img_tag:

                                    image_link = (
                                        img_tag.get("src")
                                        or ""
                                    ).strip()

                                    # Some websites lazy-load
                                    # images into data-src.
                                    if (
                                        not image_link
                                        or image_link.startswith(
                                            "data:"
                                        )
                                    ):
                                        image_link = (
                                            img_tag.get(
                                                "data-src"
                                            )
                                            or ""
                                        ).strip()

                                # --------------------------------
                                # BASIC VALIDATION
                                # --------------------------------

                                if (
                                    not name
                                    or price <= 0
                                    or not product_link
                                    or not image_link
                                ):
                                    print(
                                        "[SKIP] Missing "
                                        "required product data"
                                    )
                                    continue

                                # --------------------------------
                                # PRICE VALIDATION
                                # --------------------------------

                                if (
                                    original_price <= 0
                                    or price >= original_price
                                ):
                                    print(
                                        f"[SKIP] Invalid price "
                                        f"comparison: {name}"
                                    )
                                    continue

                                # --------------------------------
                                # PRODUCT DETAIL PAGE
                                # --------------------------------

                                details = (
                                    await fetch_product_details(
                                        context,
                                        product_link,
                                    )
                                )

                                description = details.get(
                                    "description",
                                    "N/A",
                                )

                                if (
                                    not discount
                                    or description == "N/A"
                                ):
                                    print(
                                        f"[SKIP] Missing "
                                        f"discount/description: "
                                        f"{name}"
                                    )
                                    continue

                                # --------------------------------
                                # CATEGORY
                                # --------------------------------

                                category_label = (
                                    CATEGORY_LABELS.get(
                                        category_id,
                                        category_id,
                                    )
                                )

                                if rating == 0.0:
                                    rating = None

                                # --------------------------------
                                # PRODUCT OBJECT
                                # --------------------------------
                                timestamp = datetime.now(
                                                                timezone(
                                                                    timedelta(
                                                                        hours=5,
                                                                        minutes=30
                                                                    )
                                                                )
                                                            ).strftime(
                                                                "%Y-%m-%dT%H:%M:%S"
                                                            )

                                unique_products[
                                    product_id
                                ] = {
                                    "name": name,
                                    "price": price,
                                    "original_price": (
                                        original_price
                                    ),
                                    "discount": discount,
                                    "currency": "₹",
                                    "ratings": rating,
                                    "description": (
                                        description
                                    ),
                                    "image_link": (
                                        image_link
                                    ),
                                    "product_link": (
                                        product_link
                                    ),
                                    "organization_id": (
                                        "Dealwallet"
                                    ),
                                    "store_id": (
                                        "Vijay Sales"
                                    ),
                                    "categories_id": (
                                        category_label
                                    ),
                                     "created_at": timestamp,
                                }

                                print(
                                    f"[OK] {name} | "
                                    f"₹{price} | "
                                    f"{discount}% OFF"
                                )

                            except Exception as exc:

                                print(
                                    "[SKIP]",
                                    exc,
                                )

                                continue

                        if (
                            len(unique_products)
                            >= max_products
                        ):
                            break

        finally:

            await page.close()
            await context.close()
            await browser.close()

    return list(
        unique_products.values()
    )


# ============================================================
# WINDOWS / SCRAPYD PROCESS RUNNER
# ============================================================

def run_vijay_sales_scraper():
    """
    Run the async Playwright scraper inside a
    separate process.

    This follows the same scheduler/process pattern
    used by the SoulFlower spider.
    """

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "[%(levelname)s] "
            "%(message)s"
        ),
        stream=sys.stdout,
        force=True,
    )

    # --------------------------------------------
    # Windows Playwright event loop
    # --------------------------------------------

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)

    logging.info(
        "Vijay Sales browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            VijaySales_Scraper(
                max_products=2000
            )
        )

        logging.info(
            "Vijay Sales browser process finished. "
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "Vijay Sales browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class VijaySalesSpider(scrapy.Spider):

    name = "vijay_sales"

    allowed_domains = [
        "vijaysales.com",
        "www.vijaysales.com",
    ]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
    }

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Vijay Sales Playwright scraper..."
        )

        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = (
                await loop.run_in_executor(
                    executor,
                    run_vijay_sales_scraper,
                )
            )

        # --------------------------------------------
        # Save raw JSON
        # --------------------------------------------

        output_file = (
            "vijaysales_discounted_products.json"
        )

        with open(
            output_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=4,
            )

        self.logger.info(
            "Vijay Sales scraper returned %s products.",
            len(products),
        )

        self.logger.info(
            "Raw output saved to: %s",
            output_file,
        )

        # --------------------------------------------
        # Yield products to Scrapy pipeline
        # --------------------------------------------

        pipeline_count = 0
        skipped_count = 0

        for product in products:

            if (
                not product.get("image_link")
                or not product.get("product_link")
                or not product.get("price")
                or not product.get("original_price")
            ):

                skipped_count += 1

                continue

            pipeline_count += 1

            yield product

        # --------------------------------------------
        # Final logging
        # --------------------------------------------

        self.logger.info("=" * 60)

        self.logger.info(
            "Vijay Sales spider completed."
        )

        self.logger.info(
            "Products scraped : %s",
            len(products),
        )

        self.logger.info(
            "Products yielded : %s",
            pipeline_count,
        )

        self.logger.info(
            "Products skipped : %s",
            skipped_count,
        )

        self.logger.info(
            "Output JSON      : %s",
            output_file,
        )

        self.logger.info("=" * 60)


# ============================================================
# STANDALONE MAIN
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "[%(levelname)s] "
            "%(message)s"
        ),
        stream=sys.stdout,
        force=True,
    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    products = run_vijay_sales_scraper()

    with open(
        "vijaysales_discounted_products.json",
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            products,
            file,
            indent=4,
            ensure_ascii=False,
        )

    logging.info(
        "Created "
        "'vijaysales_discounted_products.json' "
        "with %s products.",
        len(products),
    )
