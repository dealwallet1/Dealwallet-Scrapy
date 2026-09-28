# Created at: 2026-09-25 10:00 IST
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
import scrapy
from dotenv import load_dotenv
from scrapy import signals
from scrapy.exceptions import DontCloseSpider
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler


# ============================================================
# CONFIG
# ============================================================

HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.google.com/",
}


USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) "
        "Gecko/20100101 Firefox/129.0"
    ),
]


BASE_URL = "https://www.wrapcart.com"


COLLECTIONS = [
    {
        "url": (
            "https://www.wrapcart.com/collections/"
            "cases-for-iphone-13-pro-max"
        ),
        "category": "Mobile Accessories",
    },
    {
        "url": (
            "https://www.wrapcart.com/collections/"
            "3d-mobile-skins"
        ),
        "category": "Mobile Accessories",
    },
    {
        "url": (
            "https://www.wrapcart.com/collections/cover-skin-combo"
        ),
        "category": "Mobile Accessories",
    },
    {
        "url": (
            "https://www.wrapcart.com/collections/"
            "laptop-skins"
        ),
        "category": "Computers Gadgets",
    },
    {
        "url": (
            "https://www.wrapcart.com/collections/"
            "laptop-bags-for-her"
        ),
        "category": "Bags",
    },
]


MAX_PAGES = 2

MAX_PRODUCTS_PER_CATEGORY = 90

MAX_TOTAL_PRODUCTS = 500

# ============================================================
# JSON OUTPUT
# ============================================================

# ============================================================
# SINGLE JSON OUTPUT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_FILE = PROJECT_ROOT / "scrape_final.json"

# DATABASE CONFIGURATION
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
DB_PRODUCTS_TABLE = os.getenv("DB_PRODUCTS_TABLE", "products")
DB_STORE_TABLE = os.getenv("DB_STORE_TABLE", "stores")
STORE_NAME = "Wrapcart"
ORGANIZATION_NAME = "DealWallet"



# Maximum number of product detail pages opened simultaneously


# ============================================================
# TEXT CLEANING
# ============================================================

def clean(text):
    if not text:
        return None

    text = re.sub(r"\s+", " ", str(text)).strip()

    # Remove leading punctuation
    text = re.sub(r"^[.,\-\s]+", "", text)

    return text if text else None


# ============================================================
# PRICE EXTRACTION
# ============================================================

def extract_price(text):
    if not text:
        return None

    text = str(text)

    # Remove currency and commas
    text = (
        text
        .replace("₹", "")
        .replace(",", "")
        .replace(".00", "")
        .strip()
    )

    match = re.search(r"(\d+(?:\.\d+)?)", text)

    if not match:
        return None

    try:
        value = float(match.group(1))

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def extract_image_from_srcset(srcset):
    if not srcset:
        return None

    parts = []

    for part in srcset.split(","):
        part = part.strip()

        if not part:
            continue

        url = part.split(" ")[0]

        if url:
            parts.append(url)

    if not parts:
        return None

    # Use the largest image available
    src = parts[-1]

    if src.startswith("//"):
        return "https:" + src

    return src


def extract_product_image(item):
    """
    Verified Wrapcart HTML:

    <img
        src="//www.wrapcart.com/cdn/shop/files/..."
        ...
        class="motion-reduce"
    >

    The first image is the main product image.
    """

    # Main image
    img_tag = item.select_one(
        "img.motion-reduce"
    )

    if not img_tag:
        # Fallback
        img_tag = item.select_one(
            "img"
        )

    if not img_tag:
        return None

    # Prefer srcset
    image = extract_image_from_srcset(
        img_tag.get("srcset")
    )

    if image:
        return image

    # Then src
    image = img_tag.get("src")

    if image:

        if image.startswith("//"):
            return "https:" + image

        if image.startswith("data:"):
            return None

        return image

    # Other lazy-loading attributes
    for attribute in [
        "data-src",
        "data-original",
        "data-lazy-src",
    ]:

        image = img_tag.get(attribute)

        if image:

            if image.startswith("//"):
                return "https:" + image

            if image.startswith("data:"):
                continue

            return image

    return None


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(item):
    """
    Verified HTML:

    <a
        href="/collections/.../products/..."
        class="full-unstyled-link card-title-change"
    >
        ...
        Product Name
        ...
    </a>
    """

    title_tag = item.select_one(
        'a.card-title-change[href*="/products/"]'
    )

    if not title_tag:
        return None

    name = clean(
        title_tag.get_text(
            " ",
            strip=True,
        )
    )

    return name


# ============================================================
# PRODUCT LINK
# ============================================================

def extract_product_link(item):
    """
    Verified Wrapcart product URL:

    /collections/cases-for-iphone-13-pro-max/products/...
    """

    link_tag = item.select_one(
        'a.card-title-change[href*="/products/"]'
    )

    if not link_tag:
        # Fallback to media link
        link_tag = item.select_one(
            'a[href*="/products/"]'
        )

    if not link_tag:
        return None

    href = link_tag.get("href")

    if not href:
        return None

    return urljoin(
        BASE_URL,
        href,
    )


# ============================================================
# CURRENT PRICE
# ============================================================

def extract_current_price(item):
    """
    Verified HTML:

    <span class="price-item price-item--sale price-item--last">
        <span class="money">₹449.00</span>
    </span>
    """

    price_tag = item.select_one(
        "span.price-item--sale span.money"
    )

    if not price_tag:
        # Fallback
        price_tag = item.select_one(
            ".price-item--sale .money"
        )

    if not price_tag:
        return None

    return extract_price(
        price_tag.get_text(
            " ",
            strip=True,
        )
    )


# ============================================================
# ORIGINAL PRICE
# ============================================================

def extract_original_price(item):
    """
    Verified HTML:

    <s class="price-item price-item--regular">
        <span class="money">₹1,500.00</span>
    </s>
    """

    original_price_tag = item.select_one(
        "s.price-item--regular span.money"
    )

    if not original_price_tag:
        original_price_tag = item.select_one(
            "s .money"
        )

    if not original_price_tag:
        return None

    return extract_price(
        original_price_tag.get_text(
            " ",
            strip=True,
        )
    )


# ============================================================
# DISCOUNT
# ============================================================

def extract_discount(item, price, original_price):
    """
    Prefer the displayed savings text.

    Example:

    Save 17%

    If unavailable, calculate from prices.
    """

    savings_tag = item.select_one(
        ".grid-product__price--savings"
    )

    if savings_tag:

        savings_text = clean(
            savings_tag.get_text(
                " ",
                strip=True,
            )
        )

        if savings_text:

            match = re.search(
                r"(\d+(?:\.\d+)?)\s*%",
                savings_text,
            )

            if match:

                try:
                    return int(
                        float(
                            match.group(1)
                        )
                    )

                except ValueError:
                    pass

    # Calculate discount
    if (
        price is not None
        and original_price is not None
        and original_price > price
    ):

        return round(
            (
                (original_price - price)
                / original_price
            )
            * 100
        )

    return None


