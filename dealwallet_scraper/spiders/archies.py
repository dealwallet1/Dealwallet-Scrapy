
import asyncio
import csv
import json
import logging
import re
import sys

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import (
    urljoin,
    urlparse,
    urldefrag,
    urlunparse,
    parse_qsl,
    urlencode,
)

import scrapy
from bs4 import BeautifulSoup

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)


# ==========================================================
# CONFIGURATION
# ==========================================================

BASE_URL = "https://archiesonline.com"

PRODUCTS_PER_CATEGORY = 120
TOTAL_TARGET = 120
MAX_LISTING_PAGES = 10

OUTPUT_JSON = "archies_products.json"
OUTPUT_CSV = "archies_products.csv"
LOG_FILE = "archies_scraper.log"

COLLECTIONS = [
    {
        "url": "https://archiesonline.com/collections/necklaces",
        "category": "Beauty",
        "target": PRODUCTS_PER_CATEGORY,
    },
    {
        "url": "https://archiesonline.com/collections/trendy-jewellery",
        "category": "Beauty",
        "target": PRODUCTS_PER_CATEGORY,
    },
]

# DO NOT CHANGE THESE FIELDS

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


# ==========================================================
# LOGGING
# ==========================================================

def configure_logging():

    logger = logging.getLogger("archies_scraper")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )

        file_handler = logging.FileHandler(
            LOG_FILE,
            encoding="utf-8"
        )

        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


# ==========================================================
# COMMON HELPERS
# ==========================================================

def clean_text(value):

    if value is None:
        return None

    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value or None


def normalize_url(url, base=BASE_URL):

    if not url:
        return None

    url = str(url).strip()

    if url.startswith("//"):
        url = "https:" + url
    else:
        url = urljoin(base, url)

    url, _ = urldefrag(url)

    return url


def is_product_url(url):

    if not url:
        return False

    parsed = urlparse(url)

    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc.lower().endswith("archiesonline.com")
        and "/products/" in parsed.path
    )


def number_to_int(value):

    if value is None or isinstance(value, bool):
        return None

    try:

        match = re.search(
            r"\d+(?:\.\d+)?",
            str(value).replace(",", "")
        )

        if not match:
            return None

        number = Decimal(match.group(0))

        if not number.is_finite() or number <= 0:
            return None

        return int(number)

    except (InvalidOperation, ValueError, TypeError):
        return None


def shopify_cents_to_int(value):

    """
    Shopify embedded product JSON stores prices in paise/cents.
    Example: 61500 becomes 615.
    """

    if value is None or isinstance(value, bool):
        return None

    try:

        number = Decimal(str(value))

        if not number.is_finite() or number <= 0:
            return None

        return int(number / Decimal("100"))

    except (InvalidOperation, ValueError, TypeError):
        return None


def choose_src_from_srcset(srcset):

    if not srcset:
        return None

    entries = [
        entry.strip()
        for entry in str(srcset).split(",")
        if entry.strip()
    ]

    parsed_entries = []

    for entry in entries:

        parts = entry.split()

        if not parts:
            continue

        width_match = (
            re.search(r"(\d+)w$", parts[1])
            if len(parts) > 1 else None
        )

        width = int(width_match.group(1)) if width_match else 0

        parsed_entries.append((width, parts[0]))

    return (
        max(parsed_entries, key=lambda item: item[0])[1]
        if parsed_entries else None
    )


def get_image_url(img):

    if not img:
        return None

    for attr in (
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-srcset",
    ):

        value = img.get(attr)

        if value and not str(value).startswith("data:image"):

            if attr == "data-srcset":
                return choose_src_from_srcset(value)

            return value

    return choose_src_from_srcset(img.get("srcset"))


def extract_rating(element):

    if not element:
        return None

    candidates = [
        element.get("data-average-rating"),
        element.get("data-score"),
    ]

    rating_element = element.select_one(
        ".jdgm-prev-badge__stars[data-score], "
        "[data-average-rating], [data-score]"
    )

    if rating_element:

        candidates.extend([
            rating_element.get("data-average-rating"),
            rating_element.get("data-score"),
        ])

    for candidate in candidates:

        if candidate is None:
            continue

        match = re.search(
            r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
            str(candidate)
        )

        if match:

            rating = float(match.group(1))

            if 0 < rating <= 5:
                return round(rating, 1)

    return None


