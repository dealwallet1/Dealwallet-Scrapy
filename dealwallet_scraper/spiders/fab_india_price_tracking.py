
import asyncio
import json
import logging
import os
import re
import sys

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import psycopg2
import scrapy
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

STORE_NAME = "Fabindia"

# Test only 10 existing products.
TEST_LIMIT = None

CONCURRENT_REQUESTS = 3
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "fabindia_existing_products.json"
OUTPUT_FILE = "fabindia_price_tracking_results.json"


# ============================================================
# PRICE SELECTORS
# Based on the uploaded Fabindia scraper, with PDP fallbacks.
# ============================================================
PRICE_SELECTORS = (
    "app-fab-pdp-summary .price_block_pdp_parent .amount",
    "app-fab-pdp-summary .price_block .amount",
    "span.offer-amount",
    ".offer-amount",
    "[itemprop='price']",
    "meta[property='product:price:amount']",
    ".selling-price",
    ".special-price",
    ".price",
)

ORIGINAL_PRICE_SELECTORS = (
    "app-fab-pdp-summary .price_block_pdp_parent .deleted_price",
    "app-fab-pdp-summary .deleted_price",
    "del span.amount",
    "del .amount",
    "del span",
    "del",
    ".original-price",
    ".mrp-price",
    ".regular-price",
    "[data-original-price]",
)

DISCOUNT_SELECTORS = (
    "app-fab-pdp-summary .price_block_pdp_parent .off_class",
    "app-fab-pdp-summary .off_class",
    "span.discount-amount span.amount",
    "span.discount-amount",
    ".discount-percent",
    ".discount",
    ".offer-discount",
)
RATING_SELECTORS = (
    "[itemprop='ratingValue']",
    ".rating-value",
    ".average-rating",
    ".product-rating",
    ".rating",
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def extract_price(value):
    """
    Extract a numeric price from text such as:
        ₹1,299
        Rs. 1,299.50
        1299
    """

    if value is None:
        return None

    match = re.search(
        r"\d[\d,]*(?:\.\d+)?",
        str(value).replace("\u00a0", " "),
    )

    if not match:
        return None

    try:
        amount = Decimal(match.group(0).replace(",", ""))

        if not amount.is_finite() or amount <= 0:
            return None

        if amount == amount.to_integral_value():
            return int(amount)

        return float(amount)

    except (InvalidOperation, ValueError):
        return None


def extract_discount(value):
    """Extract a percentage from text such as '20% off'."""

    if value is None:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        str(value),
    )

    if not match:
        return None

    try:
        percentage = float(match.group(1))

        if not 0 <= percentage <= 100:
            return None

        return round(percentage)

    except ValueError:
        return None


def extract_rating(value):
    if value is None:
        return None

    match = re.search(
        r"(?<!\d)([0-5](?:\.\d+)?)",
        str(value),
    )

    if not match:
        return None

    try:
        rating = float(match.group(1))

        if 0 <= rating <= 5:
            return rating

        return None

    except ValueError:
        return None


def calculate_discount(price, original_price):
    if (
        price is None
        or original_price is None
        or original_price <= 0
        or original_price <= price
    ):
        return None

    return round(
        ((original_price - price) / original_price) * 100
    )


def values_differ(old_value, new_value):
    if old_value is None or new_value is None:
        return None

    try:
        return (
            Decimal(str(old_value))
            != Decimal(str(new_value))
        )

    except Exception:
        return (
            str(old_value).strip()
            != str(new_value).strip()
        )


def normalize_url(url):
    if not url:
        return None

    url = clean_text(url)
    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
    ):
        return None

    hostname = parsed.hostname.lower()

    if (
        hostname != "fabindia.com"
        and not hostname.endswith(".fabindia.com")
    ):
        return None

    return url


# ============================================================
# FETCH EXISTING FABINDIA PRODUCTS FROM POSTGRESQL
# ============================================================