# ============================================================
# RATING
# ============================================================

def extract_rating(item):
    """
    Verified Judge.me structure:

    <span
        class="jdgm-prev-badge__stars"
        data-score="4.73"
    >
    """

    rating_tag = item.select_one(
        "span.jdgm-prev-badge__stars[data-score]"
    )

    if rating_tag:

        rating = rating_tag.get(
            "data-score"
        )

        if rating:

            match = re.search(
                r"\d+(?:\.\d+)?",
                rating,
            )

            if match:

                try:
                    return float(
                        match.group()
                    )

                except ValueError:
                    pass

    # Fallback
    average_tag = item.select_one(
        ".jdgm-prev-badge[data-average-rating]"
    )

    if average_tag:

        rating = average_tag.get(
            "data-average-rating"
        )

        if rating:

            try:
                return float(rating)

            except ValueError:
                pass

    return None


# ============================================================
# DETAIL PAGE PRICE TRACKING SELECTORS
# ============================================================

DETAIL_PRICE_SELECTOR = ".price-item--sale .money"
DETAIL_ORIGINAL_PRICE_SELECTOR = ".compare-at-price .price-item--regular .money"
DETAIL_RATING_SELECTOR = ".jdgm-prev-badge__stars[data-score]"


def extract_detail_price(soup):
    """Extract current/sale price from the product detail page."""
    tag = soup.select_one(DETAIL_PRICE_SELECTOR)
    if not tag:
        return None
    return extract_price(tag.get_text(" ", strip=True))


def extract_detail_original_price(soup):
    """Extract compare-at/original price from the product detail page."""
    tag = soup.select_one(DETAIL_ORIGINAL_PRICE_SELECTOR)
    if not tag:
        # Safe fallback for the same detail-page structure.
        tag = soup.select_one(".compare-at-price .money")
    if not tag:
        return None
    return extract_price(tag.get_text(" ", strip=True))


def extract_detail_discount(soup, price, original_price):
    """
    Extract discount from the detail page when a discount element exists.
    If it is not available, calculate the percentage from price and
    original_price and return an integer rounded value.
    """
    # Optional displayed discount/savings selectors.
    discount_selectors = (
        ".price__badge-sale",
        ".price__badge",
        ".grid-product__price--savings",
        "[class*='discount']",
        "[class*='savings']",
    )

    for selector in discount_selectors:
        tag = soup.select_one(selector)
        if not tag:
            continue

        text_value = clean(tag.get_text(" ", strip=True))
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text_value)
        if match:
            try:
                return int(round(float(match.group(1))))
            except (TypeError, ValueError):
                pass

    # No discount element found: calculate from prices.
    if (
        price is not None
        and original_price is not None
        and original_price > price
    ):
        return int(round(
            ((original_price - price) / original_price) * 100
        ))

    return None


def extract_detail_rating(soup):
    """Extract Judge.me rating from the product detail page."""
    tag = soup.select_one(DETAIL_RATING_SELECTOR)
    if tag:
        value = tag.get("data-score")
        if value:
            match = re.search(r"\d+(?:\.\d+)?", value)
            if match:
                try:
                    return float(match.group())
                except ValueError:
                    pass

    # Fallback from the parent Judge.me badge.
    tag = soup.select_one(".jdgm-prev-badge[data-average-rating]")
    if tag:
        value = tag.get("data-average-rating")
        if value:
            try:
                return float(value)
            except ValueError:
                pass

    return None


# ============================================================
# COLLECTION DESCRIPTION
# ============================================================

def extract_collection_description(item):
    """
    Verified Wrapcart HTML:

    <div class="product-description__collection none">
        Product description...
    </div>
    """

    description_tag = item.select_one(
        ".product-description__collection"
    )

    if not description_tag:
        return None

    description = clean(
        description_tag.get_text(
            " ",
            strip=True,
        )
    )

    return description


# ============================================================
# PRODUCT DETAIL PAGE
# ============================================================

async def scrape_product_details(
    crawler,
    product_url,
    semaphore,
):
    """
    Opens the product detail page.

    This function extracts BOTH:

        1. Description
        2. Rating

    The request is protected by a semaphore so that
    only DETAIL_CONCURRENCY pages are requested at once.
    """

    async with semaphore:

        try:

            result = await crawler.arun(
                url=product_url
            )

        except Exception as e:

            print(
                f"      Detail page error: {e}"
            )

            return None, None

        if not result.success:
            return None, None

        if not result.html:
            return None, None

        soup = BeautifulSoup(
            result.html,
            "html.parser",
        )

    # --------------------------------------------------------
    # RATING
    # --------------------------------------------------------

    rating = None

    # Exact Judge.me selector from provided HTML:
    #
    # <span
    #     class="jdgm-prev-badge__stars"
    #     data-score="4.73"
    #     ...
    # >
    #
    # data-score is the preferred source.

    rating_tag = soup.select_one(
        "span.jdgm-prev-badge__stars[data-score]"
    )

    if rating_tag:

        score = rating_tag.get(
            "data-score"
        )

        if score:

            match = re.search(
                r"\d+(?:\.\d+)?",
                score,
            )

            if match:

                try:
                    rating = float(
                        match.group()
                    )

                except ValueError:
                    pass

    # Fallback to review summary
    if rating is None:

        rating_tag = soup.select_one(
            "span.jdgm-rev-widg__summary-average"
        )

        if rating_tag:

            match = re.search(
                r"\d+(?:\.\d+)?",
                rating_tag.get_text(
                    " ",
                    strip=True,
                ),
            )

            if match:

                try:
                    rating = float(
                        match.group()
                    )

                except ValueError:
                    pass

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    description = None

    description_selectors = [
        "div.product__description",
        "div.product__info",
        "div.rte",
        ".product-description",
        ".product__description.rte",
    ]

    for selector in description_selectors:

        description_tag = soup.select_one(
            selector
        )

        if not description_tag:
            continue

        text = clean(
            description_tag.get_text(
                " ",
                strip=True,
            )
        )

        if text and len(text) >= 100:

            description = text

            break

    return description, rating


# ============================================================
# CREATED AT
# ============================================================

IST = timezone(timedelta(hours=5, minutes=30))