# ==========================================================
# EMBEDDED HTML JSON / JSON-LD
# No separate API requests
# ==========================================================

def parse_shopify_product_data(element):

    if not element:
        return {}

    raw = element.get("data-json-product")

    if not raw:
        return {}

    try:

        value = json.loads(raw)

        return value if isinstance(value, dict) else {}

    except (json.JSONDecodeError, TypeError):
        return {}


def find_product_schema(obj):

    if isinstance(obj, list):

        for item in obj:

            found = find_product_schema(item)

            if found:
                return found

    elif isinstance(obj, dict):

        types = obj.get("@type", [])

        if isinstance(types, str):
            types = [types]

        if any(str(item).lower() == "product" for item in types):
            return obj

        for value in obj.values():

            if isinstance(value, (list, dict)):

                found = find_product_schema(value)

                if found:
                    return found

    return {}


def extract_jsonld_product(soup):

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except (json.JSONDecodeError, TypeError):
            continue

        product = find_product_schema(data)

        if product:
            return product

    return {}


def get_product_json_from_soup(soup):

    for element in soup.select("[data-json-product]"):

        data = parse_shopify_product_data(element)

        if data:
            return data

    return {}


# ==========================================================
# PRODUCT PRICE EXTRACTION
# ==========================================================

def extract_product_prices(soup, schema_product=None):

    current_price = None
    original_price = None

    current_selectors = [
        ".price__sale .price-item--sale",
        ".price__last .price-item--sale",
        ".price__regular .price__last .price-item--regular",
        ".productView-price .price-item--sale",
        ".productView-price .price-item--regular",
    ]

    original_selectors = [
        ".price__compare .price-item--regular",
        ".price__compare",
        ".productView-price .price-item--regular s",
        "s.price-item--regular",
    ]

    for selector in current_selectors:

        element = soup.select_one(selector)

        if element:

            current_price = number_to_int(
                element.get_text(" ", strip=True)
            )

            if current_price is not None:
                break

    for selector in original_selectors:

        element = soup.select_one(selector)

        if element:

            original_price = number_to_int(
                element.get_text(" ", strip=True)
            )

            if original_price is not None:
                break

    if schema_product:

        offers = schema_product.get("offers", [])

        if isinstance(offers, dict):
            offers = [offers]

        for offer in offers:

            if not isinstance(offer, dict):
                continue

            if current_price is None:
                current_price = number_to_int(
                    offer.get("price")
                )

            if original_price is None:
                original_price = number_to_int(
                    offer.get("highPrice")
                )

            if current_price is not None:
                break

    return current_price, original_price


def extract_product_discount(soup):

    selectors = [
        ".price__badge-sale",
        ".badge--sale",
        ".product__badge",
        ".product-badge",
        ".sale-badge",
        ".discount-badge",
        "[class*='discount']",
        "[class*='sale-badge']",
        "[class*='badge']",
    ]

    for selector in selectors:

        for element in soup.select(selector):

            label = element.get_text(" ", strip=True)

            match = re.search(
                r"(?<!\d)(\d{1,3})\s*%",
                label or ""
            )

            if match:

                value = int(match.group(1))

                if 0 <= value <= 100:
                    return value

    return None


# ==========================================================
# DESCRIPTION EXTRACTION
# ==========================================================

def extract_description(
    soup,
    schema_product=None,
    listing_description=None
):

    if schema_product:

        description = clean_text(
            schema_product.get("description")
        )

        if description and len(description) > 25:
            return description

    selectors = [
        ".productView-description .rte",
        ".productView-description",
        "[data-tab-content*='description']",
        "#description",
        ".product-single__description",
        ".product__description",
        ".product-description",
        '[itemprop="description"]',
        '[class*="description-content"]',
        '[id*="description"]',
    ]

    for selector in selectors:

        for element in soup.select(selector):

            description = clean_text(
                element.get_text(" ", strip=True)
            )

            if not description:
                continue

            if len(description) <= 25 or len(description) > 15000:
                continue

            lowered = description.lower()

            if (
                "your cart is empty" in lowered
                or "customer service" in lowered
                or lowered in {
                    "description",
                    "delivery information",
                    "reviews",
                }
            ):
                continue

            return description

    # Description label fallback

    for label in soup.find_all(
        ["a", "button", "h2", "h3", "h4", "span"]
    ):

        label_text = clean_text(
            label.get_text(" ", strip=True)
        )

        if label_text and label_text.lower() == "description":

            for sibling in label.find_next_siblings(limit=5):

                text = clean_text(
                    sibling.get_text(" ", strip=True)
                )

                if text and 40 < len(text) < 15000:
                    return text

    description = clean_text(listing_description)

    if description and len(description) > 15:
        return description

    return None


