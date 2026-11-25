
import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://homeessentials.in"

MAX_PAGES = 1

BASE_URLS = {
    "Home & Kitchen": [
        "https://homeessentials.in/collections/cooking",
        "https://homeessentials.in/collections/kitchen",
    "https://homeessentials.in/collections/honeycomb-cookware",
    "https://homeessentials.in/collections/stainless-steel-cookware"
    "https://homeessentials.in/collections/cast-iron-cookware"
    "https://homeessentials.in/collections/maifan-stone-cookware"
    "https://homeessentials.in/collections/aluminum-alloy-titanium-cookware"
    ],
    "Furnitures": [
        "https://homeessentials.in/collections/wooden-furniture",
        "https://homeessentials.in/collections/coat-racks",
        "https://homeessentials.in/collections/wooden-shoe-racks",
        "https://homeessentials.in/collections/oxford-furniture"
    ],
    "Home Decor": [
        "https://homeessentials.in/collections/home-decor"
    ],
    "Home Appliances": [
        "https://homeessentials.in/collections/bathroom-essentials",
        "https://homeessentials.in/collections/cloth-racks-stand",
        "https://homeessentials.in/collections/trash-cans",
        "https://homeessentials.in/collections/mops-brushes"
    ]
}

STORE_ID = "Home Essentials"
ORGANIZATION_ID = "Dealwallet"

LISTING_PAGE_DELAY = 2
DETAIL_PAGE_DELAY = 2

RATE_LIMIT_DELAY = 3
MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000

IST = timezone(timedelta(hours=5, minutes=30))


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    return " ".join(str(text).split())


def normalize_product_name(name):

    if not name:
        return ""

    return normalize_text(name).strip()


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


def clean_to_int(value):

    if value is None:
        return 0

    try:
        return int(round(float(value)))
    except (ValueError, TypeError):
        return 0


def parse_rating(value):

    if value is None:
        return None

    try:
        rating = float(value)

        if rating.is_integer():
            return int(rating)

        return rating

    except (ValueError, TypeError):
        return None


def get_ist_timestamp():

    return datetime.now(IST).strftime(
        "%Y-%m-%d"
    )


def get_page_url(base_url, page_number):

    if page_number == 1:
        return base_url

    parsed = urlparse(base_url)

    params = parse_qs(parsed.query)

    params["page"] = [str(page_number)]

    query = urlencode(params, doseq=True)

    return urlunparse(
        parsed._replace(query=query)
    )


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

def extract_product_description(product_html):

    if not product_html:
        return None

    soup = BeautifulSoup(
        product_html,
        "html.parser"
    )

    selectors = [
        ".product__description",
        ".product-description",
        ".product-description-wrapper",
        ".product-single__description",
        ".product__info-description",
        ".product__accordion",
        ".product-details",
        "[class*='product-description']",
    ]

    for selector in selectors:

        description_tag = soup.select_one(selector)

        if not description_tag:
            continue

        for tag in description_tag.select(
            "script, style, svg, img, button"
        ):
            tag.decompose()

        description = normalize_text(
            description_tag.get_text(" ", strip=True)
        )

        if description:
            return description

    # Fallback: Meta description

    meta_description = soup.select_one(
        'meta[name="description"]'
    )

    if meta_description:

        description = normalize_text(
            meta_description.get("content")
        )

        if description:
            return description

    # Fallback: Open Graph description

    og_description = soup.select_one(
        'meta[property="og:description"]'
    )

    if og_description:

        description = normalize_text(
            og_description.get("content")
        )

        if description:
            return description

    # Fallback: JSON-LD

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:
            data = json.loads(script.string or script.get_text())

        except Exception:
            continue

        objects = []

        if isinstance(data, dict):

            objects.append(data)

            if isinstance(data.get("@graph"), list):
                objects.extend(data["@graph"])

        elif isinstance(data, list):

            objects.extend(data)

        for item in objects:

            if isinstance(item, dict):

                description = normalize_text(
                    item.get("description")
                )

                if description:
                    description = re.sub(
                        r"^\s*Description\s*",
                        "",
                        description,
                        flags=re.IGNORECASE
                    ).strip()

                    return description

    return None


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(card):

    selectors = [
        ".product-card-title",
        ".product-card-info a",
        "a.product-featured-image-link",
        ".product-featured-image-link",
    ]

    for selector in selectors:

        tag = card.select_one(selector)

        if not tag:
            continue

        name = normalize_product_name(
            tag.get("title") or tag.get_text(" ", strip=True)
        )

        if name:
            return name

    image = card.select_one("img")

    if image:
        return normalize_product_name(
            image.get("alt")
        )

    return None


