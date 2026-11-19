
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
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://mamaearth.in"

MAX_PAGES = 5

BASE_URLS = {
    "Beauty": [
        "https://mamaearth.in/collections/face-wash",
        "https://mamaearth.in/collections/sunscreen",
        "https://mamaearth.in/collections/face-serum",
        "https://mamaearth.in/collections/face-cream",
        "https://mamaearth.in/collections/face-moisturizer",
        "https://mamaearth.in/collections/face-mask",
        "https://mamaearth.in/collections/face-scrub",
        "https://mamaearth.in/collections/skin-care",
        "https://mamaearth.in/collections/hair-shampoo",
        "https://mamaearth.in/collections/hair-oil",
        "https://mamaearth.in/collections/hair-conditioner",
        "https://mamaearth.in/collections/hair-serum",
        "https://mamaearth.in/collections/hair-mask",
        "https://mamaearth.in/collections/hair"
    ],
    "Makeup": [
       "https://mamaearth.in/collections/lipsticks"
       "https://mamaearth.in/collections/eye-kajal",
       "https://mamaearth.in/collections/foundation",
       "https://mamaearth.in/collections/compact",
       "https://mamaearth.in/collections/lip-balm",
       "https://mamaearth.in/collections/lip-balm",
       "https://mamaearth.in/collections/makeup-products"
    ]
    
}


# ============================================================
# REQUEST / RATE LIMIT SETTINGS
# ============================================================

LISTING_PAGE_DELAY = 1

RATE_LIMIT_DELAY = 30

MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000

PRODUCT_SELECTOR_TIMEOUT = 30000


# ============================================================
# HELPERS
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

    if value is None:
        return None

    match = re.search(r"(\d+)", str(value))

    return int(match.group(1)) if match else None


def parse_rating_to_float(rating_str):

    if rating_str is None:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(rating_str)
    )

    if not match:
        return None

    try:

        value = float(match.group())

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def parse_price_to_float(price_str):

    if not price_str:
        return None

    if price_str in ("N/A", "-", ""):
        return None

    clean = re.sub(
        r"[^\d.]",
        "",
        str(price_str)
    )

    if not clean:
        return None

    try:

        value = float(clean)

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def normalize_text(text):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


def normalize_product_name(name):

    if not name:
        return ""

    name = normalize_text(name)

    name = (
        name
        .replace("–", " ")
        .replace("—", " ")
    )

    return normalize_text(name)


def extract_rating_preserve_decimal(text):

    if not text:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(text)
    )

    if not match:
        return None

    value = match.group()

    if "." in value:
        return float(value)

    return int(value)


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

def extract_product_description(product_html):

    if not product_html:
        return None

    product_soup = BeautifulSoup(
        product_html,
        "html.parser"
    )

    description_tag = product_soup.select_one(
        "#honasa-product-description"
    )

    if not description_tag:

        description_tag = product_soup.select_one(
            ".small-description"
        )

    if not description_tag:
        return None

    for tag in description_tag.select(
        "svg, img, button, script, style"
    ):
        tag.decompose()

    description_parts = []

    for element in description_tag.select("p, li"):

        text = normalize_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if text:
            description_parts.append(text)

    if not description_parts:

        text = normalize_text(
            description_tag.get_text(
                " ",
                strip=True
            )
        )

        if text:
            description_parts.append(text)

    if not description_parts:
        return None

    description = normalize_text(
        " ".join(description_parts)
    )

    return description if description else None


# ============================================================
# IMAGE EXTRACTION
# ============================================================

async def extract_image(card):

    image_locator = card.locator(
        "img.hdt-card-product__media--main"
    )

    if await image_locator.count() == 0:

        image_locator = card.locator("img")

    if await image_locator.count() == 0:
        return None

    image_tag = image_locator.first

    attributes = [
        "src",
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-srcset",
        "srcset",
    ]

    for attribute in attributes:

        value = await image_tag.get_attribute(
            attribute
        )

        if not value:
            continue

        value = value.strip()

        if not value:
            continue

        image_url = value.split(",")[0].strip()
        image_url = image_url.split()[0]

        if image_url.startswith("//"):
            return "https:" + image_url

        return urljoin(
            BASE_URL,
            image_url
        )

    return None


# ============================================================
# PRODUCT NAME EXTRACTION
# ============================================================

