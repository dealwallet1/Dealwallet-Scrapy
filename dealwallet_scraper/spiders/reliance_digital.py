
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.reliancedigital.in"

MAX_PAGES = 20
PAGE_SIZE = 12

BASE_URLS = {
    "Mobile": [
        "https://www.reliancedigital.in/collection/smartphones?is_available=true&internal_source=clp&internal_medium=view-all&internal_campaign=brands&page_no=1&page_size=12&page_type=number"
    ],
    # "Electronics": [
    #     "https://www.reliancedigital.in/collection/headphones-earphones-speakers?is_available=true&internal_source=clp&internal_medium=view-all&internal_campaign=brands",
    # ],
    # "Laptops": ["https://www.reliancedigital.in/collection/laptops?is_available=true&internal_source=clp&internal_medium=view-all&internal_campaign=brands&page_no=1&page_size=12&page_type=number"
    # ],
    # "Home Appliances": [
    #     "https://www.reliancedigital.in/collection/best-sellers?is_available=true&page_no=1&page_size=12&page_type=number",
    #     "https://www.reliancedigital.in/collection/best-selling-refrigerators-250618?is_available=true&page_no=1&page_size=12&page_type=number"
    # ],
   
}

OUTPUT_FILE = "scrape_reliance_digital.json"

HEADLESS = True
PAGE_TIMEOUT = 60000
RETRY_COUNT = 3
RETRY_DELAY = 5

STORE_ID = "Reliance Digital"
ORGANIZATION_ID = "Dealwallet"
CURRENCY = "₹"

LISTING_CARD_SELECTOR = ".product-card"
PRODUCT_NAME_SELECTOR = ".product-card-title"
PRODUCT_LINK_SELECTOR = "a.details-container"
PRODUCT_IMAGE_SELECTOR = ".product-card-image img"
SELLING_PRICE_SELECTOR = ".price"
ORIGINAL_PRICE_SELECTOR = ".mrp-amount"
DISCOUNT_SELECTOR = ".discount"
REVIEW_COUNT_SELECTOR = ".product-card-rating .detail"

RATING_SELECTOR = ".rd-feedback-service-rating-content span"
DESCRIPTION_SELECTOR = "#key_features .features li"

BRAND_SELECTORS = (
    ".product-brand",
    ".brand-name",
    ".pdp-brand",
    "[itemprop='brand']",
    ".product-card-brand",
)

OUTPUT_FIELDS = (
    "name",
    "brand",
    "price",
    "currency",
    "original_price",
    "discount",
    "ratings",
    "review_count",
    "description",
    "image_link",
    "product_link",
    "affiliate_url",
    "organization_id",
    "store_id",
    "categories_id",
    "created_at",
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_text(value):

    if not value:
        return None

    value = re.sub(r"\s+", " ", str(value))
    value = value.strip()

    if not value:
        return None

    return value


def parse_price(value):

    if not value:
        return None

    value = str(value).replace(",", "")
    value = re.sub(r"[^\d.]", "", value)

    try:
        return int(round(float(value)))
    except (ValueError, TypeError):
        return None


def parse_discount(value):

    if not value:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        str(value)
    )

    if match:
        try:
            return int(round(float(match.group(1))))
        except ValueError:
            return None

    return None


def calculate_discount(price, original_price):

    if price is None or original_price is None:
        return None

    if original_price <= price or original_price == 0:
        return None

    discount = (
        (original_price - price) / original_price
    ) * 100

    return int(round(discount))


def get_created_at():
    return datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).strftime("%Y-%m-%d")



def get_page_url(base_url, page_number):

    parsed_url = urlparse(base_url)

    query_params = parse_qs(parsed_url.query)

    query_params["page_no"] = [str(page_number)]
    query_params["page_size"] = [str(PAGE_SIZE)]

    new_query = urlencode(
        query_params,
        doseq=True
    )

    return urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment
        )
    )


def get_first_text(element, selector):

    found = element.select_one(selector)

    if found:
        return normalize_text(
            found.get_text(" ", strip=True)
        )

    return None


