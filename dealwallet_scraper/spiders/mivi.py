
import asyncio
import json
import logging
import re
import sys

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import scrapy
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.mivi.in"

BASE_URLS = {
    "Electronics": [
        # "https://www.mivi.in/collections/earbuds",
        # "https://www.mivi.in/collections/soundbars",
        # "https://www.mivi.in/collections/speakers",
        # "https://www.mivi.in/collections/neckbands",
        "https://www.mivi.in/collections/combos",
        "https://www.mivi.in/collections/accessories"
    ]
}

MAX_PAGES = 10

LISTING_PAGE_DELAY = 3
RATE_LIMIT_DELAY = 30
MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000

STORE_ID = "Mivi"
ORGANIZATION_ID = "Dealwallet"
CATEGORY_ID = "Electronics"

OUTPUT_FILE = "scrape_mivi.json"


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text)).strip()


def parse_price(value):
    if not value:
        return None

    match = re.search(
        r"\d[\d,]*(?:\.\d+)?",
        str(value).replace("₹", "")
    )

    if not match:
        return None

    try:
        number = float(match.group().replace(",", ""))

        if number.is_integer():
            return int(number)

        return number

    except ValueError:
        return None


def parse_rating(value):
    if not value:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        str(value)
    )

    if not match:
        return None

    number = float(match.group(1))

    return int(number) if number.is_integer() else number


def parse_discount(value):
    if not value:
        return None

    match = re.search(r"(\d+)", str(value))

    return int(match.group(1)) if match else None


def calculate_discount(price, original_price):
    if (
        price is None
        or original_price is None
        or original_price <= 0
        or price > original_price
    ):
        return None

    return round(
        ((original_price - price) / original_price) * 100
    )


def get_timestamp():
    return datetime.now(
        timezone(
            timedelta(hours=5, minutes=30)
        )
    ).strftime("%Y-%m-%dT%H:%M:%S")


def get_page_url(base_url, page_number):
    parsed = urlparse(base_url)

    query = parse_qs(parsed.query)

    query["page"] = [str(page_number)]

    new_query = urlencode(query, doseq=True)

    return urlunparse(
        parsed._replace(query=new_query)
    )


# ============================================================
# PAGE REQUEST
# ============================================================

async def open_listing_page(page, url):

    for attempt in range(1, MAX_429_RETRIES + 1):

        try:
            logging.info(
                "Opening page (%s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                url
            )

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            if response and response.status == 429:

                if attempt < MAX_429_RETRIES:

                    logging.warning(
                        "HTTP 429 received. Waiting %s seconds.",
                        RATE_LIMIT_DELAY
                    )

                    await page.wait_for_timeout(
                        RATE_LIMIT_DELAY * 1000
                    )

                    continue

                return None

            return response

        except Exception as exc:

            logging.warning(
                "Page request failed: %s",
                exc
            )

            if attempt < MAX_429_RETRIES:

                await page.wait_for_timeout(
                    RATE_LIMIT_DELAY * 1000
                )

    return None


# ============================================================
# PRODUCT NAME
# ============================================================

async def extract_product_name(card):

    # Mivi product images contain the product name in alt.
    image = card.locator("img[alt]")

    if await image.count():

        name = await image.first.get_attribute("alt")

        name = normalize_text(name)

        if name:
            return name

    selectors = [
        "[data-testid*='product-title']",
        "[class*='product-title']",
        "[class*='product-name']",
        "h2",
        "h3",
        "a[href*='/products/']"
    ]

    for selector in selectors:

        locator = card.locator(selector)

        if await locator.count():

            try:
                name = normalize_text(
                    await locator.first.inner_text()
                )

                if name:
                    return name

            except Exception:
                continue

    return None


# ============================================================
# PRODUCT LINK
# ============================================================

async def extract_product_link(card):

    links = card.locator("a[href*='/products/']")

    if not await links.count():
        return None

    for index in range(await links.count()):

        href = await links.nth(index).get_attribute("href")

        if href and "/products/" in href:

            return urljoin(BASE_URL, href)

    return None


# ============================================================
# PRODUCT IMAGE
# ============================================================

async def extract_image(card):

    image = card.locator(
        "[data-testid='product-card-image-track'] img"
    )

    if not await image.count():
        image = card.locator("img")

    if not await image.count():
        return None

    image = image.first

    # Prefer the original image URL rather than a small thumbnail.
    for attribute in [
        "data-src",
        "data-lazy-src",
        "src",
        "srcset"
    ]:

        value = await image.get_attribute(attribute)

        if not value:
            continue

        if attribute == "srcset":

            candidates = value.split(",")

            value = candidates[-1].strip().split()[0]

        else:
            value = value.strip()

        if not value:
            continue

        if value.startswith("data:"):
            continue

        if value.startswith("//"):
            return "https:" + value

        return urljoin(BASE_URL, value)

    return None


# ============================================================
# PRICE EXTRACTION
# ============================================================

async def get_first_text(card, selectors):

    for selector in selectors:

        locator = card.locator(selector)

        if not await locator.count():
            continue

        for index in range(await locator.count()):

            try:

                value = normalize_text(
                    await locator.nth(index).inner_text()
                )

                if value:
                    return value

            except Exception:
                continue

    return None