def get_fabindia_products():

    missing = [
        key
        for key, value in {
            "DB_HOST": DB_HOST,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing database environment variables: "
            + ", ".join(missing)
        )

    connection = None

    try:
        logging.info(
            "Connecting to PostgreSQL to fetch existing Fabindia products..."
        )

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.price,
                    p.original_price,
                    p.currency,
                    p.discount,
                    p.ratings,
                    p.description,
                    p.image_link,
                    p.product_link,
                    o.name AS organization_name,
                    s.name AS store_name,
                    c.name AS category_name,
                    p.affiliate_url
                FROM public.products AS p
                JOIN public.stores AS s
                    ON p.store_id = s.id
                LEFT JOIN public.organization AS o
                    ON p.organization_id = o.id
                LEFT JOIN public.categories AS c
                    ON p.categories_id = c.id
                WHERE LOWER(TRIM(s.name)) LIKE %s
                  AND p.product_link IS NOT NULL
                  AND TRIM(p.product_link) <> ''
                ORDER BY p.id
                LIMIT %s
                """,
                ("%fabindia%", TEST_LIMIT),
            )

            rows = cursor.fetchall()

        products = []
        invalid_urls = 0

        for row in rows:
            (
                product_id,
                name,
                price,
                original_price,
                currency,
                discount,
                ratings,
                description,
                image_link,
                product_link,
                organization_name,
                store_name,
                category_name,
                affiliate_url,
            ) = row

            url = normalize_url(product_link)

            if not url:
                invalid_urls += 1
                logging.warning(
                    "Skipping unsupported URL for product %s: %s",
                    product_id,
                    product_link,
                )
                continue

            products.append({
                "product_id": str(product_id),
                "name": name,
                "price": (
                    float(price)
                    if price is not None
                    else None
                ),
                "original_price": (
                    float(original_price)
                    if original_price is not None
                    else None
                ),
                "currency": currency or "₹",
                "discount": (
                    int(discount)
                    if discount is not None
                    else None
                ),
                "ratings": (
                    float(ratings)
                    if ratings is not None
                    else None
                ),
                "description": description,
                "image_link": image_link,
                "product_link": product_link,
                "scrape_url": url,
                "organization_id": organization_name,
                "store_id": store_name,
                "store_name": store_name,
                "categories_id": category_name,
                "affiliate_url": affiliate_url,
            })

        with open(INPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(
                products,
                file,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        logging.info(
            "Database rows fetched: %s | Eligible URLs: %s | "
            "Invalid URLs skipped: %s",
            len(rows),
            len(products),
            invalid_urls,
        )

        logging.info(
            "Saved input file: %s",
            os.path.abspath(INPUT_FILE),
        )

        return products

    finally:
        if connection:
            connection.close()


# ============================================================
# HTML / JSON-LD PARSING
# ============================================================

def first_selector_value(soup, selectors):

    for selector in selectors:
        element = soup.select_one(selector)

        if not element:
            continue

        value = (
            element.get("content")
            or element.get("data-original-price")
            or element.get("value")
            or element.get_text(" ", strip=True)
        )

        value = clean_text(value)

        if value:
            return value

    return None


def iter_product_jsonld(soup):

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            payload = json.loads(raw)

        except (json.JSONDecodeError, TypeError):
            continue

        stack = payload if isinstance(payload, list) else [payload]

        while stack:
            entry = stack.pop(0)

            if not isinstance(entry, dict):
                continue

            graph = entry.get("@graph")

            if isinstance(graph, list):
                stack.extend(graph)

            entry_type = entry.get("@type")

            if (
                entry_type == "Product"
                or (
                    isinstance(entry_type, list)
                    and "Product" in entry_type
                )
            ):
                yield entry


def extract_product_data(html, product):

    soup = BeautifulSoup(html, "html.parser")

    # 1. Current price
    price = extract_price(
        first_selector_value(soup, PRICE_SELECTORS)
    )

    # 2. Original price / MRP
    original_price = extract_price(
        first_selector_value(soup, ORIGINAL_PRICE_SELECTORS)
    )

    # 3. Discount percentage
    discount = extract_discount(
        first_selector_value(soup, DISCOUNT_SELECTORS)
    )

    # 4. Rating
    rating = extract_rating(
        first_selector_value(soup, RATING_SELECTORS)
    )

    # 5. JSON-LD fallback
    product_json = next(
        iter(iter_product_jsonld(soup)),
        {},
    )

    offers = (
        product_json.get("offers", {})
        if isinstance(product_json, dict)
        else {}
    )

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        offers = {}

    if price is None:
        price = extract_price(
            offers.get("price")
            or offers.get("lowPrice")
        )

    if rating is None:
        aggregate = product_json.get("aggregateRating", {})

        if isinstance(aggregate, dict):
            rating = extract_rating(
                aggregate.get("ratingValue")
            )

    # Keep the database values as fallback if the live page
    # does not expose a corresponding field.
    if original_price is None:
        original_price = product.get("original_price")

    if discount is None:
        discount = calculate_discount(
            price,
            original_price,
        )

    if discount is None:
        discount = product.get("discount")

    return price, original_price, discount, rating


# ============================================================
# RESULT STRUCTURE
# ============================================================

def make_result(
    product,
    price,
    original_price,
    discount,
    rating,
    status,
    http_status,
):

    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "database_price": product.get("price"),
        "price": price,
        "original_price": (
            original_price
            if original_price is not None
            else product.get("original_price")
        ),
        "currency": product.get("currency") or "₹",
        "discount": (
            discount
            if discount is not None
            else product.get("discount")
        ),
        "database_ratings": product.get("ratings"),
        "ratings": (
            rating
            if rating is not None
            else product.get("ratings")
        ),
        "description": product.get("description"),
        "image_link": product.get("image_link"),
        "product_link": product.get("product_link"),
        "scrape_url": product.get("scrape_url"),
        "organization_id": product.get("organization_id"),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name"),
        "categories_id": product.get("categories_id"),
        "affiliate_url": product.get("affiliate_url"),
        "created_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "status": status,
        "http_status": http_status,
    }


# ============================================================
# SCRAPE ONE EXISTING PRODUCT
# ============================================================

async def scrape_one(context, product, index, total):

    url = product["scrape_url"]
    last_status = None

    logging.info(
        "[PRODUCT %s/%s] ID=%s | %s | %s",
        index,
        total,
        product.get("product_id"),
        product.get("name"),
        url,
    )

    for attempt in range(1, MAX_RETRIES + 1):

        page = None

        try:
            page = await context.new_page()

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            last_status = (
                response.status
                if response
                else None
            )

            logging.info(
                "[NAVIGATION] ID=%s | Attempt=%s/%s | HTTP=%s",
                product.get("product_id"),
                attempt,
                MAX_RETRIES,
                last_status,
            )

            if last_status is not None and last_status >= 400:

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    None,
                    None,
                    None,
                    None,
                    "http_error",
                    last_status,
                )

            # Wait briefly for client-rendered product details.
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=8000,
                )
            except PlaywrightTimeoutError:
                pass

            await page.wait_for_timeout(1000)

            html = await page.content()

            (
                price,
                original_price,
                discount,
                rating,
            ) = extract_product_data(html, product)

            if price is None:
                logging.warning(
                    "[MISSING PRICE] ID=%s | %s",
                    product.get("product_id"),
                    url,
                )

                return make_result(
                    product,
                    None,
                    original_price,
                    discount,
                    rating,
                    "missing_price",
                    last_status,
                )

            result = make_result(
                product,
                price,
                original_price,
                discount,
                rating,
                "success",
                last_status,
            )

            result["price_changed"] = values_differ(
                product.get("price"),
                price,
            )

            result["original_price_changed"] = values_differ(
                product.get("original_price"),
                original_price,
            )

            result["discount_changed"] = values_differ(
                product.get("discount"),
                discount,
            )

            result["ratings_changed"] = values_differ(
                product.get("ratings"),
                rating,
            )

            result["price_difference"] = (
                float(price) - float(product["price"])
                if product.get("price") is not None
                else None
            )

            logging.info(
                "[SCRAPED] ID=%s | Old=%s | Current=%s | "
                "Original=%s | Discount=%s | Changed=%s",
                product.get("product_id"),
                product.get("price"),
                price,
                original_price,
                discount,
                result["price_changed"],
            )

            return result

        except Exception as exc:

            logging.warning(
                "[RETRY ERROR] ID=%s | Attempt=%s/%s | %s",
                product.get("product_id"),
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(attempt * 2)

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    return make_result(
        product,
        None,
        None,
        None,
        None,
        "failed",
        last_status,
    )


# ============================================================
# PLAYWRIGHT RUNNER
# ============================================================

async def run_playwright(products):

    results = []
    queue = asyncio.Queue()

    for index, product in enumerate(products, 1):
        await queue.put((index, product))

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={
                "width": 1366,
                "height": 900,
            },
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        async def worker(worker_id):

            while True:
                job = await queue.get()

                try:
                    if job is None:
                        return

                    index, product = job

                    try:
                        result = await scrape_one(
                            context,
                            product,
                            index,
                            len(products),
                        )

                    except Exception as exc:
                        logging.exception(
                            "Worker %s failed for product %s",
                            worker_id,
                            product.get("product_id"),
                        )

                        result = make_result(
                            product,
                            None,
                            None,
                            None,
                            None,
                            "failed",
                            None,
                        )
                        result["error"] = str(exc)

                    results.append(result)

                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker(i + 1))
            for i in range(
                min(CONCURRENT_REQUESTS, len(products))
            )
        ]

        await queue.join()

        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers)

        await context.close()
        await browser.close()

    order = {
        product.get("product_id"): index
        for index, product in enumerate(products)
    }

    results.sort(
        key=lambda item: order.get(
            item.get("product_id"),
            10**9,
        )
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            results,
            file,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    successful = sum(
        item.get("status") == "success"
        for item in results
    )

    logging.info(
        "Fabindia tracking complete | Total=%s | "
        "Successful=%s | Failed/Missing=%s | Output=%s",
        len(results),
        successful,
        len(results) - successful,
        os.path.abspath(OUTPUT_FILE),
    )

    return results


def run_playwright_process(products):

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return {
        "success": True,
        "results": asyncio.run(
            run_playwright(products)
        ),
        "output_file": OUTPUT_FILE,
    }


# ============================================================
# SCRAPY SPIDER / PIPELINE HANDOFF
# ============================================================

class FabindiaPriceTrackingSpider(scrapy.Spider):

    name = "fabindia_price_tracking"

    allowed_domains = [
        "fabindia.com",
        "www.fabindia.com",
    ]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "EXTENSIONS": {
            "scrapy.extensions.logstats.LogStats": None,
        },
        "CONCURRENT_REQUESTS": 1,
    }

    async def start(self):

        self.logger.info("=" * 70)
        self.logger.info("FABINDIA EXISTING PRODUCT PRICE TRACKER")
        self.logger.info(
            "TEST MODE: Maximum %s existing products",
            TEST_LIMIT,
        )
        self.logger.info(
            "Using stored URLs only; no product discovery"
        )
        self.logger.info("=" * 70)

        try:
            products = get_fabindia_products()

        except Exception as exc:
            self.logger.error(
                "Could not fetch existing Fabindia products: %s",
                exc,
                exc_info=True,
            )
            return

        if not products:
            self.logger.warning(
                "No eligible existing Fabindia products found."
            )
            return

        self.logger.info(
            "Fetched %s existing products. Starting Playwright...",
            len(products),
        )

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(
                    executor,
                    run_playwright_process,
                    products,
                )

        except Exception as exc:
            self.logger.error(
                "Playwright process failed: %s",
                exc,
                exc_info=True,
            )
            return

        if not result or not result.get("success"):
            self.logger.error(
                "Fabindia price tracking failed."
            )
            return

        results = result.get("results") or []

        pipeline_count = 0
        skipped_count = 0

        for item in results:

            if (
                not isinstance(item, dict)
                or item.get("status") != "success"
                or item.get("price") is None
            ):
                skipped_count += 1
                continue

            # Existing product ID is retained.
            # The existing pipeline must record price history
            # for this ID instead of inserting a duplicate product.
            pipeline_count += 1
            yield item

        self.logger.info(
            "Finished | Scraped=%s | Sent to pipeline=%s | "
            "Skipped=%s | JSON=%s",
            len(results),
            pipeline_count,
            skipped_count,
            result.get("output_file"),
        )


# ============================================================
# STANDALONE ENTRY POINT
# ============================================================

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    products = get_fabindia_products()

    if not products:
        print("No eligible existing Fabindia products found.")
        raise SystemExit(0)

    result = run_playwright_process(products)

    print(
        "Standalone mode writes JSON only. "
        "Scrapy pipelines run only with `scrapy crawl`."
    )
    print("Output:", result.get("output_file"))
