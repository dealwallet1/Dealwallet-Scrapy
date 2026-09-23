import asyncio
import logging
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler


BASE_URLS = {
    "Beauty": [
        "https://worldofasaya.com/collections/all-products",
        "https://worldofasaya.com/collections/hyperpigmentation",
    ],
}

MAX_PAGES = 1
MAX_PRODUCTS_PER_CATEGORY = 100

BASE_DOMAIN = "https://worldofasaya.com"


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    value = value.strip()

    return value if value else None


def parse_price_to_float(price_str):
    if not price_str:
        return None

    clean = re.sub(r"[^\d.]", "", str(price_str))

    if not clean:
        return None

    try:
        value = float(clean)

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def parse_rating_to_float(rating_str):
    if not rating_str:
        return None

    match = re.search(r"\d+(?:\.\d+)?", str(rating_str))

    if not match:
        return None

    try:
        return float(match.group())
    except ValueError:
        return None


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    if original_price <= 0:
        return None

    if original_price <= price:
        return None

    return round(
        ((original_price - price) / original_price) * 100,
    )


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(card):
    name_tag = card.select_one(
        "p.product__grid__title"
    )

    if not name_tag:
        return "N/A"

    name = clean_text(
        name_tag.get_text(" ", strip=True)
    )

    return name or "N/A"


# ============================================================
# PRODUCT LINK
# ============================================================

def extract_product_link(card):
    link_tag = card.select_one(
        'a[data-grid-link][href^="/products/"]'
    )

    if not link_tag:
        return "N/A"

    href = link_tag.get("href")

    if not href:
        return "N/A"

    return urljoin(BASE_DOMAIN, href)


# ============================================================
# PRICE
# ============================================================

def extract_prices(card):
    price_tag = card.select_one(
        "span.custom-discount-price"
    )

    price = None

    if price_tag:
        price = parse_price_to_float(
            price_tag.get_text(" ", strip=True)
        )

    original_price_tag = card.select_one(
        "span.custom-original-price"
    )

    original_price = None

    if original_price_tag:
        original_price = parse_price_to_float(
            original_price_tag.get_text(" ", strip=True)
        )

    return price, original_price


# ============================================================
# IMAGE
# ============================================================

def extract_image(card):
    img_tag = card.select_one(
        "img.collection-product-image.primary-image"
    )

    if not img_tag:
        return "N/A"

    image_url = (
        img_tag.get("src")
        or img_tag.get("data-src")
        or img_tag.get("data-lazy-src")
    )

    if not image_url:
        srcset = img_tag.get("srcset")

        if srcset:
            image_url = (
                srcset.split(",")[0]
                .strip()
                .split(" ")[0]
            )

    if not image_url:
        return "N/A"

    if image_url.startswith("data:"):
        return "N/A"

    return urljoin(BASE_DOMAIN, image_url)


# ============================================================
# RATING
# ============================================================

def extract_rating(card):
    rating_tag = card.select_one(
        ".jdgm-prev-badge__stars[data-score]"
    )

    if not rating_tag:
        return None

    rating = rating_tag.get("data-score")

    return parse_rating_to_float(rating)


# ============================================================
# DESCRIPTION
# ============================================================

def extract_description(card):
    description_tag = card.select_one(
        "span.small-discription"
    )

    if not description_tag:
        return "N/A"

    description = clean_text(
        description_tag.get_text(" ", strip=True)
    )

    return description or "N/A"


# ============================================================
# ASYNC SCRAPER
# ============================================================