# ==========================================================
# IMAGE AND RATING EXTRACTION
# ==========================================================

def extract_product_image(soup):

    meta = soup.select_one(
        'meta[property="og:image"]'
    )

    if meta and meta.get("content"):
        return meta.get("content")

    selectors = [
        ".productView-image img",
        ".productView-thumbnail img",
        ".product-single__media img",
        ".product__media img",
        '[class*="product-gallery"] img',
        '[class*="product-image"] img',
    ]

    for selector in selectors:

        img = soup.select_one(selector)

        src = get_image_url(img)

        if src:
            return src

    return None


def extract_product_rating(soup):

    rating_el = (
        soup.select_one(".jdgm-prev-badge")
        or soup.select_one(".jdgm-prev-badge__stars")
    )

    return extract_rating(rating_el)


# ==========================================================
# LISTING PAGE EXTRACTION
# ==========================================================

def parse_listing_products(html, logger):

    soup = BeautifulSoup(
        html or "",
        "html.parser"
    )

    cards = soup.select(
        "#product-grid li.grid__item, "
        "#product-grid li.product, "
        "#CollectionProductGrid li.grid__item, "
        "#CollectionProductGrid li.product, "
        ".productGrid li.product, "
        ".product-grid li.product, "
        "li.grid__item, "
        "li.product"
    )

    source = "product card selectors"

    # Fallback when product card selectors return nothing

    if not cards:

        source = "product link ancestor fallback"

        cards = []
        seen_card_ids = set()

        product_links = soup.select(
            'a[href*="/products/"]'
        )

        for link in product_links:

            product_url = normalize_url(link.get("href"))

            if not is_product_url(product_url):
                continue

            best_card = None
            best_score = -1

            for ancestor in list(link.parents)[:9]:

                classes = " ".join(
                    ancestor.get("class", [])
                )

                identity = (
                    classes + " " + ancestor.get("id", "")
                ).lower()

                has_price = bool(
                    ancestor.select_one(
                        ".price, [class*='price'], "
                        "[data-price], [data-product-price]"
                    )
                )

                has_image = bool(
                    ancestor.select_one("img")
                )

                has_heading = bool(
                    ancestor.select_one(
                        "h2, h3, h4, "
                        "[class*='title'], [class*='name']"
                    )
                )

                product_class = any(
                    word in identity
                    for word in (
                        "product",
                        "card",
                        "grid-item"
                    )
                )

                score = (
                    (5 if product_class else 0)
                    + (3 if has_price else 0)
                    + (2 if has_image else 0)
                    + (2 if has_heading else 0)
                )

                if score > best_score:

                    best_score = score
                    best_card = ancestor

                if product_class and (
                    has_price or has_image or has_heading
                ):

                    best_card = ancestor
                    break

            if (
                best_card is not None
                and id(best_card) not in seen_card_ids
            ):

                seen_card_ids.add(id(best_card))
                cards.append(best_card)

    logger.info("LISTING SELECTOR: %s", source)

    logger.info(
        "PRODUCT LINKS FOUND: %s",
        len(soup.select('a[href*="/products/"]'))
    )

    logger.info("PRODUCT CARDS FOUND: %s", len(cards))

    products = {}

    for card in cards:

        item = (
            card.select_one(
                ".product-item.enable_custom_position_swatch"
            )
            or card.select_one(".product-item")
            or card
        )

        shopify_data = parse_shopify_product_data(item)

        if not shopify_data:

            for ancestor in list(card.parents)[:3]:

                shopify_data = parse_shopify_product_data(
                    ancestor
                )

                if shopify_data:
                    break

        # NAME

        name_el = (
            card.select_one("h3.card__heading a.card-title")
            or card.select_one("h2 a[href*='/products/']")
            or card.select_one("h3 a[href*='/products/']")
            or card.select_one("a[href*='/products/']")
            or card.select_one("[class*='product-title']")
            or card.select_one("[class*='card-title']")
        )

        name = clean_text(
            (
                name_el.get("data-product-title")
                if name_el else None
            )
            or (
                name_el.get("title")
                if name_el else None
            )
            or (
                name_el.get_text(" ", strip=True)
                if name_el else None
            )
            or shopify_data.get("name")
        )

        # PRODUCT URL

        link_el = card.select_one(
            "a[href*='/products/']"
        )

        product_url = (
            normalize_url(link_el.get("href"))
            if link_el and link_el.get("href")
            else None
        )

        if not is_product_url(product_url):

            handle = shopify_data.get("handle")

            if handle:
                product_url = normalize_url(
                    f"/products/{handle}"
                )

        if not is_product_url(product_url):
            continue

        if not name and link_el:

            name = clean_text(
                link_el.get("aria-label")
                or link_el.get("title")
                or link_el.get_text(" ", strip=True)
            )

        if not name:

            image_for_name = card.select_one("img[alt]")

            if image_for_name:
                name = clean_text(
                    image_for_name.get("alt")
                )

        # SELLING PRICE

        price = None

        for selector in (
            ".price__sale .price-item--sale",
            ".price__regular .price__last .price-item--regular",
            ".card-price .price__last .price-item--regular",
            "[class*='price-item--sale']",
            "[class*='price-item--regular']",
            "[class*='price']",
        ):

            price_el = card.select_one(selector)

            if price_el:

                price = number_to_int(
                    price_el.get_text(" ", strip=True)
                )

                if price is not None:
                    break

        # ORIGINAL PRICE

        original_price = None

        for selector in (
            ".price__compare .price-item--regular",
            ".price__compare",
            "s[class*='price']",
            "del[class*='price']",
            "[class*='compare-at']",
        ):

            original_el = card.select_one(selector)

            if original_el:

                original_price = number_to_int(
                    original_el.get_text(" ", strip=True)
                )

                if original_price is not None:
                    break

        # EMBEDDED PRODUCT DATA FALLBACK

        variants = shopify_data.get("variants") or []

        if isinstance(variants, dict):
            variants = [variants]

        for variant in variants:

            if not isinstance(variant, dict):
                continue

            if price is None:
                price = shopify_cents_to_int(
                    variant.get("price")
                )

            if original_price is None:
                original_price = shopify_cents_to_int(
                    variant.get("compare_at_price")
                )

            if price is not None:
                break

        # RUPEE TEXT FALLBACK

        if price is None:

            rupee_values = re.findall(
                r"₹\s*[\d,]+(?:\.\d{1,2})?",
                card.get_text(" ", strip=True)
            )

            parsed_values = [
                number_to_int(value)
                for value in rupee_values
            ]

            parsed_values = [
                value for value in parsed_values
                if value is not None
            ]

            if parsed_values:

                price = min(parsed_values)

                if len(parsed_values) > 1:
                    original_price = max(parsed_values)

        # LISTING DESCRIPTION

        description_el = (
            card.select_one(".card-summary.card-list__hidden")
            or card.select_one("[class*='description']")
            or card.select_one("[class*='summary']")
        )

        listing_description = (
            clean_text(
                description_el.get_text(" ", strip=True)
            )
            if description_el else None
        )

        # IMAGE

        image_el = (
            card.select_one(".card-media img")
            or card.select_one("img")
        )

        image_url = (
            normalize_url(
                get_image_url(image_el),
                product_url
            )
            if image_el else None
        )

        # RATING

        rating_el = (
            card.select_one(".jdgm-prev-badge")
            or card.select_one(".jdgm-prev-badge__stars")
        )

        rating = extract_rating(rating_el)

        products[product_url] = {
            "name": name,
            "product_link": product_url,
            "price": price,
            "original_price": original_price,
            "ratings": rating,
            "image_link": image_url,
            "listing_description": listing_description,
        }

    return list(products.values())


