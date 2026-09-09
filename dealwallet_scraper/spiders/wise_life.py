import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://wiselife.in"

MAX_PRODUCTS_PER_URL = 25

START_URLS = [
    (
        "https://wiselife.in/collections/yoga-props?filter.v.availability=1",
        "Sports",
    ),
    (
        "https://wiselife.in/collections/home-fitness?filter.v.availability=1",
        "Sports",
    ),
    (
        "https://wiselife.in/collections/travel-lifestyle?filter.v.availability=1",
        "Others",
    ),
    (
        "https://wiselife.in/collections/all-yoga-mats-1?filter.v.availability=1",
        "Sports",
    ),
    (
        "https://wiselife.in/collections/apparel?filter.v.availability=1",
        "Fashion & Lifestyle",
    ),
]


# ============================================================
# RATE LIMIT SETTINGS
# ============================================================

LISTING_PAGE_DELAY = 5
DETAIL_PAGE_DELAY = 3

RATE_LIMIT_DELAY = 30
MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000


# ============================================================
# HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(text),
    ).strip()


def extract_price(text):
    if not text:
        return None

    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        str(text).replace(",", ""),
    )

    if not numbers:
        return None

    try:
        value = float(numbers[0])

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def extract_discount(text):
    if not text:
        return None

    discount = clean_text(text)

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%?",
        discount,
    )

    if not match:
        return None

    try:
        value = float(match.group(1))
        return int(value) if value.is_integer() else value
    except (TypeError, ValueError):
        return None


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    try:
        price = float(price)
        original_price = float(original_price)

        if original_price > price > 0:

            percent = round(
                (
                    (original_price - price)
                    / original_price
                )
                * 100
            )

            return percent

    except (TypeError, ValueError):
        pass

    return None


def normalize_product_name(name):
    if not name:
        return ""

    return clean_text(name)


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

def extract_product_description(product_html):

    if not product_html:
        return None

    soup = BeautifulSoup(
        product_html,
        "html.parser",
    )

    # --------------------------------------------------------
    # METHOD 1 - WISELIFE DESCRIPTION
    # --------------------------------------------------------

    description_selectors = [
        "div.accordion-content div.metafield-rich_text_field",
        ".product__description",
        ".product-description",
        ".product__info-description",
        ".product-single__description",
        "[class*='product-description']",
    ]

    for selector in description_selectors:

        description_tag = soup.select_one(
            selector
        )

        if not description_tag:
            continue

        for tag in description_tag.select(
            "script, style, svg, img, button"
        ):
            tag.decompose()

        description = clean_text(
            description_tag.get_text(
                " ",
                strip=True,
            )
        )

        if description:

            description = re.sub(
                r"^\s*description\s*:\s*",
                "",
                description,
                count=1,
                flags=re.IGNORECASE,
            )

            return description.strip()

    # --------------------------------------------------------
    # METHOD 2 - META DESCRIPTION
    # --------------------------------------------------------

    meta_tag = soup.select_one(
        'meta[name="description"]'
    )

    if meta_tag:

        description = clean_text(
            meta_tag.get("content")
        )

        if description:

            description = re.sub(
                r"^\s*description\s*:\s*",
                "",
                description,
                count=1,
                flags=re.IGNORECASE,
            )

            return description.strip()

    # --------------------------------------------------------
    # METHOD 3 - OG DESCRIPTION
    # --------------------------------------------------------

    og_tag = soup.select_one(
        'meta[property="og:description"]'
    )

    if og_tag:

        description = clean_text(
            og_tag.get("content")
        )

        if description:

            description = re.sub(
                r"^\s*description\s*:\s*",
                "",
                description,
                count=1,
                flags=re.IGNORECASE,
            )

            return description.strip()

    # --------------------------------------------------------
    # METHOD 4 - JSON-LD
    # --------------------------------------------------------

    scripts = soup.select(
        'script[type="application/ld+json"]'
    )

    for script in scripts:

        raw_json = script.string

        if not raw_json:
            continue

        try:
            data = json.loads(raw_json)

        except Exception:
            continue

        objects = []

        if isinstance(data, dict):

            objects.append(data)

            if isinstance(
                data.get("@graph"),
                list,
            ):
                objects.extend(
                    data["@graph"]
                )

        elif isinstance(data, list):

            objects.extend(data)

        for item in objects:

            if not isinstance(item, dict):
                continue

            description = item.get(
                "description"
            )

            if not description:
                continue

            description = clean_text(
                description
            )

            description = re.sub(
                r"^\s*description\s*:\s*",
                "",
                description,
                count=1,
                flags=re.IGNORECASE,
            )

            if description:
                return description.strip()

    return None