# ============================================================
# PRODUCT LINK
# ============================================================

def extract_product_link(card):

    selectors = [
        "a.product-featured-image-link",
        ".product-card-title",
        "a[href*='/products/']",
    ]

    for selector in selectors:

        tag = card.select_one(selector)

        if not tag:
            continue

        href = tag.get("href")

        if href and "/products/" in href:

            return urljoin(BASE_URL, href)

    return None


# ============================================================
# PRODUCT IMAGE
# ============================================================

def extract_image(card):

    image_tag = card.select_one(
        "img.product-primary-image"
    )

    if not image_tag:
        image_tag = card.select_one("img")

    if not image_tag:
        return None

    attributes = [
        "src",
        "data-src",
        "data-lazy-src",
        "data-original",
    ]

    for attribute in attributes:

        image_url = image_tag.get(attribute)

        if image_url:

            image_url = image_url.strip()

            if image_url.startswith("data:"):
                continue

            return urljoin(BASE_URL, image_url)

    # Fallback: srcset

    srcset = image_tag.get("data-srcset") or image_tag.get(
        "srcset"
    )

    if srcset:

        image_url = srcset.split(",")[-1].strip().split()[0]

        return urljoin(BASE_URL, image_url)

    return None


# ============================================================
# PRODUCT PRICES
# ============================================================

def extract_prices(card):

    sale_price_text = None
    original_price_text = None

    sale_tag = card.select_one(
        "span.amount.discounted"
    )

    original_tag = card.select_one(
        "del .amount"
    )

    if sale_tag:

        sale_price_text = sale_tag.get_text(
            " ",
            strip=True
        )

    if original_tag:

        original_price_text = original_tag.get_text(
            " ",
            strip=True
        )

    sale_price = parse_price_to_float(
        sale_price_text
    )

    original_price = parse_price_to_float(
        original_price_text
    )

    return sale_price, original_price


# ============================================================
# PRODUCT VARIANTS JSON
# ============================================================

def extract_variant_data(card):

    variant_tag = card.select_one(
        'div[name="product-variants-json"]'
    )

    if not variant_tag:
        return None

    raw_json = variant_tag.get_text(
        strip=True
    )

    if not raw_json:
        return None

    try:

        variants = json.loads(raw_json)

        if isinstance(variants, list) and variants:

            return variants[0]

    except Exception as exc:

        logging.warning(
            "Unable to parse product variants: %s",
            exc
        )

    return None


# ============================================================
# PRODUCT RATING
# ============================================================