# ==========================================================
# PRODUCT PAGE EXTRACTION
# ==========================================================

def extract_product_page(html, product_url, listing):

    soup = BeautifulSoup(
        html or "",
        "html.parser"
    )

    schema_product = extract_jsonld_product(soup)

    product_data = get_product_json_from_soup(soup)

    h1 = soup.select_one("h1")

    name = (
        clean_text(h1.get_text(" ", strip=True))
        if h1 else listing.get("name")
    )

    if not name:
        name = clean_text(schema_product.get("name"))

    # PRODUCT PAGE PRICE

    page_price, page_original_price = (
        extract_product_prices(
            soup,
            schema_product
        )
    )

    # DESCRIPTION

    description = extract_description(
        soup,
        schema_product,
        listing.get("listing_description")
    )

    # IMAGE

    image_url = (
        extract_product_image(soup)
        or listing.get("image_link")
    )

    if image_url:

        image_url = normalize_url(
            image_url,
            product_url
        )

    # RATINGS

    ratings = (
        listing.get("ratings")
        if listing.get("ratings") is not None
        else extract_product_rating(soup)
    )

    # EMBEDDED HTML PRODUCT DATA FALLBACK

    if page_price is None and product_data:

        variants = product_data.get("variants") or []

        if isinstance(variants, dict):
            variants = [variants]

        available = [
            variant for variant in variants
            if isinstance(variant, dict)
            and variant.get("available") is True
        ]

        ordered = available + [
            variant for variant in variants
            if isinstance(variant, dict)
            and variant not in available
        ]

        for variant in ordered:

            page_price = shopify_cents_to_int(
                variant.get("price")
            )

            page_original_price = shopify_cents_to_int(
                variant.get("compare_at_price")
            )

            if page_price is not None:
                break

    return {
        "name": name,
        "page_price": page_price,
        "page_original_price": page_original_price,
        "description": description,
        "image_link": image_url,
        "ratings": ratings,
    }