async def scrape_world_of_asaya_async():
    results = []
    seen_links = set()
    category_counts = {}

    async with AsyncWebCrawler(verbose=True) as crawler:

        for category, urls in BASE_URLS.items():

            logging.info("")
            logging.info("=" * 70)
            logging.info(f"SCRAPING CATEGORY: {category}")
            logging.info("=" * 70)

            category_results = []

            for base_url in urls:

                page_num = 1

                while page_num <= MAX_PAGES:

                    if page_num == 1:
                        url = base_url
                    else:
                        url = f"{base_url}?page={page_num}"

                    logging.info("")
                    logging.info(f"[PAGE] {url}")

                    try:
                        page = await crawler.arun(url=url)

                    except Exception as e:
                        logging.error(f"[CRAWL ERROR] {e}")
                        break

                    if not page.success:
                        logging.error(f"[FAILED] {url}")

                        if getattr(page, "error_message", None):
                            logging.error(page.error_message)

                        break

                    if not page.html:
                        logging.error("[ERROR] Empty HTML")
                        break

                    soup = BeautifulSoup(
                        page.html,
                        "html.parser",
                    )

                    product_cards = soup.select(
                        "product-grid-item"
                    )

                    logging.info(
                        f"[PRODUCT CARDS FOUND] "
                        f"{len(product_cards)}"
                    )

                    if not product_cards:
                        logging.info("[NO PRODUCTS FOUND]")
                        break

                    page_product_count = 0

                    for index, card in enumerate(
                        product_cards[:MAX_PRODUCTS_PER_CATEGORY],
                        start=1,
                    ):

                        try:
                            logging.info("")
                            logging.info(
                                f"--- Product "
                                f"{index}/{len(product_cards)} ---"
                            )

                            # --------------------------------------
                            # NAME
                            # --------------------------------------

                            name = extract_product_name(card)

                            if not name or name == "N/A":
                                logging.info("   SKIPPED - name missing")
                                continue

                            # --------------------------------------
                            # LINK
                            # --------------------------------------

                            product_link = extract_product_link(card)

                            # --------------------------------------
                            # PRICE
                            # --------------------------------------

                            price, original_price = extract_prices(card)

                            logging.info(f"   Name           : {name}")
                            logging.info(f"   Price          : {price}")
                            logging.info(
                                f"   Original Price : "
                                f"{original_price}"
                            )

                            # --------------------------------------
                            # DISCOUNT VALIDATION
                            # --------------------------------------

                            if original_price is None:
                                logging.info("   SKIPPED - no discount")
                                continue

                            if price is None:
                                logging.info("   SKIPPED - price missing")
                                continue

                            if original_price <= price:
                                logging.info("   SKIPPED - no valid discount")
                                continue

                            # --------------------------------------
                            # DISCOUNT
                            # --------------------------------------

                            discount = calculate_discount(
                                price,
                                original_price,
                            )

                            if discount is None or discount <= 0:
                                logging.info("   SKIPPED - invalid discount")
                                continue

                            logging.info(
                                f"   Discount       : "
                                f"{discount}%"
                            )

                            # --------------------------------------
                            # IMAGE
                            # --------------------------------------

                            image_link = extract_image(card)

                            # --------------------------------------
                            # RATING
                            # --------------------------------------

                            rating = extract_rating(card)

                            # --------------------------------------
                            # DESCRIPTION
                            # --------------------------------------

                            description = extract_description(card)

                            # --------------------------------------
                            # DUPLICATE CHECK
                            # --------------------------------------

                            if (
                                product_link != "N/A"
                                and product_link in seen_links
                            ):
                                logging.info("   SKIPPED - duplicate")
                                continue

                            if product_link != "N/A":
                                seen_links.add(product_link)

                            # --------------------------------------
                            # PRODUCT DATA
                            # --------------------------------------

                            product_data = {
                                "name": name,
                                "price": price,
                                "currency": "₹",
                                "original_price": original_price,
                                "discount": discount,
                                "ratings": rating,
                                "description": description,
                                "image_link": image_link,
                                "product_link": product_link,
                                "organization_id": "Dealwallet",
                                "store_id": "World of Asaya",
                                "categories_id": category,
                            }

                            results.append(product_data)
                            category_results.append(product_data)

                            page_product_count += 1

                            logging.info(f"   Scraped: {name}")
                            logging.info(f"   Price: ₹{price}")
                            logging.info(
                                f"   Original: "
                                f"₹{original_price}"
                            )
                            logging.info(f"   Discount: {discount}%")
                            logging.info(f"   Rating: {rating}")
                            logging.info(
                                f"   Description: "
                                f"{description}"
                            )

                        except Exception as e:
                            print(
                                f"[PRODUCT ERROR] {e}"
                            )

                    logging.info("")
                    logging.info(
                        f"[PAGE PRODUCTS ADDED] "
                        f"{page_product_count}"
                    )

                    page_num += 1

            category_counts[category] = len(category_results)

            logging.info("")
            logging.info(
                f"Total products scraped for "
                f"'{category}': "
                f"{len(category_results)}"
            )

    logging.info("")
    logging.info("=" * 70)
    print("WORLD OF ASAYA SCRAPING COMPLETED")
    logging.info("=" * 70)

    logging.info(
        f"GRAND TOTAL PRODUCTS: "
        f"{len(results)}"
    )

    logging.info("")
    logging.info("Products scraped per category:")

    for category, count in category_counts.items():
        logging.info(
            f" - {category}: "
            f"{count} products"
        )

    logging.info("=" * 70)

    return results


# ============================================================
# WINDOWS PROCESS WORKER
# ============================================================

def run_world_of_asaya_in_process():
    # Configure logging inside the separate browser
    # process so all scraping progress is visible
    # in the Scrapyd job log.
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )

    # Keep HTTP connection/debug information visible like the
    # existing DealWallet browser-based spiders.
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    logging.getLogger("dealwallet_scraper").setLevel(logging.DEBUG)

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)
    logging.info("World of Asaya browser process started.")
    logging.info("=" * 60)

    try:
        results = asyncio.run(
            scrape_world_of_asaya_async()
        )

        logging.info(
            "World of Asaya browser process finished. "
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:
        logging.exception(
            "World of Asaya browser process failed."
        )
        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class WorldOfAsayaSpider(scrapy.Spider):
    name = "world_of_asaya"

    allowed_domains = [
        "worldofasaya.com",
        "www.worldofasaya.com",
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 1,
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    async def start(self):

        self.logger.info("=" * 60)
        self.logger.info(
            "Starting World of Asaya Playwright/Crawl4AI scraper..."
        )
        self.logger.info("=" * 60)

        # ----------------------------------------------------
        # Run browser scraper in separate process
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_world_of_asaya_in_process,
            )

        # ----------------------------------------------------
        # SAVE RAW SCRAPED DATA
        # ----------------------------------------------------

        with open(
            "scrape_world_of_asaya.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                products,
                f,
                ensure_ascii=False,
                indent=2,
            )

        self.logger.info(
            "World of Asaya scraper returned "
            "%s products.",
            len(products),
        )

        # ----------------------------------------------------
        # YIELD TO SCRAPY
        # ----------------------------------------------------

        for product in products:

            # Final validation.
            if not product.get("image_link"):
                continue

            if not product.get("product_link"):
                continue

            yield product

        self.logger.info("=" * 60)
        self.logger.info("World of Asaya spider completed.")
        self.logger.info("=" * 60)