def extract_rating(card):

    rating_tag = card.select_one(
        ".jdgm-prev-badge"
    )

    if not rating_tag:
        return None

    rating = rating_tag.get(
        "data-average-rating"
    )

    return parse_rating(rating)


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
                "Opening listing page (%s/%s): %s",
                attempt,
                MAX_429_RETRIES,
                url
            )

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            if response:

                status = response.status

                logging.info(
                    "Listing HTTP status: %s",
                    status
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        logging.warning(
                            "HTTP 429. Waiting %s seconds.",
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
                "Failed listing page %s: %s",
                url,
                exc
            )

            if attempt < MAX_429_RETRIES:

                await page.wait_for_timeout(
                    DETAIL_PAGE_DELAY * 1000
                )

    return None


# ============================================================
# OPEN PRODUCT DETAIL PAGE WITH 429 RETRY
# ============================================================

async def open_product_page(product_page, product_link):

    for attempt in range(
        1,
        MAX_429_RETRIES + 1
    ):

        try:

            response = await product_page.goto(
                product_link,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            if response:

                status = response.status

                logging.info(
                    "Product page HTTP status: %s",
                    status
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        await product_page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    return None

            return response

        except Exception as exc:

            logging.warning(
                "Failed product page %s: %s",
                product_link,
                exc
            )

            if attempt < MAX_429_RETRIES:

                await product_page.wait_for_timeout(
                    DETAIL_PAGE_DELAY * 1000
                )

    return None


# ============================================================
# SCRAPE HOME ESSENTIALS
# ============================================================

async def scrape_home_essentials_async():

    results = []

    seen_links = set()
    seen_names = set()

    category_counts = {}

    async with async_playwright() as playwright:

        # ----------------------------------------------------
        # BROWSER
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # CATEGORY LOOP
        # ----------------------------------------------------

        for category, urls in BASE_URLS.items():

            category_results = []

            for base_url in urls:

                page_number = 1

                while page_number <= MAX_PAGES:

                    url = get_page_url(
                        base_url,
                        page_number
                    )

                    logging.info("=" * 60)

                    logging.info(
                        "Fetching category: %s",
                        category
                    )

                    logging.info(
                        "Page: %s",
                        page_number
                    )

                    logging.info(
                        "URL: %s",
                        url
                    )

                    # ------------------------------------------------
                    # DELAY
                    # ------------------------------------------------

                    if page_number > 1:

                        await page.wait_for_timeout(
                            LISTING_PAGE_DELAY * 1000
                        )

                    # ------------------------------------------------
                    # OPEN LISTING PAGE
                    # ------------------------------------------------

                    response = await open_listing_page(
                        page,
                        url
                    )

                    if response is None:

                        logging.warning(
                            "Unable to fetch: %s",
                            url
                        )

                        break

                    # ------------------------------------------------
                    # WAIT FOR PRODUCT GRID
                    # ------------------------------------------------

                    try:

                        await page.wait_for_selector(
                            "#product-grid li.column",
                            timeout=PRODUCT_SELECTOR_TIMEOUT
                        )

                    except Exception:

                        logging.warning(
                            "Product grid not found: %s",
                            url
                        )

                        break

                    # ------------------------------------------------
                    # PARSE LISTING HTML
                    # ------------------------------------------------

                    html = await page.content()

                    soup = BeautifulSoup(
                        html,
                        "html.parser"
                    )

                    product_cards = soup.select(
                        "#product-grid li.column"
                    )

                    card_count = len(product_cards)

                    logging.info(
                        "Found %s product cards.",
                        card_count
                    )

                    if card_count == 0:
                        break

                    # ------------------------------------------------
                    # PRODUCT LOOP
                    # ------------------------------------------------

                    for index, card in enumerate(
                        product_cards,
                        start=1
                    ):

                        try:

                            # ----------------------------------------
                            # PRODUCT NAME
                            # ----------------------------------------

                            name = extract_product_name(
                                card
                            )

                            if not name:

                                logging.warning(
                                    "Product name missing: card %s",
                                    index
                                )

                                continue

                            # ----------------------------------------
                            # PRODUCT LINK
                            # ----------------------------------------

                            product_link = extract_product_link(
                                card
                            )

                            if not product_link:
                                continue

                            # ----------------------------------------
                            # DUPLICATE CHECK
                            # ----------------------------------------

                            if product_link in seen_links:
                                continue

                            if name.lower() in seen_names:
                                continue

                            # ----------------------------------------
                            # PRICE EXTRACTION
                            # ----------------------------------------

                            sale_price, original_price = (
                                extract_prices(card)
                            )

                            # ----------------------------------------
                            # VARIANT FALLBACK
                            # ----------------------------------------

                            variant_data = extract_variant_data(
                                card
                            )

                            if variant_data:

                                variant_price = variant_data.get(
                                    "price"
                                )

                                variant_original = variant_data.get(
                                    "compare_at_price"
                                )

                                if sale_price is None and variant_price is not None:

                                    sale_price = (
                                        variant_price / 100
                                    )

                                if original_price is None and variant_original is not None:

                                    original_price = (
                                        variant_original / 100
                                    )

                            # ----------------------------------------
                            # PRICE VALIDATION
                            # ----------------------------------------

                            if (
                                sale_price is None
                                or original_price is None
                            ):

                                logging.info(
                                    "Skipping %s: price missing.",
                                    name
                                )

                                continue

                            if sale_price <= 0 or original_price <= 0:
                                continue

                            if sale_price > original_price:

                                logging.info(
                                    "Skipping %s: sale price greater than original price.",
                                    name
                                )

                                continue

                            # ----------------------------------------
                            # DISCOUNT
                            # ----------------------------------------

                            discount = round(
                                (
                                    (
                                        original_price - sale_price
                                    )
                                    / original_price
                                ) * 100
                            )

                            if discount <= 0:

                                logging.info(
                                    "Skipping %s: no discount.",
                                    name
                                )

                                continue

                            # ----------------------------------------
                            # IMAGE
                            # ----------------------------------------

                            image_link = extract_image(
                                card
                            )

                            if not image_link:

                                logging.warning(
                                    "Image missing: %s",
                                    name
                                )

                                continue

                            # ----------------------------------------
                            # RATING
                            # ----------------------------------------

                            rating = extract_rating(
                                card
                            )

                            # ----------------------------------------
                            # DESCRIPTION
                            # ----------------------------------------

                            description = None

                            await page.wait_for_timeout(
                                DETAIL_PAGE_DELAY * 1000
                            )

                            product_page = await context.new_page()

                            product_page.set_default_timeout(
                                PAGE_TIMEOUT
                            )

                            product_page.set_default_navigation_timeout(
                                PAGE_TIMEOUT
                            )

                            try:

                                detail_response = await open_product_page(
                                    product_page,
                                    product_link
                                )

                                if detail_response:

                                    await product_page.wait_for_timeout(
                                        2000
                                    )

                                    product_html = (
                                        await product_page.content()
                                    )

                                    description = (
                                        extract_product_description(
                                            product_html
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

                            except Exception as exc:

                                logging.warning(
                                    "Description extraction failed for %s: %s",
                                    name,
                                    exc
                                )

                            finally:

                                await product_page.close()

                            # ----------------------------------------
                            # FINAL VALIDATION
                            # ----------------------------------------

                            if not name:
                                continue

                            if not image_link:
                                continue

                            if not product_link:
                                continue

                            # Description is optional.

                            # ----------------------------------------
                            # FINAL CLEANING
                            # ----------------------------------------

                            sale_price = clean_to_int(
                                sale_price
                            )

                            original_price = clean_to_int(
                                original_price
                            )

                            discount = clean_to_int(
                                discount
                            )

                            # ----------------------------------------
                            # TIMESTAMP
                            # ----------------------------------------

                            timestamp = get_ist_timestamp()
                            description = description = description.replace("Description ", "", 1).strip()

                            # ----------------------------------------
                            # FINAL PRODUCT
                            # ----------------------------------------

                            product_data = {

                                "name": name,

                                "price": sale_price,

                                "currency": "₹",

                                "original_price": original_price,

                                "discount": discount,

                                "ratings": rating,

                                "description": description,

                                "image_link": image_link,

                                "product_link": product_link,

                                "organization_id": ORGANIZATION_ID,

                                "store_id": STORE_ID,

                                "categories_id": category,

                                "created_at": timestamp,

                            }

                            # ----------------------------------------
                            # SAVE PRODUCT
                            # ----------------------------------------

                            results.append(
                                product_data
                            )

                            category_results.append(
                                product_data
                            )

                            seen_links.add(
                                product_link
                            )

                            seen_names.add(
                                name.lower()
                            )

                            logging.info(
                                "Scraped successfully: %s | Price: %s | Discount: %s%%",
                                name,
                                sale_price,
                                discount
                            )

                        except Exception as exc:

                            logging.exception(
                                "Error parsing product card %s on page %s: %s",
                                index,
                                page_number,
                                exc
                            )

                    # ------------------------------------------------
                    # NEXT PAGE
                    # ------------------------------------------------

                    page_number += 1

            # ----------------------------------------------------
            # CATEGORY SUMMARY
            # ----------------------------------------------------

            category_counts[category] = len(
                category_results
            )

            logging.info(
                "Total products scraped for %s: %s",
                category,
                len(category_results)
            )

        # ----------------------------------------------------
        # CLOSE BROWSER
        # ----------------------------------------------------

        await page.close()

        await context.close()

        await browser.close()

    logging.info("=" * 60)

    logging.info(
        "Total Home Essentials products scraped: %s",
        len(results)
    )

    logging.info(
        "Category summary: %s",
        category_counts
    )

    logging.info("=" * 60)

    return results


# ============================================================
# WINDOWS PROCESS RUNNER
# ============================================================

def run_home_essentials_scraper():

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

    logging.info("=" * 60)

    logging.info(
        "Home Essentials browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_home_essentials_async()
        )

        logging.info(
            "Home Essentials browser process completed. Products: %s",
            len(results)
        )

        return results

    except Exception:

        logging.exception(
            "Home Essentials scraper failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class HomeEssentialsSpider(scrapy.Spider):

    name = "home_essentials"

    allowed_domains = [
        "homeessentials.in",
        "www.homeessentials.in",
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Home Essentials Playwright scraper..."
        )

        self.logger.info("=" * 60)

        # ----------------------------------------------------
        # RUN PLAYWRIGHT IN SEPARATE PROCESS
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_home_essentials_scraper
            )

        # ----------------------------------------------------
        # SAVE RAW SCRAPED DATA
        # ----------------------------------------------------

        with open(
            "scrape_home_essentials.json",
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
            "Home Essentials scraper returned %s products.",
            len(products)
        )

        # ----------------------------------------------------
        # YIELD TO SCRAPY
        # ----------------------------------------------------

        for product in products:

            if not product.get("image_link"):
                continue

            if not product.get("product_link"):
                continue

            yield product

        self.logger.info("=" * 60)

        self.logger.info(
            "Home Essentials spider completed."
        )

        self.logger.info("=" * 60)
