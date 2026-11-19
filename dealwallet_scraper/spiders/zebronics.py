
import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://shop.zebronics.com"

MAX_PAGES = 10

BASE_URLS = {
    "Electronics": [
        "https://shop.zebronics.com/collections/soundbars",
        "https://shop.zebronics.com/collections/dj-speakers",
        "https://shop.zebronics.com/collections/wireless-speakers",
        "https://shop.zebronics.com/collections/accessories",
        "https://shop.zebronics.com/collections/led-monitor",
        "https://shop.zebronics.com/collections/projectors",
        "https://shop.zebronics.com/collections/portable-fan",
       
    ]
}


# ============================================================
# REQUEST / RATE LIMIT SETTINGS
# ============================================================

LISTING_PAGE_DELAY = 3
DETAIL_PAGE_DELAY = 2

RATE_LIMIT_DELAY = 30
MAX_429_RETRIES = 3

PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    return " ".join(str(text).split())


def clean_to_int(value):

    if value is None:
        return 0

    try:
        return int(round(float(value)))

    except (ValueError, TypeError):
        return 0


def parse_price_to_float(price_text):

    if not price_text:
        return None

    clean = re.sub(r"[^\d.]", "", str(price_text))

    if not clean:
        return None

    try:
        value = float(clean)

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def extract_prices(text):

    if not text:
        return []

    pattern = r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)"

    matches = re.findall(
        pattern,
        str(text),
        flags=re.IGNORECASE
    )

    prices = []

    for value in matches:

        price = parse_price_to_float(value)

        if price is not None and price not in prices:
            prices.append(price)

    return prices


def extract_discount(text, price, original_price):

    if text:

        match = re.search(
            r"(?:Save|You save)\s*([\d.]+)\s*%",
            str(text),
            flags=re.IGNORECASE
        )

        if match:

            try:
                return int(round(float(match.group(1))))
            except ValueError:
                pass

    if (
        price is not None
        and original_price is not None
        and original_price > price
        and original_price > 0
    ):

        discount = (
            (original_price - price) / original_price
        ) * 100

        return int(round(discount))

    return 0


def get_ist_timestamp():

    return datetime.now(
        timezone(timedelta(hours=5, minutes=30))
    ).strftime("%Y-%m-%dT%H:%M:%S")