async def extract_prices(card):

    original_price_text = await get_first_text(
        card,
        [
            "[data-testid*='compare-at-price']",
            "[class*='compare-at-price']",
            "[class*='original-price']",
            "[class*='mrp']",
            "del",
            "s"
        ]
    )

    sale_price_text = await get_first_text(
        card,
        [
            "[data-testid*='sale-price']",
            "[data-testid*='price']",
            "[class*='sale-price']",
            "[class*='selling-price']",
            "[class*='current-price']",
            "[class*='price']"
        ]
    )

    original_price = parse_price(original_price_text)
    price = parse_price(sale_price_text)

    # Fallback: extract prices from the product card text.
    if price is None:

        card_text = normalize_text(
            await card.inner_text()
        )

        price_matches = re.findall(
            r"₹\s*[\d,]+(?:\.\d+)?",
            card_text
        )

        if price_matches:

            price = parse_price(price_matches[0])

            if len(price_matches) > 1 and original_price is None:
                original_price = parse_price(
                    price_matches[1]
                )

    return price, original_price


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

async def extract_product_description(card):

    description_selectors = [
        "[data-testid*='product-description']",
        "[data-testid*='description']",
        "[class*='product-description']",
        "[class*='product_desc']",
        "[class*='productDescription']",
        "[class*='description']",
        ".product-card-description",
        ".product-description",
        "p"
    ]

    for selector in description_selectors:

        locator = card.locator(selector)

        if not await locator.count():
            continue

        for index in range(await locator.count()):

            try:

                element = locator.nth(index)

                text = normalize_text(
                    await element.inner_text()
                )

                if not text:
                    continue

                # Avoid accidentally returning the entire card.
                if len(text) < 25:
                    continue

                # Exclude price and rating-only text.
                if re.fullmatch(
                    r"[\d₹,.\s%()]+",
                    text
                ):
                    continue

                return text

            except Exception:
                continue

    return None


# ============================================================
# PRODUCT RATING
# ============================================================

async def extract_rating(card):

    rating_selectors = [
        "[data-testid*='rating']",
        "[class*='rating']",
        "[class*='review-rating']",
        "[aria-label*='star']"
    ]

    for selector in rating_selectors:

        locator = card.locator(selector)

        if not await locator.count():
            continue

        for index in range(await locator.count()):

            try:

                element = locator.nth(index)

                value = await element.get_attribute(
                    "aria-label"
                )

                if not value:
                    value = await element.inner_text()

                rating = parse_rating(value)

                if rating is not None and 0 <= rating <= 5:
                    return rating

            except Exception:
                continue

    return None


# ============================================================
# GET PRODUCT CARD
# ============================================================

async def get_product_cards(page):

    tracks = page.locator(
        "[data-testid='product-card-image-track']"
    )

    track_count = await tracks.count()

    cards = []

    for index in range(track_count):

        track = tracks.nth(index)

        try:

            # Find the nearest ancestor containing a product URL.
            card = track.locator(
                "xpath=ancestor::*[.//a[contains(@href, '/products/')]][1]"
            )

            if not await card.count():
                continue

            card = card.first

            # Avoid duplicate card elements.
            product_link = await extract_product_link(card)

            if not product_link:
                continue

            cards.append(card)

        except Exception as exc:

            logging.warning(
                "Unable to identify product card %s: %s",
                index + 1,
                exc
            )

    return cards


# ============================================================
# MAIN MIVI SCRAPER
# ============================================================