async def extract_product_name(card):

    selectors = [
        "a.hdt-card-product__title",
        ".hdt-card-product__title",
        "a[href*='/products/']",
    ]

    for selector in selectors:

        locator = card.locator(selector)

        if await locator.count() == 0:
            continue

        try:

            text = await locator.first.inner_text()

            name = normalize_product_name(text)

            if name:
                return name

        except Exception:
            continue

    return None


# ============================================================
# PRODUCT LINK EXTRACTION
# ============================================================

async def extract_product_link(card):

    selectors = [
        "a.hdt-card-product__title",
        "a.hdt-card-product__media-wrapp",
        "a[href*='/products/']",
    ]

    for selector in selectors:

        locator = card.locator(selector)

        if await locator.count() == 0:
            continue

        try:

            href = await locator.first.get_attribute(
                "href"
            )

            if href and "/products/" in href:

                return urljoin(
                    BASE_URL,
                    href
                )

        except Exception:
            continue

    return None


# ============================================================
# PRICE EXTRACTION
# ============================================================

async def get_locator_text(card, selector):

    locator = card.locator(selector)

    if await locator.count() == 0:
        return None

    try:

        text = await locator.first.inner_text()

        return normalize_text(text)

    except Exception:
        return None


async def extract_product_prices(card):

    sale_price_text = await get_locator_text(
        card,
        ".hdt-price .hdt-card-money"
    )

    original_price_text = await get_locator_text(
        card,
        ".hdt-compare-at-price .hdt-card-money"
    )

    price_val = parse_price_to_float(
        sale_price_text
    )

    original_val = parse_price_to_float(
        original_price_text
    )

    return price_val, original_val


# ============================================================
# DISCOUNT EXTRACTION
# ============================================================

async def extract_discount(card, price_val, original_val):

    discount_text = await get_locator_text(
        card,
        ".hdt-money-discount"
    )

    discount = discount_to_int(
        discount_text
    )

    if discount is not None:
        return discount

    if (
        price_val is not None
        and original_val is not None
        and original_val > 0
        and price_val <= original_val
    ):

        return round(
            (
                (original_val - price_val)
                / original_val
            ) * 100
        )

    return None


# ============================================================
# RATING EXTRACTION
# ============================================================

async def extract_rating(card):

    rating_locator = card.locator(
        ".star-rating .rating"
    )

    if await rating_locator.count() > 0:

        try:

            rating_element = rating_locator.first

            aria_label = await rating_element.get_attribute(
                "aria-label"
            )

            if aria_label:

                rating = extract_rating_preserve_decimal(
                    aria_label
                )

                if rating is not None and 0 <= rating <= 5:
                    return rating

            rating_text = await rating_element.inner_text()

            rating = extract_rating_preserve_decimal(
                rating_text
            )

            if rating is not None and 0 <= rating <= 5:
                return rating

        except Exception as exc:

            logging.warning(
                f"Rating extraction failed: {exc}"
            )

    # Fallback: check other rating elements.
    fallback_selectors = [
        "[aria-label*='out of 5']",
        ".hdt-rating",
    ]

    for selector in fallback_selectors:

        locator = card.locator(selector)

        count = await locator.count()

        for index in range(count):

            try:

                element = locator.nth(index)

                rating_text = await element.get_attribute(
                    "aria-label"
                )

                if not rating_text:

                    rating_text = await element.inner_text()

                rating = extract_rating_preserve_decimal(
                    rating_text
                )

                if rating is not None and 0 <= rating <= 5:
                    return rating

            except Exception:
                continue

    return None


# ============================================================
# OPEN LISTING PAGE WITH 429 RETRY
# ============================================================

