
import asyncio
import html
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

BASE_URL = "https://www.savana.com"

BASE_URLS = {
    "Fashion & Lifestyle": [
        # "https://www.savana.com/activity/stylish-tops-13140?9=30_50%2C50_70",
        # "https://www.savana.com/activity/blouses-and-shirts-12848?9=30_50%2C50_70",
        # "https://www.savana.com/activity/basic-tops-13141?9=30_50%2C50_70",
        # "https://www.savana.com/activity/t-shirts-12847?9=30_50%2C50_70",
        "https://www.savana.com/activity/sweatshirts-and-hoodies-11527?9=50_70%2C30_50",
        "https://www.savana.com/activity/pullovers-13212?9=50_70%2C30_50",
        "https://www.savana.com/activity/birthday-dresses-12827?9=50_70%2C30_50",
        "https://www.savana.com/activity/daily-dresses-12838?9=50_70%2C30_50",
        "https://www.savana.com/activity/party-dresses-12835?9=50_70%2C30_50",
        "https://www.savana.com/activity/vacation-dresses-12836?9=50_70%2C30_50",
        "https://www.savana.com/activity/trousers-12878?9=50_70%2C30_50",
        "https://www.savana.com/activity/skirts-12882?9=50_70%2C30_50",
        "https://www.savana.com/activity/jeans-12879?9=50_70%2C30_50",
        "https://www.savana.com/activity/shorts-12883?9=50_70%2C30_50",
        "https://www.savana.com/activity/joggers-12881?9=50_70%2C30_50"
    ]
}

STORE_ID = "Savana"
ORGANIZATION_ID = "Dealwallet"

LISTING_PAGE_DELAY = 2
DETAIL_PAGE_DELAY = 2
SCROLL_DELAY = 2

RATE_LIMIT_DELAY = 3
MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000

MAX_SCROLLS = 2

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
        return None

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

    return datetime.now(IST).strftime("%Y-%m-%d")


def get_absolute_url(url):

    if not url:
        return None

    return urljoin(BASE_URL, url.strip())


# ============================================================
# SAVANA CARD DATA
# ============================================================

def extract_card_json(card):

    anchor = card.select_one("a.savana-card")

    if not anchor:
        return {}

    raw_data = anchor.get("data-bee-show")

    if not raw_data:
        return {}

    try:
        raw_data = html.unescape(raw_data)
        data = json.loads(raw_data)

        if isinstance(data, dict):
            return data

    except Exception as exc:

        logging.warning(
            "Unable to parse Savana card JSON: %s",
            exc
        )

    return {}


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(card):

    title_tag = card.select_one(".title")

    if title_tag:

        name = normalize_product_name(
            title_tag.get_text(" ", strip=True)
        )

        if name:
            return name

    image_tag = card.select_one(".ub-image img")

    if image_tag:

        name = normalize_product_name(
            image_tag.get("alt")
        )

        if name:
            return name

    return None


# ============================================================
# PRODUCT LINK
# ============================================================

def extract_product_link(card):

    anchor = card.select_one("a.savana-card")

    if not anchor:
        return None

    href = anchor.get("href")

    if not href:
        return None

    return get_absolute_url(href)


# ============================================================
# PRODUCT IMAGE
# ============================================================

def extract_image(card, card_data):

    image_tag = card.select_one(".ub-image img")

    if image_tag:

        attributes = [
            "src",
            "data-src",
            "data-original",
            "data-lazy-src"
        ]

        for attribute in attributes:

            image_url = image_tag.get(attribute)

            if image_url:

                image_url = image_url.strip()

                if (
                    image_url
                    and not image_url.startswith("data:")
                ):
                    return get_absolute_url(image_url)

    # Fallback: image URL from Savana card JSON

    image_url = card_data.get("url")

    if image_url:
        return get_absolute_url(image_url)

    # Fallback: hidden product image data

    hidden_data = card.select_one(".ub-image > div[style*='display']")

    if hidden_data:

        raw_text = hidden_data.get_text(strip=True)

        try:

            image_data = json.loads(raw_text)

            image_url = (
                image_data.get("goodsThumb")
                or image_data.get("skcMainImg")
            )

            if image_url:
                return get_absolute_url(image_url)

        except Exception:
            pass

    return None