# ==========================================================
# PAGINATION
# ==========================================================

def build_collection_page_url(collection_url, page_number):

    if page_number <= 1:
        return collection_url

    parsed = urlparse(collection_url)

    query_items = parse_qsl(
        parsed.query,
        keep_blank_values=True
    )

    query_items = [
        (key, value)
        for key, value in query_items
        if key.lower() != "page"
    ]

    query_items.append(
        ("page", str(page_number))
    )

    return urlunparse(
        parsed._replace(
            query=urlencode(query_items, doseq=True)
        )
    )


# ==========================================================
# CRAWL4AI SCRAPY SPIDER
# ==========================================================

class ArchiesSpider(scrapy.Spider):

    name = "archies"

    allowed_domains = ["archiesonline.com"]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_TIMEOUT": 120,
        "ROBOTSTXT_OBEY": False,
        "LOG_LEVEL": "INFO",
        "TELNETCONSOLE_ENABLED": False,
    }

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.logger_extra = configure_logging()

        self.all_results = []

        self.seen_product_urls = set()
        self.seen_listing_urls = set()

        self.category_counts = {}
        self.category_targets = {}

        self.stats = {
            "collections_started": 0,
            "listing_pages_crawled": 0,
            "products_found": 0,
            "product_pages_crawled": 0,
            "products_saved": 0,
            "products_skipped": 0,
            "crawl_failures": 0,
        }

        for collection in COLLECTIONS:

            category = collection["category"]

            self.category_targets[category] = max(
                self.category_targets.get(category, 0),
                int(
                    collection.get(
                        "target",
                        PRODUCTS_PER_CATEGORY
                    )
                )
            )

        self.browser_config = BrowserConfig(
            browser_type="chromium",
            headless=True,
            verbose=False,
        )

        self.run_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=120000,
            wait_until="domcontentloaded",
        )

    # ------------------------------------------------------
    # WINDOWS PROACTOR EVENT LOOP FIX
    # ------------------------------------------------------

    def run_crawl_in_browser_thread(self):

        """
        Runs Crawl4AI in a separate thread.

        Windows Playwright requires a Proactor event loop
        because it launches browser subprocesses.

        Scrapy uses AsyncioSelectorReactor, so the browser
        must not be launched in Scrapy's event loop.
        """

        if sys.platform == "win32":

            loop = asyncio.ProactorEventLoop()

        else:

            loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

        try:

            return loop.run_until_complete(
                self.run_crawl()
            )

        finally:

            try:

                loop.run_until_complete(
                    loop.shutdown_asyncgens()
                )

            except Exception:

                self.logger_extra.exception(
                    "ASYNC GENERATOR SHUTDOWN ERROR"
                )

            finally:

                loop.close()
                asyncio.set_event_loop(None)

    # ------------------------------------------------------
    # SCRAPY START
    # ------------------------------------------------------

    async def start(self):

        self.logger_extra.info(
            "========== ARCHIES CRAWLER STARTED =========="
        )

        self.logger_extra.info(
            "TOTAL TARGET: %s",
            TOTAL_TARGET
        )

        self.logger_extra.info(
            "COLLECTIONS CONFIGURED: %s",
            len(COLLECTIONS)
        )

        try:

            # Run Crawl4AI in a worker thread with its own
            # Windows Proactor event loop.

            results = await asyncio.to_thread(
                self.run_crawl_in_browser_thread
            )

            for record in results:
                yield record

        except Exception:

            self.logger_extra.exception(
                "SCRAPY START ERROR"
            )

            self.save_outputs()
            self.log_summary()

    # ------------------------------------------------------
    # ACTUAL ASYNC CRAWL
    # Runs only inside the browser worker thread
    # ------------------------------------------------------

    async def run_crawl(self):

        try:

            async with AsyncWebCrawler(
                config=self.browser_config
            ) as crawler:

                for collection in COLLECTIONS:

                    if len(self.all_results) >= TOTAL_TARGET:
                        break

                    self.stats["collections_started"] += 1

                    collection_url = collection["url"]
                    category = collection["category"]

                    self.logger_extra.info(
                        "STARTING COLLECTION: %s | CATEGORY: %s",
                        collection_url,
                        category
                    )

                    await self.scrape_collection(
                        crawler,
                        collection
                    )

        except Exception:

            self.logger_extra.exception(
                "CRAWLER START ERROR"
            )

        finally:

            self.save_outputs()
            self.log_summary()

        return self.all_results

    # ------------------------------------------------------
    # COLLECTION SCRAPING
    # ------------------------------------------------------

    async def scrape_collection(self, crawler, collection):

        collection_url = collection["url"]
        category = collection["category"]

        target = int(
            collection.get(
                "target",
                PRODUCTS_PER_CATEGORY
            )
        )

        for page_number in range(1, MAX_LISTING_PAGES + 1):

            if len(self.all_results) >= TOTAL_TARGET:
                break

            if self.category_counts.get(category, 0) >= target:
                break

            listing_url = build_collection_page_url(
                collection_url,
                page_number
            )

            if listing_url in self.seen_listing_urls:

                self.logger_extra.info(
                    "DUPLICATE LISTING PAGE SKIPPED: %s",
                    listing_url
                )

                break

            self.seen_listing_urls.add(listing_url)

            self.logger_extra.info(
                "CRAWLING LISTING PAGE %s: %s",
                page_number,
                listing_url
            )

            try:

                result = await crawler.arun(
                    url=listing_url,
                    config=self.run_config
                )

                if not result.success:

                    self.stats["crawl_failures"] += 1

                    self.logger_extra.error(
                        "LISTING CRAWL FAILED: %s | %s",
                        listing_url,
                        getattr(
                            result,
                            "error_message",
                            "Unknown error"
                        )
                    )

                    continue

                self.stats["listing_pages_crawled"] += 1

                html = (
                    result.html
                    or getattr(result, "cleaned_html", "")
                    or ""
                )

                products = parse_listing_products(
                    html,
                    self.logger_extra
                )

                self.logger_extra.info(
                    "LISTING PRODUCTS FOUND: %s",
                    len(products)
                )

                if not products:

                    self.logger_extra.warning(
                        "NO PRODUCTS FOUND. STOPPING COLLECTION PAGE: %s",
                        listing_url
                    )

                    break

                new_products = []

                for product in products:

                    product_url = product.get("product_link")

                    if not is_product_url(product_url):
                        continue

                    parsed = urlparse(product_url)

                    product_url = urlunparse(
                        parsed._replace(
                            query="",
                            fragment=""
                        )
                    )

                    if product_url in self.seen_product_urls:
                        continue

                    self.seen_product_urls.add(product_url)

                    product["product_link"] = product_url

                    new_products.append(product)

                self.stats["products_found"] += len(new_products)

                self.logger_extra.info(
                    "NEW PRODUCT URLS: %s",
                    len(new_products)
                )

                if not new_products:

                    self.logger_extra.warning(
                        "NO NEW PRODUCTS ON PAGE %s",
                        page_number
                    )

                    break

                for listing in new_products:

                    if len(self.all_results) >= TOTAL_TARGET:
                        break

                    if self.category_counts.get(category, 0) >= target:
                        break

                    await self.scrape_product(
                        crawler,
                        listing,
                        category
                    )

                self.logger_extra.info(
                    "COLLECTION PROGRESS: %s/%s",
                    self.category_counts.get(category, 0),
                    target
                )

            except Exception:

                self.stats["crawl_failures"] += 1

                self.logger_extra.exception(
                    "COLLECTION ERROR: %s",
                    listing_url
                )

    # ------------------------------------------------------
    # PRODUCT SCRAPING
    # ------------------------------------------------------

    async def scrape_product(
        self,
        crawler,
        listing,
        category
    ):

        product_url = listing["product_link"]

        self.logger_extra.info(
            "CRAWLING PRODUCT: %s",
            product_url
        )

        try:

            result = await crawler.arun(
                url=product_url,
                config=self.run_config
            )

            if not result.success:

                self.stats["crawl_failures"] += 1
                self.stats["products_skipped"] += 1

                self.logger_extra.error(
                    "PRODUCT CRAWL FAILED: %s | %s",
                    product_url,
                    getattr(
                        result,
                        "error_message",
                        "Unknown error"
                    )
                )

                return

            self.stats["product_pages_crawled"] += 1

            html = (
                result.html
                or getattr(result, "cleaned_html", "")
                or ""
            )

            page_data = extract_product_page(
                html,
                product_url,
                listing
            )

            self.finalize_product(
                listing,
                page_data,
                category,
                product_url
            )

        except Exception:

            self.stats["crawl_failures"] += 1
            self.stats["products_skipped"] += 1

            self.logger_extra.exception(
                "PRODUCT EXTRACTION ERROR: %s",
                product_url
            )

    # ------------------------------------------------------
    # FINAL VALIDATION AND SAVE
    # ------------------------------------------------------

    def finalize_product(
        self,
        listing,
        page_data,
        category,
        product_url
    ):

        if len(self.all_results) >= TOTAL_TARGET:
            return

        # PRICE PRIORITY:
        # 1. Product page HTML
        # 2. Collection listing HTML

        price = page_data.get("page_price")

        if price is None:
            price = listing.get("price")

        original_price = page_data.get(
            "page_original_price"
        )

        if original_price is None:
            original_price = listing.get("original_price")

        # SELLING PRICE VALIDATION

        if price is None or price <= 0:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: SELLING PRICE MISSING OR INVALID: %s",
                product_url
            )

            return

        price = int(price)

        # ORIGINAL PRICE VALIDATION

        if original_price is None:
            original_price = price

        original_price = int(original_price)

        if original_price <= 0:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: ORIGINAL PRICE INVALID: %s",
                product_url
            )

            return

        if original_price < price:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: ORIGINAL PRICE LESS THAN SELLING PRICE | "
                "SELLING=%s | ORIGINAL=%s | URL=%s",
                price,
                original_price,
                product_url
            )

            return

        # CALCULATE DISCOUNT FROM ACTUAL PRICES

        calculated_discount = round(
            ((original_price - price) / original_price) * 100
        )

        # PRESERVE DISCOUNTED-PRODUCTS-ONLY RULE

        if calculated_discount <= 0:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: ZERO DISCOUNT: %s",
                product_url
            )

            return

        if calculated_discount > 100:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: INVALID DISCOUNT: %s",
                product_url
            )

            return

        discount = calculated_discount

        # DESCRIPTION VALIDATION

        description = clean_text(
            page_data.get("description")
        )

        if not description:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: DESCRIPTION MISSING: %s",
                product_url
            )

            return

        # NAME VALIDATION

        name = (
            clean_text(page_data.get("name"))
            or clean_text(listing.get("name"))
        )

        if not name:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: PRODUCT NAME MISSING: %s",
                product_url
            )

            return

        # IMAGE

        image_url = (
            page_data.get("image_link")
            or listing.get("image_link")
        )

        if image_url:

            image_url = normalize_url(
                image_url,
                product_url
            )

        else:

            self.logger_extra.warning(
                "IMAGE MISSING: %s",
                product_url
            )

        # RATING

        ratings = page_data.get("ratings")

        # DUPLICATE VALIDATION

        if any(
            item["product_link"] == product_url
            for item in self.all_results
        ):

            self.logger_extra.warning(
                "DUPLICATE PRODUCT SKIPPED: %s",
                product_url
            )

            return

        # FINAL RECORD
        # ALL ORIGINAL 13 FIELDS ARE PRESERVED

        record = {
            "name": name,
            "price": price,
            "currency": "₹",
            "original_price": original_price,
            "discount": int(discount),
            "ratings": ratings,
            "description": description,
            "image_link": image_url,
            "product_link": product_url,
            "organization_id": "Dealwallet",
            "store_id": "Archies",
            "categories_id": category,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        # VALIDATE OUTPUT FIELDS

        missing_fields = [
            field
            for field in FIELDS
            if record.get(field) is None
            or record.get(field) == ""
        ]

        # RATINGS AND IMAGE ARE OPTIONAL

        optional_fields = {
            "ratings",
            "image_link",
        }

        required_missing = [
            field
            for field in missing_fields
            if field not in optional_fields
        ]

        if required_missing:

            self.stats["products_skipped"] += 1

            self.logger_extra.warning(
                "SKIPPED: REQUIRED FIELDS MISSING: %s | %s",
                required_missing,
                product_url
            )

            return

        # SAVE PRODUCT

        self.all_results.append(record)

        self.category_counts[category] = (
            self.category_counts.get(category, 0) + 1
        )

        self.stats["products_saved"] += 1

        self.save_outputs()

        self.logger_extra.info(
            "PRODUCT SAVED: %s",
            name
        )

        self.logger_extra.info(
            "PRICE: %s | ORIGINAL: %s | DISCOUNT: %s%%",
            price,
            original_price,
            discount
        )

        self.logger_extra.info(
            "RATING: %s | IMAGE: %s",
            ratings,
            "YES" if image_url else "NO"
        )

        self.logger_extra.info(
            "CATEGORY PROGRESS: %s/%s | TOTAL: %s/%s",
            self.category_counts[category],
            self.category_targets.get(
                category,
                PRODUCTS_PER_CATEGORY
            ),
            len(self.all_results),
            TOTAL_TARGET
        )

    # ------------------------------------------------------
    # SAVE JSON AND CSV
    # ------------------------------------------------------

    def save_outputs(self):

        try:

            with open(
                OUTPUT_JSON,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    self.all_results,
                    file,
                    ensure_ascii=False,
                    indent=4
                )

            with open(
                OUTPUT_CSV,
                "w",
                newline="",
                encoding="utf-8-sig"
            ) as file:

                writer = csv.DictWriter(
                    file,
                    fieldnames=FIELDS
                )

                writer.writeheader()
                writer.writerows(self.all_results)

        except Exception:

            self.logger_extra.exception(
                "OUTPUT FILE SAVE FAILED"
            )

    # ------------------------------------------------------
    # FINAL SUMMARY
    # ------------------------------------------------------

    def log_summary(self):

        self.logger_extra.info(
            "========== ARCHIES SCRAPING COMPLETED =========="
        )

        for category, count in self.category_counts.items():

            target = self.category_targets.get(
                category,
                PRODUCTS_PER_CATEGORY
            )

            self.logger_extra.info(
                "CATEGORY: %s | SAVED: %s/%s",
                category,
                count,
                target
            )

        self.logger_extra.info(
            "TOTAL PRODUCTS SAVED: %s/%s",
            len(self.all_results),
            TOTAL_TARGET
        )

        self.logger_extra.info(
            "COLLECTIONS STARTED: %s",
            self.stats["collections_started"]
        )

        self.logger_extra.info(
            "LISTING PAGES CRAWLED: %s",
            self.stats["listing_pages_crawled"]
        )

        self.logger_extra.info(
            "PRODUCT PAGES CRAWLED: %s",
            self.stats["product_pages_crawled"]
        )

        self.logger_extra.info(
            "PRODUCTS FOUND: %s",
            self.stats["products_found"]
        )

        self.logger_extra.info(
            "PRODUCTS SAVED: %s",
            self.stats["products_saved"]
        )

        self.logger_extra.info(
            "PRODUCTS SKIPPED: %s",
            self.stats["products_skipped"]
        )

        self.logger_extra.info(
            "CRAWL FAILURES: %s",
            self.stats["crawl_failures"]
        )

        self.logger_extra.info(
            "JSON OUTPUT: %s",
            OUTPUT_JSON
        )

        self.logger_extra.info(
            "CSV OUTPUT: %s",
            OUTPUT_CSV
        )

        self.logger_extra.info(
            "LOG OUTPUT: %s",
            LOG_FILE
        )

    # ------------------------------------------------------
    # SPIDER CLOSED
    # ------------------------------------------------------

    def closed(self, reason):

        self.save_outputs()

        self.logger_extra.info(
            "SPIDER CLOSED | REASON: %s",
            reason
        )