# ============================================================
# LISTING PAGE EXTRACTION
# ============================================================

def extract_product_name(card):

    return get_first_text(
        card,
        PRODUCT_NAME_SELECTOR
    )


def extract_product_link(card):

    link_element = card.select_one(
        PRODUCT_LINK_SELECTOR
    )

    if not link_element:
        return None

    href = link_element.get("href")

    if not href:
        return None

    return urljoin(BASE_URL, href)


def extract_image(card):

    image_element = card.select_one(
        PRODUCT_IMAGE_SELECTOR
    )

    if not image_element:
        return None

    image_url = (
        image_element.get("src")
        or image_element.get("data-src")
        or image_element.get("data-lazy-src")
        or image_element.get("data-original")
    )

    if not image_url:

        srcset = image_element.get("srcset")

        if srcset:
            image_url = (
                srcset.split(",")[0]
                .strip()
                .split(" ")[0]
            )

    if image_url:
        return urljoin(BASE_URL, image_url)

    return None


def extract_brand_from_card(card):

    for selector in BRAND_SELECTORS:

        brand = get_first_text(
            card,
            selector
        )

        if brand:
            return brand

    return None


def extract_prices(card):

    selling_price_text = get_first_text(
        card,
        SELLING_PRICE_SELECTOR
    )

    original_price_text = get_first_text(
        card,
        ORIGINAL_PRICE_SELECTOR
    )

    discount_text = get_first_text(
        card,
        DISCOUNT_SELECTOR
    )

    price = parse_price(selling_price_text)

    original_price = parse_price(
        original_price_text
    )

    discount = parse_discount(
        discount_text
    )

    if discount is None:

        discount = calculate_discount(
            price,
            original_price
        )

    return {
        "price": price,
        "original_price": original_price,
        "discount": discount
    }


def extract_review_count(card):

    review_text = get_first_text(
        card,
        REVIEW_COUNT_SELECTOR
    )

    if not review_text:
        return None

    match = re.search(
        r"([\d,]+)",
        review_text
    )

    if match:
        return int(
            match.group(1).replace(",", "")
        )

    return None