# ============================================================
# PRODUCT PRICES
# ============================================================

def extract_prices(card, card_data):

    sale_price = None
    original_price = None

    # Current selling price

    sale_tag = card.select_one(".pcPriceRow span")

    if sale_tag:

        sale_price = parse_price_to_float(
            sale_tag.get_text(" ", strip=True)
        )

    # Original price

    original_tag = card.select_one(".priceCross")

    if original_tag:

        original_price = parse_price_to_float(
            original_tag.get_text(" ", strip=True)
        )

    # Fallback: data-bee-show JSON

    if sale_price is None:

        sale_price = parse_price_to_float(
            card_data.get("promotePriceText")
        )

    if original_price is None:

        original_price = parse_price_to_float(
            card_data.get("salesPriceText")
        )

    return sale_price, original_price


# ============================================================
# PRODUCT DISCOUNT
# ============================================================

def extract_discount(card, card_data, sale_price, original_price):

    discount_text = card_data.get("discountValueText")

    if not discount_text:

        discount_tag = card.select_one(
            ".pcPriceRow span:nth-child(3)"
        )

        if discount_tag:

            discount_text = discount_tag.get_text(
                " ",
                strip=True
            )

    if discount_text:

        match = re.search(
            r"(\d+(?:\.\d+)?)\s*%",
            str(discount_text)
        )

        if match:

            return clean_to_int(
                float(match.group(1))
            )

    if (
        sale_price is not None
        and original_price is not None
        and original_price > 0
    ):

        return clean_to_int(
            ((original_price - sale_price) / original_price) * 100
        )

    return 0


# ============================================================
# PRODUCT RATING
# ============================================================

def extract_rating(card):

    rating_selectors = [
        "[data-average-rating]",
        ".rating",
        ".rating-value",
        "[class*='rating']"
    ]

    for selector in rating_selectors:

        rating_tag = card.select_one(selector)

        if not rating_tag:
            continue

        rating_value = (
            rating_tag.get("data-average-rating")
            or rating_tag.get("data-rating")
            or rating_tag.get_text(" ", strip=True)
        )

        rating = parse_rating(rating_value)

        if rating is not None:
            return rating

    return None


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
        ".product-description",
        ".product__description",
        ".goods-description",
        ".goods-detail-description",
        ".product-detail-description",
        ".detail-description",
        "[class*='description']",
        "[class*='Description']",
        "[class*='detail-content']",
        "[class*='goods-detail']"
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

            description = re.sub(
                r"^\s*Description\s*",
                "",
                description,
                count=1,
                flags=re.IGNORECASE
            ).strip()

            if description:
                return description

    # Meta description fallback

    meta_description = soup.select_one(
        'meta[name="description"]'
    )

    if meta_description:

        description = normalize_text(
            meta_description.get("content")
        )

        if description:
            return description

    # Open Graph description fallback

    og_description = soup.select_one(
        'meta[property="og:description"]'
    )

    if og_description:

        description = normalize_text(
            og_description.get("content")
        )

        if description:
            return description

    # JSON-LD fallback

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        try:

            data = json.loads(
                script.string or script.get_text()
            )

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

            if not isinstance(item, dict):
                continue

            description = normalize_text(
                item.get("description")
            )

            if description:

                description = re.sub(
                    r"^\s*Description\s*",
                    "",
                    description,
                    count=1,
                    flags=re.IGNORECASE
                ).strip()

                if description:
                    return description

    return None


# ============================================================
# OPEN LISTING PAGE WITH 429 RETRY
# ============================================================

async def open_listing_page(page, url):

    for attempt in range(1, MAX_429_RETRIES + 1):

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
                    LISTING_PAGE_DELAY * 1000
                )

    return None


# ============================================================
# OPEN PRODUCT DETAIL PAGE WITH 429 RETRY
# ============================================================

async def open_product_page(product_page, product_link):

    for attempt in range(1, MAX_429_RETRIES + 1):

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

                        logging.warning(
                            "Product page rate limited. Waiting %s seconds.",
                            RATE_LIMIT_DELAY
                        )

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
# LOAD MORE PRODUCTS BY SCROLLING
# ============================================================