def build_page_url(base_url, page_number):

    parsed_url = urlparse(base_url)

    query_params = parse_qs(parsed_url.query)

    query_params["page"] = [str(page_number)]

    updated_query = urlencode(
        query_params,
        doseq=True
    )

    return urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        updated_query,
        parsed_url.fragment
    ))


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

    sections = []

    description_selectors = [
        ".product__description",
        ".product-description",
        ".product__info-description",
        ".product-single__description",
        ".product-description-wrapper",
        ".product__content",
        "[class*='product-description']",
        "[id*='description']",
        ".rte",
    ]

    for selector in description_selectors:

        description_tag = product_soup.select_one(selector)

        if not description_tag:
            continue

        for tag in description_tag.select(
            "script, style, svg, img, button, iframe"
        ):
            tag.decompose()

        text = normalize_text(
            description_tag.get_text(" ", strip=True)
        )

        if text and len(text) > 20:
            sections.append(text)
            break

    # META DESCRIPTION

    if not sections:

        meta_description = product_soup.select_one(
            'meta[name="description"]'
        )

        if meta_description:

            content = normalize_text(
                meta_description.get("content")
            )

            if content:
                sections.append(content)

    # OG DESCRIPTION

    if not sections:

        og_description = product_soup.select_one(
            'meta[property="og:description"]'
        )

        if og_description:

            content = normalize_text(
                og_description.get("content")
            )

            if content:
                sections.append(content)

    # JSON-LD DESCRIPTION

    if not sections:

        scripts = product_soup.select(
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

            json_objects = []

            if isinstance(data, dict):

                json_objects.append(data)

                if isinstance(data.get("@graph"), list):
                    json_objects.extend(data["@graph"])

            elif isinstance(data, list):

                json_objects.extend(data)

            for item in json_objects:

                if not isinstance(item, dict):
                    continue

                description = item.get("description")

                if not description:
                    continue

                description = normalize_text(description)

                if description:
                    sections.append(description)
                    break

            if sections:
                break

    if not sections:
        return None

    return " || ".join(sections).strip()


# ============================================================
# PRODUCT RATING - DETAIL PAGE
# ============================================================

def extract_product_rating(product_html):

    if not product_html:
        return None, 0

    product_soup = BeautifulSoup(
        product_html,
        "html.parser"
    )

    rating_element = product_soup.select_one(
        ".jdgm-prev-badge"
    )

    if not rating_element:
        return None, 0

    rating_value = rating_element.get(
        "data-average-rating"
    )

    review_count_value = rating_element.get(
        "data-number-of-reviews"
    )

    ratings = None
    number_of_reviews = 0

    if rating_value:

        try:

            ratings = float(rating_value)

            if ratings.is_integer():
                ratings = int(ratings)

        except (ValueError, TypeError):
            ratings = None

    if review_count_value:

        try:
            number_of_reviews = int(review_count_value)

        except (ValueError, TypeError):
            number_of_reviews = 0

    return ratings, number_of_reviews


# ============================================================
# PRODUCT CARD EXTRACTION
# ============================================================

async def extract_product_cards(page):

    products = await page.locator(
        'a[href*="/products/"]'
    ).evaluate_all("""
        anchors => {

            const results = [];

            for (const anchor of anchors) {

                const href = anchor.getAttribute("href");

                if (!href || !href.includes("/products/")) {
                    continue;
                }

                let card = anchor;

                for (let i = 0; i < 10 && card; i++) {

                    const text = card.innerText || "";

                    const hasPrice =
                        /₹|Rs\\.?|INR/i.test(text);

                    const hasImage =
                        card.querySelector("img") !== null;

                    if (
                        hasPrice &&
                        hasImage &&
                        text.trim().length > 15
                    ) {
                        break;
                    }

                    card = card.parentElement;
                }

                if (!card) {
                    continue;
                }

                const title =
                    anchor.innerText?.trim() ||
                    anchor.getAttribute("title") ||
                    anchor.getAttribute("aria-label") ||
                    card.querySelector("img")?.getAttribute("alt") ||
                    "";

                const images = Array.from(
                    card.querySelectorAll("img")
                );

                let image = "";

                for (const img of images) {

                    image =
                        img.getAttribute("src") ||
                        img.getAttribute("data-src") ||
                        img.getAttribute("data-original") ||
                        img.getAttribute("data-lazy-src") ||
                        "";

                    if (!image) {

                        const srcset =
                            img.getAttribute("srcset") ||
                            img.getAttribute("data-srcset") ||
                            "";

                        if (srcset) {

                            image = srcset
                                .split(",")[0]
                                .trim()
                                .split(" ")[0];
                        }
                    }

                    if (
                        image &&
                        !image.startsWith("data:")
                    ) {
                        break;
                    }
                }

                const cardText = card.innerText || "";

                const soldOut = (
                    /sold out|out of stock/i.test(cardText) ||
                    card.querySelector(
                        '[class*="sold-out"], [class*="sold_out"]'
                    ) !== null
                );

                results.push({

                    title: title,

                    url: anchor.href,

                    image: image,

                    text: cardText,

                    sold_out: soldOut
                });
            }

            return results;
        }
    """)

    unique_products = {}

    for product in products:

        product_url = product.get("url", "").split("?")[0]

        if not product_url:
            continue

        if product_url not in unique_products:

            product["url"] = product_url

            unique_products[product_url] = product

    return list(unique_products.values())


# ============================================================
# PREPARE PRODUCT
# ============================================================

def prepare_product(raw_product):

    name = normalize_text(raw_product.get("title"))

    if not name:
        return None

    product_url = raw_product.get("url")

    if not product_url:
        return None

    product_url = product_url.split("?")[0]

    image_link = raw_product.get("image")

    if image_link:
        image_link = urljoin(BASE_URL, image_link)
    else:
        image_link = None

    if raw_product.get("sold_out"):
        return None

    product_text = raw_product.get("text", "")

    prices = extract_prices(product_text)

    if not prices:
        return None

    price = prices[0]

    original_price = None

    if len(prices) > 1:

        possible_original_price = max(prices)

        if possible_original_price > price:
            original_price = possible_original_price

    if price is None or price <= 0:
        return None

    if (
        original_price is not None
        and price > original_price
    ):
        return None

    discount = extract_discount(
        product_text,
        price,
        original_price
    )

    return {

        "name": name,

        "price": clean_to_int(price),

        "currency": "₹",

        "original_price": (
            clean_to_int(original_price)
            if original_price is not None
            else None
        ),

        "discount": discount,

        "ratings": None,

        "number_of_reviews": 0,

        "description": None,

        "image_link": image_link,

        "product_link": product_url,

        "organization_id": "Dealwallet",

        "store_id": "Zebronics",

        "categories_id": "Electronics",

        "created_at": get_ist_timestamp(),
    }


# ============================================================
# OPEN LISTING PAGE WITH 429 RETRY
# ============================================================

async def open_listing_page(page, url):

    for attempt in range(1, MAX_429_RETRIES + 1):

        try:

            logging.info(
                "Opening listing page (attempt %s/%s): %s",
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
                    "Listing page HTTP status: %s",
                    status
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        logging.warning(
                            "HTTP 429 received. Waiting %s seconds.",
                            RATE_LIMIT_DELAY
                        )

                        await page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    logging.error(
                        "HTTP 429 after all retries: %s",
                        url
                    )

                    return None

            return response

        except Exception as exc:

            logging.warning(
                "Failed to open listing page %s: %s",
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
                            "Product page returned HTTP 429. Waiting %s seconds.",
                            RATE_LIMIT_DELAY
                        )

                        await product_page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    logging.error(
                        "Product page returned HTTP 429 after retries: %s",
                        product_link
                    )

                    return None

            return response

        except Exception as exc:

            logging.warning(
                "Failed to open product page %s: %s",
                product_link,
                exc
            )

            if attempt < MAX_429_RETRIES:

                await product_page.wait_for_timeout(
                    DETAIL_PAGE_DELAY * 1000
                )

    return None


# ============================================================
# CRAWL ZEBRONICS
# ============================================================

async def scrape_zebronics_async():

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

        page.set_default_navigation_timeout(PAGE_TIMEOUT)

        try:

            for category, urls in BASE_URLS.items():

                category_results = []

                for base_url in urls:

                    page_number = 1

                    while page_number <= MAX_PAGES:

                        url = build_page_url(
                            base_url,
                            page_number
                        )

                        logging.info("=" * 60)

                        logging.info("Fetching: %s", url)

                        if page_number > 1:

                            await page.wait_for_timeout(
                                LISTING_PAGE_DELAY * 1000
                            )

                        response = await open_listing_page(
                            page,
                            url
                        )

                        if response is None:
                            break

                        try:

                            await page.wait_for_selector(
                                'a[href*="/products/"]',
                                timeout=PRODUCT_SELECTOR_TIMEOUT
                            )

                        except Exception:

                            logging.warning(
                                "No product links found on page %s",
                                page_number
                            )

                            break

                        previous_height = 0

                        for _ in range(5):

                            current_height = await page.evaluate(
                                "document.body.scrollHeight"
                            )

                            if current_height == previous_height:
                                break

                            previous_height = current_height

                            await page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight)"
                            )

                            await page.wait_for_timeout(1000)

                        raw_products = await extract_product_cards(page)

                        if not raw_products:
                            break

                        logging.info(
                            "Found %s product cards on page %s",
                            len(raw_products),
                            page_number
                        )

                        new_products_count = 0

                        for index, raw_product in enumerate(raw_products):

                            try:

                                product_url = raw_product.get(
                                    "url",
                                    ""
                                ).split("?")[0]

                                if not product_url:
                                    continue

                                if product_url in seen_links:
                                    continue

                                name = normalize_text(
                                    raw_product.get("title")
                                )

                                if not name:
                                    continue

                                if name.lower() in seen_names:
                                    continue

                                seen_links.add(product_url)

                                product_data = prepare_product(
                                    raw_product
                                )

                                if not product_data:

                                    logging.info(
                                        "Skipping invalid or sold-out product: %s",
                                        name
                                    )

                                    continue

                                if not product_data.get("image_link"):

                                    logging.info(
                                        "Skipping %s because image was not found.",
                                        name
                                    )

                                    continue

                                description = None
                                ratings = None
                                number_of_reviews = 0

                                product_page = await context.new_page()

                                product_page.set_default_timeout(
                                    PAGE_TIMEOUT
                                )

                                product_page.set_default_navigation_timeout(
                                    PAGE_TIMEOUT
                                )

                                try:

                                    logging.info(
                                        "Waiting %s seconds before opening product detail: %s",
                                        DETAIL_PAGE_DELAY,
                                        name
                                    )

                                    await page.wait_for_timeout(
                                        DETAIL_PAGE_DELAY * 1000
                                    )

                                    detail_response = await open_product_page(
                                        product_page,
                                        product_url
                                    )

                                    if detail_response:

                                        await product_page.wait_for_timeout(
                                            2000
                                        )

                                        product_html = await product_page.content()

                                        description = extract_product_description(
                                            product_html
                                        )

                                        ratings, number_of_reviews = (
                                            extract_product_rating(
                                                product_html
                                            )
                                        )

                                        if description:
                                            logging.info(
                                                "Description extracted for: %s",
                                                name
                                            )
                                        else:
                                            logging.warning(
                                                "Description not found for: %s",
                                                name
                                            )

                                        if ratings is not None:

                                            logging.info(
                                                "Rating extracted for %s: %s (%s reviews)",
                                                name,
                                                ratings,
                                                number_of_reviews
                                            )
                                        else:

                                            logging.info(
                                                "Rating not found for: %s",
                                                name
                                            )

                                except Exception as exc:

                                    logging.warning(
                                        "Product detail error for %s: %s",
                                        name,
                                        exc
                                    )

                                finally:

                                    await product_page.close()

                                product_data["description"] = description

                                product_data["ratings"] = ratings

                                product_data["number_of_reviews"] = (
                                    number_of_reviews
                                )

                                if (
                                    not product_data.get("name")
                                    or not product_data.get("price")
                                    or not product_data.get("image_link")
                                    or not product_data.get("product_link")
                                ):

                                    logging.info(
                                        "Skipping incomplete product: %s",
                                        name
                                    )

                                    continue

                                results.append(product_data)

                                category_results.append(product_data)

                                seen_names.add(name.lower())

                                new_products_count += 1

                                logging.info(
                                    "Scraped: %s | Price: %s | Discount: %s%% | Rating: %s",
                                    product_data["name"],
                                    product_data["price"],
                                    product_data["discount"],
                                    product_data["ratings"]
                                )

                            except Exception as exc:

                                logging.exception(
                                    "Error parsing product card #%s on page %s: %s",
                                    index + 1,
                                    page_number,
                                    exc
                                )

                        logging.info(
                            "Page %s completed. New valid products: %s",
                            page_number,
                            new_products_count
                        )

                        if new_products_count == 0:
                            logging.info(
                                "No new valid products found. Stopping pagination."
                            )
                            break

                        page_number += 1

                logging.info(
                    "Total products scraped for %s: %s",
                    category,
                    len(category_results)
                )

        finally:

            await page.close()

            await context.close()

            await browser.close()

    logging.info(
        "Total Zebronics products scraped: %s",
        len(results)
    )

    return results


# ============================================================
# WINDOWS PROCESS RUNNER
# ============================================================

def run_zebronics_scraper():

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

    logging.info("Zebronics browser process started.")

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_zebronics_async()
        )

        logging.info(
            "Zebronics browser process finished. Products returned: %s",
            len(results)
        )

        return results

    except Exception:

        logging.exception(
            "Zebronics browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class ZebronicsSpider(scrapy.Spider):

    name = "zebronics"

    allowed_domains = [
        "shop.zebronics.com",
    ]

    async def start(self):

        self.logger.info("=" * 60)

        self.logger.info(
            "Starting Zebronics Playwright scraper..."
        )

        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:

            products = await loop.run_in_executor(
                executor,
                run_zebronics_scraper
            )

        with open(
            "scrape_zebronics.json",
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
            "Zebronics scraper returned %s products.",
            len(products)
        )

        for product in products:

            if not product.get("image_link"):
                continue

            if not product.get("product_link"):
                continue

            yield product

        self.logger.info("=" * 60)

        self.logger.info("Zebronics spider completed.")

        self.logger.info("=" * 60)