# ============================================================
# OPEN LISTING PAGE
# ============================================================

async def open_listing_page(
    crawler,
    url,
):

    for attempt in range(
        1,
        MAX_429_RETRIES + 1,
    ):

        try:

            logging.info(
                "Opening WiseLife listing page "
                "(attempt %s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                url,
            )

            result = await crawler.arun(
                url=url,
            )

            if result.success:

                return result

            logging.warning(
                "WiseLife listing request failed "
                "(attempt %s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                url,
            )

        except Exception as exc:

            logging.warning(
                "Failed to open WiseLife listing page "
                "%s: %s",
                url,
                exc,
            )

        if attempt < MAX_429_RETRIES:

            logging.info(
                "Waiting %s seconds before retry.",
                RATE_LIMIT_DELAY,
            )

            await asyncio.sleep(
                RATE_LIMIT_DELAY
            )

    logging.error(
        "Giving up WiseLife listing page: %s",
        url,
    )

    return None


# ============================================================
# OPEN PRODUCT DETAIL PAGE
# ============================================================

async def open_product_page(
    crawler,
    product_link,
):

    for attempt in range(
        1,
        MAX_429_RETRIES + 1,
    ):

        try:

            logging.info(
                "Opening WiseLife product detail "
                "(attempt %s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                product_link,
            )

            result = await crawler.arun(
                url=product_link,
            )

            if result.success:

                return result

            logging.warning(
                "WiseLife product request failed "
                "(attempt %s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                product_link,
            )

        except Exception as exc:

            logging.warning(
                "Failed WiseLife product page "
                "%s: %s",
                product_link,
                exc,
            )

        if attempt < MAX_429_RETRIES:

            logging.info(
                "Waiting %s seconds before retry.",
                RATE_LIMIT_DELAY,
            )

            await asyncio.sleep(
                RATE_LIMIT_DELAY
            )

    logging.error(
        "Giving up WiseLife product page: %s",
        product_link,
    )

    return None


# ============================================================
# CRAWL WISELIFE
# ============================================================