async def scrape_mivi_async():

    results = []

    seen_links = set()
    seen_names = set()

    async with async_playwright() as playwright:

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
                "height": 1080
            },
            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )

        page = await context.new_page()

        page.set_default_timeout(PAGE_TIMEOUT)

        page.set_default_navigation_timeout(
            PAGE_TIMEOUT
        )

        try:

            for category, urls in BASE_URLS.items():

                for base_url in urls:

                    for page_number in range(
                        1,
                        MAX_PAGES + 1
                    ):

                        url = get_page_url(
                            base_url,
                            page_number
                        )

                        logging.info("=" * 60)

                        logging.info(
                            "Fetching Mivi page %s: %s",
                            page_number,
                            url
                        )

                        if page_number > 1:

                            await page.wait_for_timeout(
                                LISTING_PAGE_DELAY * 1000
                            )

                        response = await open_listing_page(
                            page,
                            url
                        )

                        if response is None:

                            logging.warning(
                                "Unable to load: %s",
                                url
                            )

                            break

                        try:

                            await page.wait_for_selector(
                                "[data-testid='product-card-image-track']",
                                timeout=PRODUCT_SELECTOR_TIMEOUT
                            )

                        except Exception:

                            logging.info(
                                "No product cards found on page %s",
                                page_number
                            )

                            break

                        product_cards = await get_product_cards(
                            page
                        )

                        card_count = len(product_cards)

                        if card_count == 0:

                            logging.info(
                                "No products found. Stopping pagination."
                            )

                            break

                        logging.info(
                            "Found %s product cards on page %s",
                            card_count,
                            page_number
                        )

                        for index, card in enumerate(
                            product_cards,
                            start=1
                        ):

                            try:

                                # ------------------------------------
                                # PRODUCT NAME
                                # ------------------------------------

                                name = await extract_product_name(
                                    card
                                )

                                if not name:

                                    logging.warning(
                                        "Product name missing: card %s",
                                        index
                                    )

                                    continue

                                # ------------------------------------
                                # PRODUCT LINK
                                # ------------------------------------

                                product_link = await extract_product_link(
                                    card
                                )

                                if not product_link:
                                    continue

                                # ------------------------------------
                                # DUPLICATE CHECK
                                # ------------------------------------

                                if product_link in seen_links:
                                    continue

                                if name.lower() in seen_names:
                                    continue

                                # ------------------------------------
                                # PRICE
                                # ------------------------------------

                                price_val, original_val = (
                                    await extract_prices(card)
                                )

                                if (
                                    price_val is None
                                    or original_val is None
                                ):

                                    logging.info(
                                        "Skipping %s: price not found",
                                        name
                                    )

                                    continue

                                if price_val > original_val:

                                    logging.info(
                                        "Skipping %s: sale price greater than original price",
                                        name
                                    )

                                    continue

                                # ------------------------------------
                                # DISCOUNT
                                # ------------------------------------

                                discount = calculate_discount(
                                    price_val,
                                    original_val
                                )

                                # Keep only discounted products,
                                # matching the previous scraper logic.
                                if discount is None or discount <= 0:

                                    logging.info(
                                        "Skipping %s: no discount detected",
                                        name
                                    )

                                    continue

                                # ------------------------------------
                                # IMAGE
                                # ------------------------------------

                                image_link = await extract_image(
                                    card
                                )

                                if not image_link:

                                    logging.warning(
                                        "Image not found: %s",
                                        name
                                    )

                                    continue

                                # ------------------------------------
                                # DESCRIPTION
                                # ------------------------------------

                                # Extract directly from the collection card.
                                # No product detail page navigation.
                                description = (
                                    await extract_product_description(
                                        card
                                    )
                                )

                                if description:

                                    logging.info(
                                        "Description extracted: %s",
                                        name
                                    )

                                else:

                                    logging.warning(
                                        "Description not found: %s",
                                        name
                                    )

                                # ------------------------------------
                                # RATING
                                # ------------------------------------

                                rating = await extract_rating(card)

                                # ------------------------------------
                                # FINAL VALUES
                                # ------------------------------------

                                price_val = parse_price(price_val)

                                original_val = parse_price(
                                    original_val
                                )

                                discount_value = parse_discount(
                                    discount
                                )

                                timestamp = get_timestamp()

                                # ------------------------------------
                                # PRODUCT DATA
                                # ------------------------------------

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
                                    "organization_id": ORGANIZATION_ID,
                                    "store_id": STORE_ID,
                                    "categories_id": category,
                                    "created_at": timestamp
                                }

                                results.append(product_data)

                                seen_links.add(product_link)

                                seen_names.add(name.lower())

                                logging.info(
                                    "Scraped: %s | Price: %s | Discount: %s%%",
                                    name,
                                    price_val,
                                    discount_value
                                )

                            except Exception as exc:

                                logging.exception(
                                    "Error processing card %s: %s",
                                    index,
                                    exc
                                )

                        logging.info(
                            "Completed page %s. Total products: %s",
                            page_number,
                            len(results)
                        )

        finally:

            await page.close()
            await context.close()
            await browser.close()

    logging.info("=" * 60)

    logging.info(
        "Total Mivi products scraped: %s",
        len(results)
    )

    return results


# ============================================================
# PROCESS RUNNER
# ============================================================

def run_mivi_scraper():

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True
    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("Starting Mivi scraper...")

    try:

        results = asyncio.run(
            scrape_mivi_async()
        )

        logging.info(
            "Mivi scraper finished. Products: %s",
            len(results)
        )

        return results

    except Exception:

        logging.exception(
            "Mivi scraper failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class MiviSpider(scrapy.Spider):

    name = "mivi"

    allowed_domains = [
        "mivi.in",
        "www.mivi.in",
        "cdn.shopify.com"
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Mivi Playwright scraper..."
        )

        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_mivi_scraper
            )

        # --------------------------------------------
        # SAVE RAW SCRAPED DATA
        # --------------------------------------------

        with open(
            OUTPUT_FILE,
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
            "Saved %s products to %s",
            len(products),
            OUTPUT_FILE
        )

        # --------------------------------------------
        # YIELD TO SCRAPY
        # --------------------------------------------

        for product in products:

            if not product.get("name"):
                continue

            if not product.get("price"):
                continue

            if not product.get("original_price"):
                continue

            if not product.get("image_link"):
                continue

            if not product.get("product_link"):
                continue

            # Description is optional.
            yield product

        self.logger.info("=" * 60)

        self.logger.info(
            "Mivi spider completed."
        )

        self.logger.info("=" * 60)