async def load_listing_products(page):

    previous_count = 0
    unchanged_count = 0

    for scroll_number in range(1, MAX_SCROLLS + 1):

        await page.evaluate(
            "window.scrollTo(0, document.body.scrollHeight)"
        )

        await page.wait_for_timeout(
            SCROLL_DELAY * 1000
        )

        current_count = await page.locator(
            "li[data-id] a.savana-card"
        ).count()

        logging.info(
            "Scroll %s/%s - Products found: %s",
            scroll_number,
            MAX_SCROLLS,
            current_count
        )

        if current_count == previous_count:

            unchanged_count += 1

        else:

            unchanged_count = 0

        if unchanged_count >= 2:
            break

        previous_count = current_count

    return await page.content()


# ============================================================
# SCRAPE SAVANA
# ============================================================

async def scrape_savana_async():

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
                "height": 1080
            },

            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )

        page = await context.new_page()

        page.set_default_timeout(
            PAGE_TIMEOUT
        )

        page.set_default_navigation_timeout(
            PAGE_TIMEOUT
        )

        product_page = await context.new_page()

        product_page.set_default_timeout(
            PAGE_TIMEOUT
        )

        product_page.set_default_navigation_timeout(
            PAGE_TIMEOUT
        )

        # ----------------------------------------------------
        # CATEGORY LOOP
        # ----------------------------------------------------

        for category, urls in BASE_URLS.items():

            category_results = []

            for url in urls:

                logging.info("=" * 60)

                logging.info(
                    "Fetching Savana category: %s",
                    category
                )

                logging.info(
                    "URL: %s",
                    url
                )

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
                        "Unable to fetch listing page: %s",
                        url
                    )

                    continue

                # ------------------------------------------------
                # WAIT FOR PRODUCT CARDS
                # ------------------------------------------------

                try:

                    await page.wait_for_selector(
                        "li[data-id] a.savana-card",
                        timeout=PRODUCT_SELECTOR_TIMEOUT
                    )

                except Exception:

                    logging.warning(
                        "Savana product cards not found: %s",
                        url
                    )

                    continue

                # ------------------------------------------------
                # LOAD LISTING PRODUCTS
                # ------------------------------------------------

                html_content = await load_listing_products(
                    page
                )

                soup = BeautifulSoup(
                    html_content,
                    "html.parser"
                )

                product_cards = soup.select(
                    "li[data-id]"
                )

                logging.info(
                    "Found %s product cards.",
                    len(product_cards)
                )

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
                        # CARD JSON
                        # ----------------------------------------

                        card_data = extract_card_json(
                            card
                        )

                        # ----------------------------------------
                        # PRODUCT PRICE
                        # ----------------------------------------

                        sale_price, original_price = extract_prices(
                            card,
                            card_data
                        )

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

                        discount = extract_discount(
                            card,
                            card_data,
                            sale_price,
                            original_price
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
                            card,
                            card_data
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
                        # PRODUCT DESCRIPTION
                        # ----------------------------------------

                        description = None

                        await page.wait_for_timeout(
                            DETAIL_PAGE_DELAY * 1000
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

                        # ----------------------------------------
                        # FINAL VALIDATION
                        # ----------------------------------------

                        if not name:
                            continue

                        if not image_link:
                            continue

                        if not product_link:
                            continue

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

                            "created_at": timestamp

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
                            "Error parsing product card %s: %s",
                            index,
                            exc
                        )

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

        await product_page.close()

        await page.close()

        await context.close()

        await browser.close()

    logging.info("=" * 60)

    logging.info(
        "Total Savana products scraped: %s",
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

def run_savana_scraper():

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
        "Savana browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_savana_async()
        )

        logging.info(
            "Savana browser process completed. Products: %s",
            len(results)
        )

        return results

    except Exception:

        logging.exception(
            "Savana scraper failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class SavanaSpider(scrapy.Spider):

    name = "savana"

    allowed_domains = [
        "savana.com",
        "www.savana.com",
        "mfrcdn.com",
        "img101.mfrcdn.com"
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Savana Playwright scraper..."
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
                run_savana_scraper
            )

        # ----------------------------------------------------
        # SAVE RAW SCRAPED DATA
        # ----------------------------------------------------

        with open(
            "scrape_savana.json",
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
            "Savana scraper returned %s products.",
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
            "Savana spider completed."
        )

        self.logger.info("=" * 60)