async def scrape_wiselife_products():

    products = []

    seen_names = set()
    seen_links = set()

    category_counts = {}

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

        # ----------------------------------------------------
        # BROWSER CONFIGURATION
        # ----------------------------------------------------

        crawler.browser_config = {
            "headless": True,
            "javascript": True,
        }

        crawler.crawler_run_config = {
            "wait_until": "networkidle",
            "timeout": PAGE_TIMEOUT,
        }

        # ----------------------------------------------------
        # COLLECTION LOOP
        # ----------------------------------------------------

        for category_url, category in START_URLS:

            category_count = 0

            logging.info("=" * 60)

            logging.info(
                "Processing WiseLife category: %s",
                category,
            )

            logging.info(
                "Collection URL: %s",
                category_url,
            )

            # ------------------------------------------------
            # DELAY BETWEEN COLLECTION REQUESTS
            # ------------------------------------------------

            if products:

                logging.info(
                    "Waiting %s seconds before "
                    "next collection request.",
                    LISTING_PAGE_DELAY,
                )

                await asyncio.sleep(
                    LISTING_PAGE_DELAY
                )

            # ------------------------------------------------
            # OPEN COLLECTION
            # ------------------------------------------------

            result = await open_listing_page(
                crawler,
                category_url,
            )

            if result is None:

                logging.warning(
                    "Unable to fetch WiseLife "
                    "collection: %s",
                    category_url,
                )

                continue

            soup = BeautifulSoup(
                result.html,
                "html.parser",
            )

            cards = soup.select(
                "li.grid__item"
            )

            logging.info(
                "Found %s product cards.",
                len(cards),
            )

            # ------------------------------------------------
            # PRODUCT LOOP
            # ------------------------------------------------

            for index, card in enumerate(
                cards,
                start=1,
            ):

                if category_count >= MAX_PRODUCTS_PER_URL:
                    break

                try:

                    # ========================================
                    # PRODUCT NAME
                    # ========================================

                    name_el = card.select_one(
                        "p.wise-card-title"
                    )

                    if not name_el:

                        name_el = card.select_one(
                            "[class*='card-title']"
                        )

                    name = normalize_product_name(
                        name_el.get_text(
                            " ",
                            strip=True,
                        )
                        if name_el
                        else ""
                    )

                    if not name:

                        logging.warning(
                            "Product name not found "
                            "for card #%s.",
                            index,
                        )

                        continue

                    name_key = name.lower()

                    if name_key in seen_names:

                        continue

                    # ========================================
                    # PRODUCT LINK
                    # ========================================

                    link_el = card.select_one(
                        "a.product_title_link"
                    )

                    if not link_el:

                        link_el = card.select_one(
                            "a[href*='/products/']"
                        )

                    href = (
                        link_el.get("href")
                        if link_el
                        else ""
                    )

                    if not href:

                        logging.warning(
                            "Product link not found: %s",
                            name,
                        )

                        continue

                    product_link = urljoin(
                        BASE_URL,
                        href,
                    )

                    if product_link in seen_links:

                        continue

                    # ========================================
                    # IMAGE
                    # ========================================

                    img_el = card.select_one(
                        "img.product_image"
                    )

                    if not img_el:

                        img_el = card.select_one(
                            "img"
                        )

                    image_link = (
                        img_el.get("src")
                        if img_el
                        else ""
                    )

                    if image_link:

                        image_link = image_link.strip()

                        if image_link.startswith("//"):

                            image_link = (
                                "https:"
                                + image_link
                            )

                        elif image_link.startswith("/"):

                            image_link = urljoin(
                                BASE_URL,
                                image_link,
                            )

                    if not image_link:

                        logging.info(
                            "Skipping %s because "
                            "image was not found.",
                            name,
                        )

                        continue

                    # ========================================
                    # RATING
                    # ========================================

                    rating_el = card.select_one(
                        "span.wise-rating-badge"
                    )

                    ratings = (
                        clean_text(
                            rating_el.get_text(
                                " ",
                                strip=True,
                            )
                        ).replace("★", "")
                        if rating_el
                        else None
                    )

                    if ratings:
                        ratings = clean_text(ratings)

                    # ========================================
                    # DETAIL PAGE
                    # ========================================

                    logging.info(
                        "Waiting %s seconds before "
                        "opening product detail: %s",
                        DETAIL_PAGE_DELAY,
                        name,
                    )

                    await asyncio.sleep(
                        DETAIL_PAGE_DELAY
                    )

                    detail_result = (
                        await open_product_page(
                            crawler,
                            product_link,
                        )
                    )

                    description = None
                    price = None
                    original_price = None
                    discount = None

                    if detail_result:

                        # ====================================
                        # DETAIL HTML
                        # ====================================

                        detail_soup = BeautifulSoup(
                            detail_result.html,
                            "html.parser",
                        )

                        # ====================================
                        # DESCRIPTION
                        # ====================================

                        description = (
                            extract_product_description(
                                detail_result.html
                            )
                        )

                        # ====================================
                        # SALE PRICE
                        # ====================================

                        sale_el = (
                            detail_soup.select_one(
                                "span.price-item--sale"
                            )
                        )

                        if not sale_el:

                            sale_el = (
                                detail_soup.select_one(
                                    ".price-item--sale"
                                )
                            )

                        if sale_el:

                            price = extract_price(
                                sale_el.get_text(
                                    " ",
                                    strip=True,
                                )
                            )

                        # ====================================
                        # ORIGINAL PRICE
                        # ====================================

                        original_el = (
                            detail_soup.select_one(
                                "s.price-item--regular"
                            )
                        )

                        if not original_el:

                            original_el = (
                                detail_soup.select_one(
                                    ".price-item--regular"
                                )
                            )

                        if original_el:

                            original_price = (
                                extract_price(
                                    original_el.get_text(
                                        " ",
                                        strip=True,
                                    )
                                )
                            )

                        # ====================================
                        # DISCOUNT
                        # ====================================

                        discount_el = (
                            detail_soup.select_one(
                                "span.badge"
                            )
                        )

                        if discount_el:

                            discount = (
                                extract_discount(
                                    discount_el.get_text(
                                        " ",
                                        strip=True,
                                    )
                                )
                            )

                    # ========================================
                    # CALCULATE DISCOUNT
                    # ========================================

                    if not discount:

                        calculated_discount = (
                            calculate_discount(
                                price,
                                original_price,
                            )
                        )

                        if calculated_discount is not None:

                            discount = (
                                calculated_discount
                            )

                    # ========================================
                    # REQUIRED FIELD VALIDATION
                    # ========================================

                    if price is None:

                        logging.info(
                            "Skipping %s because "
                            "price was not found.",
                            name,
                        )

                        continue

                    if original_price is None:

                        logging.info(
                            "Skipping %s because "
                            "original price was not found.",
                            name,
                        )

                        continue

                    if discount is None:

                        logging.info(
                            "Skipping %s because "
                            "discount was not detected.",
                            name,
                        )

                        continue

                    # Description is optional.

                    if not description:

                        logging.info(
                            "Continuing %s without description.",
                            name,
                        )

                        description = None

                    # ========================================
                    # TIMESTAMP
                    # ========================================

                    timestamp = datetime.now(
                        timezone(
                            timedelta(
                                hours=5,
                                minutes=30,
                            )
                        )
                    ).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )

                    # ========================================
                    # FINAL PRODUCT
                    # ========================================

                    product_data = {
                        "name": name,
                        "price": price,
                        "currency": "₹",
                        "original_price": original_price,
                        "discount": discount,
                        "ratings": ratings,
                        "description": description,
                        "image_link": image_link,
                        "product_link": product_link,
                        "organization_id": "Dealwallet",
                        "store_id": "Wiselife",
                        "categories_id": category,
                        "created_at": timestamp,
                    }

                    products.append(
                        product_data
                    )

                    seen_names.add(
                        name_key
                    )

                    seen_links.add(
                        product_link
                    )

                    category_count += 1

                    logging.info(
                        "Scraped WiseLife product: %s",
                        name,
                    )

                except Exception as exc:

                    logging.exception(
                        "Error parsing WiseLife "
                        "product card #%s: %s",
                        index,
                        exc,
                    )

            category_counts[category] = (
                category_counts.get(
                    category,
                    0,
                )
                + category_count
            )

            logging.info(
                "Total products scraped for "
                "'%s': %s",
                category,
                category_count,
            )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    logging.info("=" * 60)

    logging.info(
        "Total WiseLife products scraped: %s",
        len(products),
    )

    logging.info(
        "WiseLife category counts: %s",
        category_counts,
    )

    return products