def get_created_at():
    """Return the scrape timestamp in India Standard Time."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


# ============================================================
# JSON OUTPUT HELPERS
# ============================================================

def initialize_output_json():
    """
    Start each run with one clean JSON file.

    The file contains both:
      - newly scraped products
      - existing products price-tracked in Phase 2
    """
    try:
        json_path = Path(OUTPUT_FILE).resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with json_path.open("w", encoding="utf-8") as file:
            json.dump([], file, ensure_ascii=False, indent=4)
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "JSON INITIALIZATION ERROR | %s",
            exc,
        )
        return False


def _load_output_json():
    json_path = Path(OUTPUT_FILE).resolve()

    if not json_path.exists():
        return []

    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, list) else []

    except (json.JSONDecodeError, OSError):
        return []


def save_to_single_json(product):
    """
    Save every product into the same JSON file.

    Existing products are identified primarily by product_id.
    New products, which do not have a DB UUID until the database
    pipeline inserts them, are identified by product_link.
    """
    try:
        json_path = Path(OUTPUT_FILE).resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)

        data = _load_output_json()

        product_id = product.get("product_id")
        product_link = product.get("product_link")

        existing_index = None

        for index, existing in enumerate(data):
            if not isinstance(existing, dict):
                continue

            if product_id and existing.get("product_id") == product_id:
                existing_index = index
                break

            if (
                not product_id
                and product_link
                and existing.get("product_link") == product_link
            ):
                existing_index = index
                break

        if existing_index is not None:
            data[existing_index] = product
        else:
            data.append(product)

        with json_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=4,
                default=str,
            )

        return True

    except Exception as exc:
        logging.getLogger(__name__).warning(
            "JSON SAVE ERROR | %s",
            exc,
        )
        return False


def get_scrape_url(product_link):
    """
    Return the clean product URL without query parameters.
    """
    if not product_link:
        return None

    return str(product_link).split("?", 1)[0].split("#", 1)[0]


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_db_connection():
    missing = []
    for key, value in (("DB_HOST", DB_HOST), ("DB_NAME", DB_NAME), ("DB_USER", DB_USER), ("DB_PASSWORD", DB_PASSWORD)):
        if not value:
            missing.append(key)
    if missing:
        raise RuntimeError("Missing database configuration in .env: " + ", ".join(missing))
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)


def get_store_uuid(cursor, store_name):
    query = f"""
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_STORE_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    """
    cursor.execute(query, (store_name,))
    row = cursor.fetchone()
    return str(row[0]) if row else None


def load_existing_products_from_db():
    """Load existing Wrapcart products before collection scraping."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        store_uuid = get_store_uuid(cursor, STORE_NAME)
        if not store_uuid:
            raise RuntimeError(f"Store not found in database: {STORE_NAME}")
        query = f"""
            SELECT p.id, p.name, p.product_link, p.price, p.original_price,
                   p.discount, p.ratings, p.description, p.image_link,
                   p.organization_id, p.store_id, p.categories_id,
                   c.name AS category_name, p.affiliate_url
            FROM "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}" p
            LEFT JOIN "{DB_SCHEMA}"."categories" c
                ON p.categories_id = c.id
            WHERE p.store_id = %s
              AND p.name IS NOT NULL
        """
        cursor.execute(query, (store_uuid,))
        products_by_name = {}
        price_products = []
        for row in cursor.fetchall():
            (product_id, name, product_link, old_price, old_original_price,
             old_discount, old_rating, description, image_link,
             organization_id, db_store_id, categories_id, category_name,
             affiliate_url) = row
            normalized_name = str(name).strip().lower()
            record = {
                "id": str(product_id),
                "name": name,
                "product_link": product_link,
                "price": old_price,
                "original_price": old_original_price,
                "discount": old_discount,
                "ratings": old_rating,
                "description": description,
                "image_link": image_link,
                "organization_id": organization_id,
                "store_id": db_store_id,
                "store_name": STORE_NAME,
                "categories_id": category_name,
                "affiliate_url": affiliate_url,
            }
            if normalized_name:
                products_by_name[normalized_name] = record
            if product_link and str(product_link).strip() not in ("", "N/A"):
                price_products.append(record.copy())
        return store_uuid, products_by_name, price_products
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def finalize_new_products_in_json():
    """
    After Scrapy finishes yielding items, the DealWallet pipeline has
    already attempted the database INSERT/UPDATE.

    For records created during Phase 1, retrieve the actual PostgreSQL
    UUID and stored price so the single JSON file contains the same
    product_id/database_price structure as existing products.
    """
    json_path = Path(OUTPUT_FILE).resolve()

    if not json_path.exists():
        return 0

    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return 0

        connection = get_db_connection()
        cursor = connection.cursor()

        store_uuid = get_store_uuid(cursor, STORE_NAME)

        if not store_uuid:
            cursor.close()
            connection.close()
            return 0

        added_count = 0

        for product in data:
            if not isinstance(product, dict):
                continue

            if product.get("product_id") is not None:
                continue

            name = product.get("name")

            if not name:
                continue

            cursor.execute(
                f'''
                SELECT id, price, affiliate_url
                FROM "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}"
                WHERE store_id = %s
                  AND name = %s
                LIMIT 1
                ''',
                (store_uuid, name),
            )

            row = cursor.fetchone()

            if not row:
                continue

            product["product_id"] = str(row[0])
            product["database_price"] = row[1]
            product["affiliate_url"] = product.get("affiliate_url") or row[2]
            product["price_changed"] = False
            product["price_difference"] = 0
            product["status"] = "success"

            added_count += 1

        cursor.close()
        connection.close()

        with json_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=4,
                default=str,
            )

        return added_count

    except Exception as exc:
        logging.getLogger(__name__).warning(
            "FINAL NEW PRODUCT DB RECONCILIATION ERROR | %s",
            exc,
        )
        return 0


# ============================================================
# WRAPCART SCRAPY SPIDER
# ============================================================