def extract_product_cards(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    return soup.select(
        LISTING_CARD_SELECTOR
    )


# ============================================================
# DETAIL PAGE EXTRACTION
# ============================================================

def extract_rating(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    rating_element = soup.select_one(
        RATING_SELECTOR
    )

    if not rating_element:
        return None

    rating_text = normalize_text(
        rating_element.get_text(" ", strip=True)
    )

    if not rating_text:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        rating_text
    )

    if match:

        try:
            return float(match.group(0))
        except ValueError:
            return None

    return None


def extract_product_description(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    feature_elements = soup.select(
        DESCRIPTION_SELECTOR
    )

    if not feature_elements:
        return None

    features = []

    for element in feature_elements:

        feature = normalize_text(
            element.get_text(" ", strip=True)
        )

        if feature:
            features.append(feature)

    if not features:
        return None

    return ", ".join(features)


def extract_brand_from_detail(html):

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # Check brand selectors
    for selector in BRAND_SELECTORS:

        element = soup.select_one(selector)

        if element:

            brand = normalize_text(
                element.get("content")
                or element.get_text(" ", strip=True)
            )

            if brand:
                return brand

    # Check JSON-LD structured data
    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:

            data = json.loads(
                script.string or script.get_text()
            )

            objects = data if isinstance(data, list) else [data]

            while objects:

                obj = objects.pop(0)

                if not isinstance(obj, dict):
                    continue

                graph = obj.get("@graph")

                if isinstance(graph, list):
                    objects.extend(graph)

                brand_data = obj.get("brand")

                if isinstance(brand_data, dict):

                    brand = normalize_text(
                        brand_data.get("name")
                    )

                    if brand:
                        return brand

                elif isinstance(brand_data, str):

                    brand = normalize_text(
                        brand_data
                    )

                    if brand:
                        return brand

        except (json.JSONDecodeError, TypeError):
            continue

    # Check Open Graph product metadata
    for selector in (
        'meta[property="product:brand"]',
        'meta[name="brand"]',
    ):

        element = soup.select_one(selector)

        if element:

            brand = normalize_text(
                element.get("content")
            )

            if brand:
                return brand

    return None


def extract_detail_data(html):

    return {
        "brand": extract_brand_from_detail(html),
        "ratings": extract_rating(html),
        "description": extract_product_description(html)
    }


# ============================================================
# PLAYWRIGHT LISTING PAGE
# ============================================================

async def open_listing_page(page, url):

    for attempt in range(1, RETRY_COUNT + 1):

        try:

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            if response and response.status == 429:

                logger.warning(
                    "429 received for %s. Attempt %s/%s",
                    url,
                    attempt,
                    RETRY_COUNT
                )

                await asyncio.sleep(
                    RETRY_DELAY * attempt
                )

                continue

            await page.wait_for_timeout(3000)

            try:

                await page.wait_for_selector(
                    LISTING_CARD_SELECTOR,
                    timeout=15000
                )

            except Exception:

                logger.warning(
                    "Product cards not found immediately: %s",
                    url
                )

            return await page.content()

        except Exception as error:

            logger.error(
                "Listing page error: %s | Attempt %s/%s",
                error,
                attempt,
                RETRY_COUNT
            )

            await asyncio.sleep(
                RETRY_DELAY * attempt
            )

    return None


# ============================================================
# PLAYWRIGHT PRODUCT DETAIL PAGE
# ============================================================

async def scrape_product_detail(browser, product_url):

    if not product_url:

        return {
            "brand": None,
            "ratings": None,
            "description": None
        }

    page = await browser.new_page()

    try:

        for attempt in range(1, RETRY_COUNT + 1):

            try:

                response = await page.goto(
                    product_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT
                )

                if response and response.status == 429:

                    logger.warning(
                        "Detail page returned 429: %s",
                        product_url
                    )

                    await asyncio.sleep(
                        RETRY_DELAY * attempt
                    )

                    continue

                try:

                    await page.wait_for_selector(
                        RATING_SELECTOR,
                        timeout=10000
                    )

                except Exception:
                    pass

                try:

                    await page.wait_for_selector(
                        DESCRIPTION_SELECTOR,
                        timeout=10000
                    )

                except Exception:
                    pass

                await page.wait_for_timeout(1000)

                html = await page.content()

                detail_data = extract_detail_data(html)

                logger.info(
                    "Detail extracted | Brand: %s | Rating: %s | Description: %s",
                    detail_data["brand"],
                    detail_data["ratings"],
                    "Available" if detail_data["description"] else "Missing"
                )

                return detail_data

            except Exception as error:

                logger.error(
                    "Detail page error: %s | Attempt %s/%s",
                    error,
                    attempt,
                    RETRY_COUNT
                )

                await asyncio.sleep(
                    RETRY_DELAY * attempt
                )

        return {
            "brand": None,
            "ratings": None,
            "description": None
        }

    finally:

        await page.close()


# ============================================================
# MAIN ASYNC SCRAPER
# ============================================================

async def scrape_reliance_digital_async():

    all_products = []

    seen_urls = set()
    seen_names = set()

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=HEADLESS
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={
                "width": 1366,
                "height": 768
            }
        )

        listing_page = await context.new_page()

        try:

            for category, collection_urls in BASE_URLS.items():

                for base_url in collection_urls:

                    for page_number in range(
                        1,
                        MAX_PAGES + 1
                    ):

                        listing_url = get_page_url(
                            base_url,
                            page_number
                        )

                        logger.info(
                            "Scraping category: %s | Page %s: %s",
                            category,
                            page_number,
                            listing_url
                        )

                        html = await open_listing_page(
                            listing_page,
                            listing_url
                        )

                        if not html:

                            logger.warning(
                                "Unable to load listing page: %s",
                                listing_url
                            )

                            break

                        product_cards = extract_product_cards(
                            html
                        )

                        if not product_cards:

                            logger.info(
                                "No products found on page %s",
                                page_number
                            )

                            break

                        logger.info(
                            "Found %s product cards on page %s",
                            len(product_cards),
                            page_number
                        )

                        new_products_on_page = 0

                        for card in product_cards:

                            product_name = extract_product_name(
                                card
                            )

                            product_link = extract_product_link(
                                card
                            )

                            image_link = extract_image(card)

                            brand = extract_brand_from_card(
                                card
                            )

                            if not product_name or not product_link:
                                continue

                            normalized_name = product_name.lower()

                            if product_link in seen_urls:
                                continue

                            if normalized_name in seen_names:
                                continue

                            prices = extract_prices(card)

                            # Skip products without a valid discount
                            if (
                                prices["discount"] is None
                                or prices["discount"] <= 0
                            ):

                                logger.info(
                                    "Skipping product without discount: %s | Discount: %s",
                                    product_name,
                                    prices["discount"]
                                )

                                continue

                            seen_urls.add(product_link)
                            seen_names.add(normalized_name)

                            review_count = extract_review_count(
                                card
                            )

                            logger.info(
                                "Scraping product detail: %s",
                                product_name
                            )

                            detail_data = await scrape_product_detail(
                                browser,
                                product_link
                            )

                            # Use detail-page brand when listing brand is missing
                            if not brand:
                                brand = detail_data["brand"]

                            product = {
                                "name": product_name,
                                "brand": brand,
                                "price": prices["price"],
                                "currency": CURRENCY,
                                "original_price": prices["original_price"],
                                "discount": prices["discount"],
                                "ratings": detail_data["ratings"],
                                "review_count": review_count,
                                "description": detail_data["description"],
                                "image_link": image_link,
                                "product_link": product_link,
                                "affiliate_url": None,
                                "organization_id": ORGANIZATION_ID,
                                "store_id": STORE_ID,
                                "categories_id": category,
                                "created_at": get_created_at()
                            }

                            all_products.append(
                                {
                                    field: product.get(field)
                                    for field in OUTPUT_FIELDS
                                }
                            )

                            new_products_on_page += 1

                            logger.info(
                                "Product scraped: %s | Brand: %s | Price: %s | Discount: %s | Rating: %s | Reviews: %s",
                                product_name,
                                brand,
                                prices["price"],
                                prices["discount"],
                                detail_data["ratings"],
                                review_count
                            )

                            await asyncio.sleep(1)

                        logger.info(
                            "Page %s completed. New products: %s",
                            page_number,
                            new_products_on_page
                        )

                        if new_products_on_page == 0:

                            logger.info(
                                "No discounted products found. Stopping pagination."
                            )

                            break

                        await asyncio.sleep(2)

        finally:

            await listing_page.close()
            await context.close()
            await browser.close()

    return all_products


# ============================================================
# WINDOWS PLAYWRIGHT THREAD RUNNER
# ============================================================

def run_playwright_in_thread():

    if not hasattr(asyncio, "ProactorEventLoop"):

        raise RuntimeError(
            "This Playwright runner requires Windows."
        )

    thread_loop = asyncio.ProactorEventLoop()

    asyncio.set_event_loop(thread_loop)

    try:

        return thread_loop.run_until_complete(
            scrape_reliance_digital_async()
        )

    finally:

        thread_loop.close()
        asyncio.set_event_loop(None)


# ============================================================
# SCRAPY SPIDER
# ============================================================

class RelianceDigitalSpider(scrapy.Spider):

    name = "reliance_digital"

    allowed_domains = [
        "reliancedigital.in",
        "jiostore.online"
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_TIMEOUT": 120,
        "LOG_LEVEL": "INFO",
        "FEED_EXPORT_ENCODING": "utf-8"
    }

    async def start(self):

        logger.info(
            "Reliance Digital scraper started"
        )

        loop = asyncio.get_running_loop()

        with ThreadPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_playwright_in_thread
            )

        logger.info(
            "Total products scraped: %s",
            len(products)
        )

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=4
            )

        logger.info(
            "JSON saved successfully: %s",
            OUTPUT_FILE
        )

        for product in products:
            yield product

        logger.info(
            "Reliance Digital scraper completed"
        )