# ============================================================
# WINDOWS PROCESS RUNNER
# ============================================================

def run_wiselife_scraper():

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)

    logging.info(
        "WiseLife browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_wiselife_products()
        )

        logging.info(
            "WiseLife browser process finished. "
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "WiseLife browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class WiseLifeSpider(scrapy.Spider):

    name = "wiselife"

    allowed_domains = [
        "wiselife.in",
        "www.wiselife.in",
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting WiseLife Crawl4AI scraper..."
        )

        self.logger.info("=" * 60)

        # ----------------------------------------------------
        # RUN CRAWL4AI IN SEPARATE PROCESS
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_wiselife_scraper,
            )

        # ----------------------------------------------------
        # SAVE RAW SCRAPED DATA
        # ----------------------------------------------------

        with open(
            "scrape_wiselife.json",
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
            "WiseLife scraper returned %s products.",
            len(products),
        )

        # ----------------------------------------------------
        # YIELD TO SCRAPY PIPELINE
        # ----------------------------------------------------

        for product in products:

            if not product.get(
                "image_link"
            ):
                continue

            if not product.get(
                "product_link"
            ):
                continue

            yield product

        self.logger.info("=" * 60)

        self.logger.info(
            "WiseLife spider completed."
        )

        self.logger.info("=" * 60)


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    data = run_wiselife_scraper()

    with open(
        "scrape_wiselife.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        "Saved in scrape_wiselife.json"
    )

    print(
        "Total products:",
        len(data),
    )