class WrapCartSpider(scrapy.Spider):
    name = "wrapcart"
    allowed_domains = [
        "wrapcart.com",
        "www.wrapcart.com",
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 1,
        "LOG_LEVEL": "INFO",
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.spider_idle, signal=signals.spider_idle)
        return spider

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_links = set()
        self.seen_names = set()
        self.total_products = 0
        self.category_counts = {}
        self.store_uuid = None
        self.existing_products = {}
        self.existing_price_products = []
        self.phase1_completed = False
        self.price_tracking_started = False
        self.price_tracking_completed = False
        self.price_total = 0
        self.price_success = 0
        self.price_failed = 0
        self.price_updates = []
        self.existing_products_skipped = 0

        # Final processing counters
        self.new_products_added = 0
        self.existing_products_found = 0
        self.products_updated = 0
        self.products_unchanged = 0
        self.processing_failed = 0

        # New-product diagnostic counters
        self.new_candidates = 0
        self.new_skipped_price = 0
        self.new_skipped_original_price = 0
        self.new_skipped_discount = 0
        self.new_skipped_detail = 0

    # ========================================================
    # START
    # ========================================================

    async def start(self):
        """
        Scrapy 2.18+ start entry point.

        Use async def start() instead of the deprecated start_requests()
        method so the initial collection requests are actually scheduled.
        """

        self.logger.info("=" * 70)
        self.logger.info("STARTING WRAPCART SCRAPER")
        self.logger.info("=" * 70)

        initialize_output_json()

        try:
            self.store_uuid, self.existing_products, self.existing_price_products = load_existing_products_from_db()

            self.logger.info(
                "DATABASE CHECK | Store: %s | Store UUID: %s | Existing products in DB: %s | Products with links: %s",
                STORE_NAME,
                self.store_uuid,
                len(self.existing_products),
                len(self.existing_price_products),
            )
        except Exception as exc:
            self.logger.exception("DATABASE CHECK FAILED | SCRAPER STOPPED | Error: %s", exc)
            return

        for collection in COLLECTIONS:
            collection_url = collection["url"]
            category = collection["category"]

            self.category_counts.setdefault(
                category,
                0,
            )

            page_url = f"{collection_url}?page=1"

            self.logger.info(
                "START COLLECTION | Category: %s | URL: %s",
                category,
                page_url,
            )

            yield scrapy.Request(
                url=page_url,
                callback=self.parse_collection,
                cb_kwargs={
                    "collection_url": collection_url,
                    "category": category,
                    "page": 1,
                },
                dont_filter=True,
            )

    # ========================================================
    # COLLECTION PAGE
    # ========================================================

    def parse_collection(
        self,
        response,
        collection_url,
        category,
        page,
    ):
        self.logger.info(
            "SCRAPING CATEGORY: %s | PAGE: %s | URL: %s | STATUS: %s",
            category,
            page,
            response.url,
            response.status,
        )

        if response.status != 200:
            self.logger.warning(
                "COLLECTION REQUEST FAILED | Category: %s | "
                "Page: %s | Status: %s | URL: %s",
                category,
                page,
                response.status,
                response.url,
            )
            return

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        items = soup.select(
            "li.product-grid__item"
        )

        self.logger.info(
            "PRODUCT CARDS FOUND: %s",
            len(items),
        )

        if not items:
            self.logger.info(
                "NO PRODUCTS FOUND | Category: %s | Page: %s",
                category,
                page,
            )
            return

        new_products = 0

        for index, item in enumerate(
            items,
            start=1,
        ):
            if (
                self.category_counts[category]
                >= MAX_PRODUCTS_PER_CATEGORY
            ):
                break

            if (
                self.total_products
                >= MAX_TOTAL_PRODUCTS
            ):
                self.logger.info(
                    "MAX TOTAL PRODUCTS REACHED: %s",
                    MAX_TOTAL_PRODUCTS,
                )
                return

            try:
                product = self.parse_product_card(
                    item,
                    category,
                    index,
                    len(items),
                )

                if not product:
                    continue

                new_products += 1

                yield scrapy.Request(
                    url=product["product_link"],
                    callback=self.parse_product_detail,
                    cb_kwargs={
                        "product": product,
                        "collection_url": collection_url,
                        "category": category,
                        "page": page,
                    },
                    dont_filter=True,
                )

            except Exception:
                self.logger.exception(
                    "PRODUCT ERROR | Category: %s | Page: %s | Index: %s",
                    category,
                    page,
                    index,
                )

        self.logger.info(
            "NEW PRODUCTS ON PAGE: %s | Category: %s | Page: %s",
            new_products,
            category,
            page,
        )

        # Continue pagination whenever the page returned product cards.
        # Do NOT depend on new_products here: page 1 may contain only
        # existing/invalid products while page 2 can still contain new ones.
        if (
            len(items) > 0
            and page < MAX_PAGES
            and self.category_counts[category]
            < MAX_PRODUCTS_PER_CATEGORY
            and self.total_products < MAX_TOTAL_PRODUCTS
        ):
            next_page = page + 1
            next_url = f"{collection_url}?page={next_page}"

            yield scrapy.Request(
                url=next_url,
                callback=self.parse_collection,
                cb_kwargs={
                    "collection_url": collection_url,
                    "category": category,
                    "page": next_page,
                },
                dont_filter=True,
            )

    # ========================================================
    # PARSE PRODUCT CARD
    # ========================================================

    def parse_product_card(
        self,
        item,
        category,
        index,
        total_items,
    ):
        self.logger.info(
            "--- Product %s/%s ---",
            index,
            total_items,
        )

        # ----------------------------------------------------
        # NAME
        # ----------------------------------------------------

        name = extract_product_name(item)

        if not name:
            self.logger.info(
                "SKIPPED: name missing"
            )
            return None

        # ----------------------------------------------------
        # LINK
        # ----------------------------------------------------

        product_link = extract_product_link(item)

        if not product_link:
            self.logger.info(
                "SKIPPED: product link missing | %s",
                name,
            )
            return None

        # ----------------------------------------------------
        # DUPLICATE
        # ----------------------------------------------------

        normalized_name = name.lower().strip()

        if product_link in self.seen_links:
            self.logger.info(
                "SKIPPED: duplicate URL | %s",
                product_link,
            )
            return None

        if normalized_name in self.seen_names:
            self.logger.info(
                "SKIPPED: duplicate name | %s",
                name,
            )
            return None

        # ----------------------------------------------------
        # DATABASE EXISTING-PRODUCT CHECK
        # ----------------------------------------------------
        if normalized_name in self.existing_products:
            existing = self.existing_products[normalized_name]
            self.existing_products_skipped += 1
            self.existing_products_found += 1
            self.logger.info(
                "EXISTING PRODUCT FOUND | SKIP NEW SCRAPE | %s | DB UUID: %s | Price tracking will run in Phase 2",
                name,
                existing.get("id"),
            )
            return None

        self.new_candidates += 1
        self.logger.info(
            "NEW PRODUCT | NOT FOUND IN DATABASE | %s | FULL SCRAPE | Candidate #%s",
            name,
            self.new_candidates,
        )

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        price = extract_current_price(item)
        original_price = extract_original_price(item)

        self.logger.info(
            "Name: %s | Price: %s | Original Price: %s",
            name,
            price,
            original_price,
        )

        if price is None:
            self.new_skipped_price += 1
            self.logger.info(
                "SKIPPED NEW PRODUCT: price missing | %s",
                name,
            )
            return None

        # Original price is optional. Keep the product instead of dropping
        # it when WrapCart does not display a compare-at price.
        if original_price is None:
            self.new_skipped_original_price += 1
            self.logger.info(
                "ORIGINAL PRICE MISSING | KEEPING NEW PRODUCT | %s",
                name,
            )

        if (
            original_price is not None
            and original_price <= price
        ):
            self.logger.info(
                "NO VALID COMPARE-AT PRICE | KEEPING NEW PRODUCT | %s",
                name,
            )
            original_price = None

        # ----------------------------------------------------
        # DISCOUNT
        # ----------------------------------------------------

        discount = extract_discount(
            item,
            price,
            original_price,
        )

        if discount is None or discount <= 0:
            self.new_skipped_discount += 1
            discount = 0
            self.logger.info(
                "DISCOUNT NOT AVAILABLE | KEEPING NEW PRODUCT | %s | Discount: 0",
                name,
            )

        # ----------------------------------------------------
        # IMAGE
        # ----------------------------------------------------

        image = extract_product_image(item)

        # ----------------------------------------------------
        # RATING
        # ----------------------------------------------------

        rating = extract_rating(item)

        # ----------------------------------------------------
        # COLLECTION DESCRIPTION
        # ----------------------------------------------------

        description = extract_collection_description(
            item
        )

        product = {
            "product_id": None,
            "name": name,
            "database_price": None,
            "price": price,
            "original_price": original_price,
            "currency": "₹",
            "discount": discount,
            "ratings": rating,
            "description": description,
            "image_link": image or "N/A",
            "product_link": product_link,
            "scrape_url": get_scrape_url(product_link),
            "organization_id": ORGANIZATION_NAME,
            "store_id": STORE_NAME,
            "store_name": STORE_NAME,
            "categories_id": category,
            "affiliate_url": None,
            "created_at": get_created_at(),
            "status": "success",
            "http_status": None,
            "price_changed": False,
            "price_difference": 0,
        }

        return product

    # ========================================================
    # DETAIL PAGE
    # ========================================================

    async def fetch_detail_data(
        self,
        product_url,
    ):
        """
        Use Crawl4AI only for the product detail page.

        The original WrapCart scraper uses Crawl4AI for the same purpose:
        extracting description and rating from the detail page.
        """

        try:
            async with AsyncWebCrawler(
                verbose=False
            ) as crawler:

                result = await crawler.arun(
                    url=product_url,
                    wait_for="css:.jdgm-widget, .jdgm-prev-badge",
                    delay_before_return_html=3.0,
                )

        except Exception as exc:
            self.logger.warning(
                "DETAIL PAGE ERROR | %s | %s",
                product_url,
                exc,
            )
            return None, None

        if not result.success:
            self.logger.warning(
                "DETAIL PAGE FAILED | %s",
                product_url,
            )
            return None, None

        if not result.html:
            self.logger.warning(
                "DETAIL PAGE EMPTY | %s",
                product_url,
            )
            return None, None

        soup = BeautifulSoup(
            result.html,
            "html.parser",
        )

        # ----------------------------------------------------
        # RATING
        # ----------------------------------------------------

        rating = None

        # IMPORTANT:
        # WrapCart uses Judge.me. The visible HTML may contain the
        # Judge.me badge, but the rating can also be supplied through
        # Judge.me's metafield/widget data.
        #
        # On some WrapCart products the reviews are GROUPED reviews.
        # For example, the live product page can show:
        #   1243 reviews
        #   5 stars: 911
        #   4 stars: 332
        #
        # In that situation we calculate the same average:
        #   (911*5 + 332*4) / (911+332) = 4.7337 -> 4.73

        def parse_rating(value):
            if value is None:
                return None

            if isinstance(value, (int, float)):
                value = float(value)
                if 0 <= value <= 5:
                    return value
                return None

            match = re.search(
                r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
                str(value)
            )

            if match:
                try:
                    number = float(match.group(1))
                    if 0 <= number <= 5:
                        return number
                except ValueError:
                    pass

            return None

        def extract_rating_from_text(text_value):
            """
            Extract an explicit average rating from text/HTML.
            """
            if not text_value:
                return None

            patterns = [
                # Judge.me
                r'data-score\s*=\s*["\']([0-5](?:\.\d+)?)["\']',
                r'data-average-rating\s*=\s*["\']([0-5](?:\.\d+)?)["\']',

                # JSON / JS
                r'"ratingValue"\s*:\s*"?([0-5](?:\.\d+)?)"?',
                r'"averageRating"\s*:\s*"?([0-5](?:\.\d+)?)"?',
                r'"average_rating"\s*:\s*"?([0-5](?:\.\d+)?)"?',
                r'"review_average_rating"\s*:\s*"?([0-5](?:\.\d+)?)"?',

                # Judge.me variables
                r'average_rating["\']?\s*[:=]\s*["\']?'
                r'([0-5](?:\.\d+)?)',
            ]

            for pattern in patterns:
                match = re.search(
                    pattern,
                    text_value,
                    re.I
                )

                if match:
                    value = parse_rating(match.group(1))
                    if value is not None:
                        return value

            return None

        # --------------------------------------------------------
        # 1. Exact Judge.me badge
        # --------------------------------------------------------

        rating_tag = soup.select_one(
            "span.jdgm-prev-badge__stars[data-score]"
        )

        if rating_tag:
            rating = parse_rating(
                rating_tag.get("data-score")
            )

        # --------------------------------------------------------
        # 2. Judge.me parent badge
        #
        # Example from Judge.me:
        # .jdgm-prev-badge[data-average-rating="4.73"]
        # --------------------------------------------------------

        if rating is None:
            rating_tag = soup.select_one(
                ".jdgm-prev-badge[data-average-rating]"
            )

            if rating_tag:
                rating = parse_rating(
                    rating_tag.get("data-average-rating")
                )

        # --------------------------------------------------------
        # 3. Other Judge.me selectors
        # --------------------------------------------------------

        if rating is None:
            selectors = [
                ".jdgm-rev-widg__summary-average",
                ".jdgm-rev-widg__summary-text",
                ".jdgm-all-reviews-rating",
                ".jdgm-widget[data-score]",
                "[data-average-rating]",
            ]

            for selector in selectors:
                tag = soup.select_one(selector)

                if not tag:
                    continue

                rating = parse_rating(
                    tag.get("data-score")
                    or tag.get("data-average-rating")
                    or tag.get_text(" ", strip=True)
                )

                if rating is not None:
                    break

        # --------------------------------------------------------
        # 4. Search ALL Judge.me elements
        # --------------------------------------------------------

        if rating is None:
            for tag in soup.select(
                ".jdgm-widget, .jdgm-prev-badge, "
                ".jdgm-preview-badge, [class*='jdgm']"
            ):
                candidate = extract_rating_from_text(
                    str(tag)
                )

                if candidate is not None:
                    rating = candidate
                    break

        # --------------------------------------------------------
        # 5. Search JSON-LD
        # --------------------------------------------------------

        if rating is None:
            for script in soup.select(
                'script[type="application/ld+json"]'
            ):
                script_text = script.get_text(
                    " ",
                    strip=True
                )

                candidate = extract_rating_from_text(
                    script_text
                )

                if candidate is not None:
                    rating = candidate
                    break

        # --------------------------------------------------------
        # 6. Search Judge.me embedded JS/metafield data
        # --------------------------------------------------------

        if rating is None:
            for script in soup.find_all("script"):
                script_text = script.string or script.get_text(
                    " ",
                    strip=True
                )

                if not script_text:
                    continue

                lowered = script_text.lower()

                if not any(
                    keyword in lowered
                    for keyword in [
                        "jdgm",
                        "judgeme",
                        "reviewwidget",
                        "review_widget_data",
                        "average_rating",
                    ]
                ):
                    continue

                candidate = extract_rating_from_text(
                    script_text
                )

                if candidate is not None:
                    rating = candidate
                    break

        # --------------------------------------------------------
        # 7. GROUPED REVIEW RATING
        # --------------------------------------------------------
        #
        # This is the important WrapCart fallback.
        #
        # The live product page can show:
        #
        #   5 stars: 911 (73%)
        #   4 stars: 332 (27%)
        #
        # and explicitly says:
        #
        # "This product has no reviews of its own. The merchant
        # grouped it with related products..."
        #
        # Therefore the correct product-page rating is the grouped
        # Judge.me average, not a product-specific review average.
        #
        # We calculate it from all five star buckets.

        if rating is None:
            page_text = soup.get_text(
                "\n",
                strip=True
            )

            star_counts = {}

            for star in range(1, 6):
                patterns = [
                    rf"\b{star}\s*stars?\s*:\s*"
                    rf"([\d,]+)\s*\(",

                    rf"\b{star}\s*star\s*:\s*"
                    rf"([\d,]+)\s*\(",
                ]

                for pattern in patterns:
                    match = re.search(
                        pattern,
                        page_text,
                        re.I
                    )

                    if match:
                        try:
                            star_counts[star] = int(
                                match.group(1).replace(",", "")
                            )
                        except ValueError:
                            pass
                        break

            if star_counts:
                total_reviews = sum(
                    star_counts.values()
                )

                if total_reviews > 0:
                    weighted_total = sum(
                        star * count
                        for star, count in star_counts.items()
                    )

                    rating = round(
                        weighted_total / total_reviews,
                        2
                    )

        # --------------------------------------------------------
        # 8. Raw HTML final fallback
        # --------------------------------------------------------

        if rating is None:
            rating = extract_rating_from_text(
                result.html
            )

        # --------------------------------------------------------
        # FINAL RATING LOG
        # --------------------------------------------------------

        if rating is not None:
            self.logger.info(
                "RATING EXTRACTED | %s | Rating: %.2f",
                product_url,
                rating
            )
        else:
            self.logger.warning(
                "RATING NOT FOUND | %s",
                product_url
            )

        # ----------------------------------------------------
        # DESCRIPTION
        # ----------------------------------------------------

        description = None

        description_selectors = [
            "div.product__description",
            "div.product__info",
            "div.rte",
            ".product-description",
            ".product__description.rte",
        ]

        for selector in description_selectors:
            description_tag = soup.select_one(
                selector
            )

            if not description_tag:
                continue

            text = clean(
                description_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            if text and len(text) >= 100:
                description = text
                break

        return description, rating

    # ========================================================
    # ASYNC DETAIL CALLBACK
    # ========================================================

    def parse_product_detail(
        self,
        response,
        product,
        collection_url,
        category,
        page,
    ):
        """
        Scrapy callback that obtains missing detail information through
        Crawl4AI and then yields the final item to the DealWallet pipeline.
        """

        description = product.get(
            "description"
        )
        rating = product.get(
            "ratings"
        )

        # ----------------------------------------------------
        # EXTRACT RATING DIRECTLY FROM SCRAPY RESPONSE
        # ----------------------------------------------------
        #
        # WrapCart/Judge.me can return the rating in HTML such as:
        #
        # <div style="display:none" class="jdgm-prev-badge"
        #      data-average-rating="4.73"
        #      data-number-of-reviews="1243">
        #     <span class="jdgm-prev-badge__stars"
        #           data-score="4.73">
        # ----------------------------------------------------

        try:
            detail_soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            if rating is None:
                badge = detail_soup.select_one(
                    ".jdgm-prev-badge[data-average-rating]"
                )

                if badge:
                    raw_rating = (
                        badge.get("data-average-rating")
                        or badge.select_one(
                            ".jdgm-prev-badge__stars[data-score]"
                        ).get("data-score")
                        if badge.select_one(
                            ".jdgm-prev-badge__stars[data-score]"
                        )
                        else None
                    )

                    if raw_rating:
                        match = re.search(
                            r"\d+(?:\.\d+)?",
                            str(raw_rating),
                        )
                        if match:
                            value = float(match.group())
                            if 0 <= value <= 5:
                                rating = value

                # Exact fallback to the child span.
                if rating is None:
                    stars = detail_soup.select_one(
                        "span.jdgm-prev-badge__stars[data-score]"
                    )
                    if stars:
                        match = re.search(
                            r"\d+(?:\.\d+)?",
                            stars.get("data-score", ""),
                        )
                        if match:
                            value = float(match.group())
                            if 0 <= value <= 5:
                                rating = value

            if rating is not None:
                product["ratings"] = round(float(rating), 2)
                self.logger.info(
                    "RATING FROM SCRAPY HTML | %s | Rating: %.2f",
                    product["product_link"],
                    product["ratings"],
                )

        except Exception as exc:
            self.logger.warning(
                "SCRAPY HTML RATING ERROR | %s | %s",
                product["product_link"],
                exc,
            )

        # Detail page is only required when collection data is incomplete.
        if (
            not description
            or rating is None
        ):
            self.logger.info(
                "DETAIL PAGE REQUIRED | %s | "
                "description=%s | rating=%s",
                product["name"],
                "missing" if not description else "found",
                "missing" if rating is None else "found",
            )

            # Scrapy itself is synchronous at this callback boundary, so
            # create a short-lived async runner for the Crawl4AI request.
            try:
                detail_description, detail_rating = (
                    asyncio.run(
                        self.fetch_detail_data(
                            product["product_link"]
                        )
                    )
                )

            except Exception as exc:
                self.logger.warning(
                    "DETAIL EXTRACTION ERROR | %s | %s",
                    product["name"],
                    exc,
                )
                detail_description = None
                detail_rating = None

            if (
                not description
                and detail_description
            ):
                product["description"] = (
                    detail_description
                )

            if (
                product.get("ratings") is None
                and detail_rating is not None
            ):
                product["ratings"] = (
                    detail_rating
                )

        # ----------------------------------------------------
        # FINAL VALIDATION
        # ----------------------------------------------------

        if not product.get("description"):
            self.new_skipped_detail += 1
            self.logger.info(
                "DESCRIPTION NOT FOUND | KEEPING NEW PRODUCT | %s | Using N/A",
                product["name"],
            )

        product["description"] = clean(
            product.get("description")
        ) or "N/A"

        product["image_link"] = (
            product.get("image_link")
            or "N/A"
        )

        product_link = product[
            "product_link"
        ]
        normalized_name = product[
            "name"
        ].lower().strip()

        if product_link in self.seen_links:
            self.logger.info(
                "SKIPPED: duplicate URL | %s",
                product_link,
            )
            return

        if normalized_name in self.seen_names:
            self.logger.info(
                "SKIPPED: duplicate name | %s",
                product["name"],
            )
            return

        # Mark the product as accepted only after all validation is complete.
        self.seen_links.add(product_link)
        self.seen_names.add(normalized_name)

        self.category_counts[
            category
        ] += 1

        self.total_products += 1

        self.logger.info(
            "SCRAPED SUCCESSFULLY | %s | "
            "Price: ₹%s | Original: ₹%s | "
            "Discount: %s%% | Rating: %s | Created At: %s",
            product["name"],
            product["price"],
            product["original_price"],
            product["discount"],
            product.get("ratings"),
            product.get("created_at"),
        )

        # HTTP status belongs to the collection/detail response that
        # produced the final new-product record.
        product["http_status"] = response.status
        product["status"] = "success"

        # New product is ready to be sent to the DealWallet pipeline.
        self.new_products_added += 1

        # Save the exact final product in the single JSON file.
        save_to_single_json(product)

        yield product

        # ----------------------------------------------------
        # STOP INFORMATION
        # ----------------------------------------------------

        if (
            self.category_counts[category]
            >= MAX_PRODUCTS_PER_CATEGORY
        ):
            self.logger.info(
                "CATEGORY LIMIT REACHED | %s | %s products",
                category,
                self.category_counts[category],
            )

        if (
            self.total_products
            >= MAX_TOTAL_PRODUCTS
        ):
            self.logger.info(
                "TOTAL PRODUCT LIMIT REACHED | %s",
                self.total_products,
            )

    # ========================================================
    # PHASE 2 - EXISTING PRODUCT PRICE TRACKING
    # ========================================================

    def spider_idle(self, spider):
        if spider is not self or self.price_tracking_started:
            return
        self.price_tracking_started = True
        self.phase1_completed = True
        self.logger.info("=" * 70)
        self.logger.info(
            "PHASE 1 COMPLETED | New products added/sent to pipeline: %s | Existing products found in scrape: %s | Existing products in DB: %s",
            self.new_products_added,
            self.existing_products_found,
            len(self.existing_products),
        )
        self.logger.info("STARTING PHASE 2 - EXISTING PRODUCT PRICE TRACKING | Products: %s", len(self.existing_price_products))
        self.logger.info("=" * 70)
        if not self.existing_price_products:
            self.price_tracking_completed = True
            return
        self.price_total = len(self.existing_price_products)
        for index, existing in enumerate(self.existing_price_products, start=1):
            self.crawler.engine.crawl(scrapy.Request(
                url=existing["product_link"],
                callback=self.parse_existing_product_price,
                cb_kwargs={"existing": existing, "index": index},
                headers={**HEADERS, "User-Agent": USER_AGENTS[(index - 1) % len(USER_AGENTS)]},
                dont_filter=True,
            ))
        raise DontCloseSpider

    def parse_existing_product_price(self, response, existing, index):
        product_name = existing.get("name") or "Unknown"
        product_link = existing.get("product_link") or response.url

        try:
            self.logger.info(
                "PRICE TRACKING %s/%s | %s",
                index,
                self.price_total,
                product_name,
            )

            database_price = existing.get("price")

            if response.status != 200:
                self.price_failed += 1
                self.processing_failed += 1

                failed_product = {
                    "product_id": existing.get("id"),
                    "name": product_name,
                    "database_price": database_price,
                    "price": None,
                    "original_price": None,
                    "currency": "₹",
                    "discount": None,
                    "ratings": existing.get("ratings"),
                    "description": existing.get("description") or "N/A",
                    "image_link": existing.get("image_link") or "N/A",
                    "product_link": product_link,
                    "scrape_url": get_scrape_url(product_link),
                    "organization_id": ORGANIZATION_NAME,
                    "store_id": STORE_NAME,
                    "store_name": STORE_NAME,
                    "categories_id": existing.get("categories_id"),
                    "affiliate_url": existing.get("affiliate_url"),
                    "created_at": get_created_at(),
                    "status": "failed",
                    "http_status": response.status,
                    "price_changed": None,
                    "price_difference": None,
                }

                save_to_single_json(failed_product)

                self.logger.warning(
                    "PRICE TRACKING FAILED | %s | HTTP %s",
                    product_name,
                    response.status,
                )
                return

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            # Phase 2 uses PRODUCT DETAIL PAGE selectors only.
            price = extract_detail_price(soup)
            original_price = extract_detail_original_price(soup)
            discount = extract_detail_discount(
                soup,
                price,
                original_price,
            )
            rating = extract_detail_rating(soup)

            if rating is None:
                try:
                    _, detail_rating = asyncio.run(
                        self.fetch_detail_data(product_link)
                    )

                    if detail_rating is not None:
                        rating = detail_rating

                except Exception as exc:
                    self.logger.warning(
                        "RATING FALLBACK FAILED | %s | %s",
                        product_name,
                        exc,
                    )

            if price is None:
                self.price_failed += 1
                self.processing_failed += 1

                failed_product = {
                    "product_id": existing.get("id"),
                    "name": product_name,
                    "database_price": database_price,
                    "price": None,
                    "original_price": original_price,
                    "currency": "₹",
                    "discount": discount,
                    "ratings": rating,
                    "description": existing.get("description") or "N/A",
                    "image_link": existing.get("image_link") or "N/A",
                    "product_link": product_link,
                    "scrape_url": get_scrape_url(product_link),
                    "organization_id": ORGANIZATION_NAME,
                    "store_id": STORE_NAME,
                    "store_name": STORE_NAME,
                    "categories_id": existing.get("categories_id"),
                    "affiliate_url": existing.get("affiliate_url"),
                    "created_at": get_created_at(),
                    "status": "failed",
                    "http_status": response.status,
                    "price_changed": None,
                    "price_difference": None,
                }

                save_to_single_json(failed_product)

                self.logger.warning(
                    "PRICE TRACKING FAILED | PRICE NOT FOUND | %s | %s",
                    product_name,
                    product_link,
                )
                return

            if rating is not None:
                rating = round(float(rating), 2)

            # Compare the newly scraped price with the DB price.
            try:
                price_changed = (
                    float(database_price) != float(price)
                    if database_price is not None
                    else True
                )

                price_difference = (
                    float(price) - float(database_price)
                    if database_price is not None
                    else None
                )

                if price_difference is not None and price_difference.is_integer():
                    price_difference = int(price_difference)

            except (TypeError, ValueError):
                price_changed = True
                price_difference = None

            if price_changed:
                self.products_updated += 1
                self.logger.info(
                    "PRODUCT UPDATED | %s | Old Price: %s | New Price: %s | Difference: %s",
                    product_name,
                    database_price,
                    price,
                    price_difference,
                )
            else:
                self.products_unchanged += 1
                self.logger.info(
                    "PRODUCT UNCHANGED | %s | Price: %s | No update required",
                    product_name,
                    price,
                )

            tracked_product = {
                "product_id": existing.get("id"),
                "name": product_name,
                "database_price": database_price,
                "price": price,
                "original_price": original_price,
                "currency": "₹",
                "discount": discount,
                "ratings": rating,
                "description": existing.get("description") or "N/A",
                "image_link": existing.get("image_link") or "N/A",
                "product_link": product_link,
                "scrape_url": get_scrape_url(product_link),
                "organization_id": ORGANIZATION_NAME,
                "store_id": STORE_NAME,
                "store_name": STORE_NAME,
                "categories_id": existing.get("categories_id"),
                "affiliate_url": existing.get("affiliate_url"),
                "created_at": get_created_at(),
                "status": "success",
                "http_status": response.status,
                "price_changed": price_changed,
                "price_difference": price_difference,
            }

            self.price_success += 1
            self.price_updates.append(tracked_product)

            # All products go to the SAME JSON file.
            save_to_single_json(tracked_product)

            self.logger.info(
                "EXISTING PRODUCT PRICE SCRAPED | %s | Price: ₹%s | Original: ₹%s | Discount: %s%% | Rating: %s",
                product_name,
                price,
                original_price,
                discount,
                rating,
            )

            # Send existing product through the existing DealWallet pipeline.
            yield {
                "product_id": existing.get("id"),
                "name": product_name,
                "price": price,
                "original_price": original_price,
                "discount": discount,
                "currency": "₹",
                "ratings": rating,
                "description": existing.get("description") or "N/A",
                "image_link": existing.get("image_link") or "N/A",
                "product_link": product_link,
                "organization_id": ORGANIZATION_NAME,
                "store_id": STORE_NAME,
                "categories_id": existing.get("categories_id"),
                "affiliate_url": existing.get("affiliate_url"),
                "created_at": get_created_at(),
            }

        except Exception as exc:
            self.price_failed += 1
            self.processing_failed += 1

            self.logger.exception(
                "PRICE TRACKING ERROR | %s | %s | Error: %s",
                product_name,
                product_link,
                exc,
            )

    # ========================================================
    # CLOSED
    # ========================================================

    def closed(self, reason):
        # Scrapy has finished processing yielded items, so the existing
        # DealWallet pipeline has completed its database work.
        #
        # IMPORTANT:
        # self.new_products_added is the number of new products that were
        # successfully scraped and yielded to the pipeline. Do NOT overwrite
        # it with the database reconciliation count.
        #
        # Reconcile newly inserted records only to enrich the JSON with the
        # actual PostgreSQL UUID/database price.
        confirmed_new_products = finalize_new_products_in_json()

        self.logger.info("=" * 70)
        self.logger.info("WRAPCART SCRAPER COMPLETED | Reason: %s", reason)

        for category, count in self.category_counts.items():
            self.logger.info(
                "CATEGORY RESULT | %s: %s",
                category,
                count,
            )

        json_path = Path(OUTPUT_FILE).resolve()

        self.logger.info("=" * 70)
        self.logger.info("FINAL PRODUCT PROCESSING SUMMARY")
        self.logger.info("=" * 70)
        self.logger.info(
            "New product candidates             : %s",
            self.new_candidates,
        )
        self.logger.info(
            "New products scraped successfully  : %s",
            self.new_products_added,
        )
        self.logger.info(
            "New products sent to pipeline      : %s",
            self.new_products_added,
        )
        self.logger.info(
            "New products confirmed in database: %s",
            confirmed_new_products,
        )
        self.logger.info(
            "Existing products in database      : %s",
            len(self.existing_products),
        )
        self.logger.info(
            "Existing products found in scrape  : %s",
            self.existing_products_found,
        )
        self.logger.info(
            "Existing products price checked    : %s",
            self.price_total,
        )
        self.logger.info(
            "Products updated                   : %s",
            self.products_updated,
        )
        self.logger.info(
            "Products unchanged                 : %s",
            self.products_unchanged,
        )
        self.logger.info(
            "New products skipped - price      : %s",
            self.new_skipped_price,
        )
        self.logger.info(
            "New products without original price: %s",
            self.new_skipped_original_price,
        )
        self.logger.info(
            "New products without discount     : %s",
            self.new_skipped_discount,
        )
        self.logger.info(
            "New products without description  : %s",
            self.new_skipped_detail,
        )
        self.logger.info(
            "Processing failed                  : %s",
            self.processing_failed,
        )
        self.logger.info(
            "Price tracking successful          : %s",
            self.price_success,
        )
        self.logger.info(
            "Price tracking failed              : %s",
            self.price_failed,
        )
        self.logger.info("=" * 70)
        self.logger.info(
            "SINGLE JSON FILE | %s | EXISTS: %s",
            json_path,
            json_path.exists(),
        )

        if json_path.exists():
            self.logger.info(
                "JSON FILE SIZE: %s bytes",
                json_path.stat().st_size,
            )

        self.logger.info("=" * 70)