async def open_listing_page(page, url):

    for attempt in range(
        1,
        MAX_429_RETRIES + 1
    ):

        try:

            logging.info(
                f"Opening listing page "
                f"(attempt {attempt}/"
                f"{MAX_429_RETRIES}): {url}"
            )

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            if response:

                status = response.status

                logging.info(
                    f"HTTP status: {status}"
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        logging.warning(
                            f"HTTP 429 received. "
                            f"Waiting {RATE_LIMIT_DELAY} seconds."
                        )

                        await page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    return None

            return response

        except Exception as exc:

            logging.warning(
                f"Failed to open listing page "
                f"{url}: {exc}"
            )

            if attempt < MAX_429_RETRIES:

                await page.wait_for_timeout(
                    LISTING_PAGE_DELAY * 1000
                )

    return None


# ============================================================
# CRAWL MAMAEARTH
# ============================================================

async def scrape_mamaearth_async():

    results = []

    seen_links = set()
    seen_names = set()

    category_counts = {}

    async with async_playwright() as playwright:

        # ====================================================
        # BROWSER
        # ====================================================

        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 "
                "Safari/537.36"
            ),
            viewport={
                "width": 1920,
                "height": 1080,
            },
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = await context.new_page()

        page.set_default_timeout(
            PAGE_TIMEOUT
        )

        page.set_default_navigation_timeout(
            PAGE_TIMEOUT
        )

        try:

            # =================================================
            # CATEGORY LOOP
            # =================================================

            for category, urls in BASE_URLS.items():

                category_results = []

                for base_url in urls:

                    page_number = 1

                    while page_number <= MAX_PAGES:

                        url = (
                            f"{base_url}"
                            f"?page={page_number}"
                        )

                        logging.info("=" * 60)

                        logging.info(
                            f"Fetching: {url}"
                        )

                        if page_number > 1:

                            await page.wait_for_timeout(
                                LISTING_PAGE_DELAY * 1000
                            )

                        # =====================================
                        # OPEN LISTING PAGE
                        # =====================================

                        response = await open_listing_page(
                            page,
                            url
                        )

                        if response is None:

                            logging.warning(
                                f"Unable to fetch listing page: {url}"
                            )

                            break

                        # =====================================
                        # WAIT FOR PRODUCT CARDS
                        # =====================================

                        try:

                            await page.wait_for_selector(
                                "hdt-card-product",
                                timeout=PRODUCT_SELECTOR_TIMEOUT
                            )

                        except Exception:

                            logging.warning(
                                f"Mamaearth product cards "
                                f"not found on page {page_number}: {url}"
                            )

                            break

                        # =====================================
                        # PRODUCT CARDS
                        # =====================================

                        product_cards = page.locator(
                            "hdt-card-product"
                        )

                        card_count = await product_cards.count()

                        if card_count == 0:

                            logging.info(
                                f"No products found on page {page_number}"
                            )

                            break

                        logging.info(
                            f"Found {card_count} products "
                            f"on page {page_number}"
                        )

                        # =====================================
                        # PRODUCT LOOP
                        # =====================================

                        for index in range(card_count):

                            try:

                                card = product_cards.nth(index)

                                # =============================
                                # PRODUCT NAME
                                # =============================

                                name = await extract_product_name(
                                    card
                                )

                                if not name:

                                    logging.warning(
                                        f"Product name not found "
                                        f"for card #{index + 1}"
                                    )

                                    continue

                                # =============================
                                # PRODUCT LINK
                                # =============================

                                product_link = await extract_product_link(
                                    card
                                )

                                if not product_link:

                                    logging.warning(
                                        f"Product link not found: {name}"
                                    )

                                    continue

                                # =============================
                                # DUPLICATE CHECK
                                # =============================

                                if product_link in seen_links:
                                    continue

                                if name.lower() in seen_names:
                                    continue

                                # Add immediately to avoid
                                # processing duplicate cards.
                                seen_links.add(
                                    product_link
                                )

                                seen_names.add(
                                    name.lower()
                                )

                                # =============================
                                # PRICE
                                # =============================

                                price_val, original_val = (
                                    await extract_product_prices(card)
                                )

                                if price_val is None:

                                    logging.info(
                                        f"Skipping {name}: "
                                        f"sale price not found."
                                    )

                                    continue

                                if original_val is None:

                                    logging.info(
                                        f"Skipping {name}: "
                                        f"original price not found."
                                    )

                                    continue

                                if price_val > original_val:

                                    logging.info(
                                        f"Skipping {name}: "
                                        f"sale price is greater "
                                        f"than original price."
                                    )

                                    continue

                                # =============================
                                # DISCOUNT
                                # =============================

                                discount = await extract_discount(
                                    card,
                                    price_val,
                                    original_val
                                )

                                if discount is None:

                                    logging.info(
                                        f"Skipping {name}: "
                                        f"discount not detected."
                                    )

                                    continue

                                # =============================
                                # IMAGE
                                # =============================

                                image_link = await extract_image(
                                    card
                                )

                                if not image_link:

                                    logging.info(
                                        f"Skipping {name}: "
                                        f"image not found."
                                    )

                                    continue

                                # =============================
                                # RATING
                                # =============================

                                rating = await extract_rating(
                                    card
                                )

                                # =============================
                                # DESCRIPTION
                                # =============================

                                card_html = await card.inner_html()

                                description = (
                                    extract_product_description(
                                        card_html
                                    )
                                )

                                if description:

                                    logging.info(
                                        f"Description extracted "
                                        f"from listing: {name}"
                                    )

                                else:

                                    logging.info(
                                        f"Description not available "
                                        f"for: {name}"
                                    )

                                # =============================
                                # REQUIRED FIELDS
                                # =============================

                                if (
                                    not name
                                    or price_val is None
                                    or original_val is None
                                    or not image_link
                                    or not product_link
                                ):

                                    logging.info(
                                        f"Skipping incomplete product: {name}"
                                    )

                                    continue

                                # =============================
                                # FINAL CLEANING
                                # =============================

                                price_val = clean_to_int(
                                    price_val
                                )

                                original_val = clean_to_int(
                                    original_val
                                )

                                discount_value = discount_to_int(
                                    discount
                                )

                                rating = parse_rating_to_float(
                                    rating
                                )

                                # =============================
                                # TIMESTAMP
                                # =============================

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

                                # =============================
                                # FINAL PRODUCT
                                # =============================

                                product_data = {

                                    "name": name,

                                    "price": price_val,

                                    "currency": "₹",

                                    "original_price": original_val,

                                    "discount": discount_value,

                                    "ratings": rating,

                                    "description": description,

                                    "image_link": image_link,

                                    "product_link": product_link,

                                    "organization_id": "Dealwallet",

                                    "store_id": "Mamaearth",

                                    "categories_id": category,

                                    "created_at": timestamp,
                                }

                                # =============================
                                # ADD RESULT
                                # =============================

                                results.append(
                                    product_data
                                )

                                category_results.append(
                                    product_data
                                )

                                logging.info(
                                    f"Scraped: {name} | "
                                    f"Price: {price_val} | "
                                    f"Original: {original_val} | "
                                    f"Discount: {discount_value}% | "
                                    f"Rating: {rating}"
                                )

                            except Exception as exc:

                                logging.error(
                                    f"Error parsing product card "
                                    f"#{index + 1} on page "
                                    f"{page_number}: {exc}",
                                    exc_info=True
                                )

                        # =====================================
                        # NEXT PAGE
                        # =====================================

                        page_number += 1

                # =============================================
                # CATEGORY SUMMARY
                # =============================================

                category_counts[category] = len(
                    category_results
                )

                logging.info(
                    f"Total products scraped for "
                    f"'{category}': {len(category_results)}"
                )

        finally:

            await page.close()

            await context.close()

            await browser.close()

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    logging.info("=" * 60)

    logging.info(
        f"Total Mamaearth products scraped: {len(results)}"
    )

    logging.info("=" * 60)

    return results


# ============================================================
# WINDOWS PROCESS RUNNER
# ============================================================

def run_mamaearth_scraper():

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
        "Mamaearth browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_mamaearth_async()
        )

        logging.info(
            "Mamaearth browser process finished. "
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "Mamaearth browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class MamaearthSpider(scrapy.Spider):

    name = "mamaearth"

    allowed_domains = [
        "mamaearth.in",
        "www.mamaearth.in",
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Mamaearth Playwright scraper..."
        )

        self.logger.info("=" * 60)

        # ================================================
        # RUN PLAYWRIGHT IN SEPARATE PROCESS
        # ================================================

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_mamaearth_scraper
            )

        # ================================================
        # SAVE RAW SCRAPED DATA
        # ================================================

        with open(
            "scrape_mamaearth.json",
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=2
            )

        self.logger.info(
            "Mamaearth scraper returned %s products.",
            len(products)
        )

        # ================================================
        # YIELD TO SCRAPY
        # ================================================

        for product in products:

            if not product.get("image_link"):
                continue

            if not product.get("product_link"):
                continue

            yield product

        self.logger.info("=" * 60)

        self.logger.info(
            "Mamaearth spider completed."
        )

        self.logger.info("=" * 60)
