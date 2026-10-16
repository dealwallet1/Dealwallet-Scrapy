

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

STORE_NAME = "ZOP"

# Test with 10 existing products.
TEST_LIMIT = None

CONCURRENT_REQUESTS = 3
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "zop_existing_products.json"
OUTPUT_FILE = "zop_price_tracking_results.json"


# ============================================================
# UPDATED ZOP SELECTORS
# ============================================================

# Current selling price: ₹75
PRICE_SELECTORS = (
    ".f-price-item--sale.priceRHS",
    "span.f-price-item--sale",
    ".f-price__sale .priceRHS",
    "span.productprice",
    ".price__current",
    ".price-item--sale",
    "[itemprop='price']",
    "meta[property='product:price:amount']",
)

# Original price / MRP: ₹175
ORIGINAL_PRICE_SELECTORS = (
    ".f-price-item--regular.comparePriceRHS s",
    ".comparePriceRHS s",
    ".f-price__sale .comparePriceRHS",
    ".zop-mrp-info__price",
    "span.productcompare-at-price",
    ".price__compare",
    "s.price-item",
    "del .price-item",
    "del",
)

# Discount badge: 57% Off
DISCOUNT_SELECTORS = (
    ".zop-discount-badge",
    ".f-price__badges .badge",
    ".tagCont .badge",
)

RATING_SELECTORS = (
    "div.product-rating span.big-point",
    "[itemprop='ratingValue']",
    ".rating-value",
    ".product-rating",
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def parse_money(value):
    """Convert values such as ₹75, ₹1,299.00 into numeric values."""

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    match = re.search(r"\d[\d,]*(?:\.\d+)?", text)

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


def parse_discount(value):
    """Extract discount percentage from text such as '57% Off'."""

    if value is None:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value))

    if not match:
        return None

    try:
        discount = float(match.group(1))

        if not 0 <= discount <= 100:
            return None

        return round(discount)

    except ValueError:
        return None


def parse_rating(value):
    if value is None:
        return None

    match = re.search(r"(?<!\d)([0-5](?:\.\d+)?)", str(value))

    if not match:
        return None

    try:
        rating = float(match.group(1))
        return rating if 0 <= rating <= 5 else None

    except ValueError:
        return None


def calculate_discount(original_price, price):
    if (
        original_price is None
        or price is None
        or original_price <= 0
        or price >= original_price
    ):
        return None

    return round(
        ((original_price - price) / original_price) * 100
    )


def values_differ(old, new):
    if old is None or new is None:
        return None

    try:
        return Decimal(str(old)) != Decimal(str(new))

    except Exception:
        return str(old).strip() != str(new).strip()


def normalize_url(url):
    url = clean_text(url)

    if not url:
        return None

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.hostname:
        return None

    hostname = parsed.hostname.lower()

    if hostname != "zop.in" and not hostname.endswith(".zop.in"):
        return None

    return url


# ============================================================
# DATABASE: FETCH EXISTING ZOP PRODUCTS ONLY
# ============================================================

def get_zop_products():

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
            "Connecting to PostgreSQL to fetch existing ZOP products..."
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
                WHERE LOWER(TRIM(s.name)) = LOWER(TRIM(%s))
                  AND p.product_link IS NOT NULL
                  AND TRIM(p.product_link) <> ''
                ORDER BY p.id
                LIMIT %s
                """,
                (STORE_NAME, TEST_LIMIT),
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
                continue

            products.append({
                "product_id": str(product_id),
                "name": name,
                "price": (
                    float(price)
                    if price is not None else None
                ),
                "original_price": (
                    float(original_price)
                    if original_price is not None else None
                ),
                "currency": currency or "INR",
                "discount": (
                    int(discount)
                    if discount is not None else None
                ),
                "ratings": (
                    float(ratings)
                    if ratings is not None else None
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
            "DB rows fetched: %s | Eligible URLs: %s | "
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
            logging.info("PostgreSQL connection closed.")


# ============================================================
# HTML PARSING HELPERS
# ============================================================

def first_selector_value(soup, selectors):

    for selector in selectors:
        element = soup.select_one(selector)

        if not element:
            continue

        value = (
            element.get("content")
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


def extract_product_data(page_html, product):

    soup = BeautifulSoup(page_html, "html.parser")

    # --------------------------------------------------------
    # 1. Current price from the new ZOP selector
    # --------------------------------------------------------
    price_text = first_selector_value(
        soup,
        PRICE_SELECTORS,
    )
    price = parse_money(price_text)

    # --------------------------------------------------------
    # 2. MRP / original price from the new ZOP selector
    # --------------------------------------------------------
    original_price_text = first_selector_value(
        soup,
        ORIGINAL_PRICE_SELECTORS,
    )
    original_price = parse_money(original_price_text)

    # --------------------------------------------------------
    # 3. Discount from the ZOP discount badge
    # --------------------------------------------------------
    discount_text = first_selector_value(
        soup,
        DISCOUNT_SELECTORS,
    )
    discount = parse_discount(discount_text)

    # --------------------------------------------------------
    # 4. Rating
    # --------------------------------------------------------
    rating = parse_rating(
        first_selector_value(soup, RATING_SELECTORS)
    )

    # --------------------------------------------------------
    # 5. JSON-LD fallback
    # --------------------------------------------------------
    jsonld = next(iter(iter_product_jsonld(soup)), {})

    offers = (
        jsonld.get("offers", {})
        if isinstance(jsonld, dict)
        else {}
    )

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        offers = {}

    if price is None:
        price = parse_money(
            offers.get("price")
            or offers.get("lowPrice")
        )

    if original_price is None:
        original_price = parse_money(
            offers.get("highPrice")
        )

    if rating is None:
        aggregate = jsonld.get("aggregateRating", {})

        if isinstance(aggregate, dict):
            rating = parse_rating(
                aggregate.get("ratingValue")
            )

    # --------------------------------------------------------
    # 6. Shopify JSON fallback for current price
    # --------------------------------------------------------
    if price is None:

        for script in soup.select(
            'script[type="application/json"]'
        ):
            raw = script.string or script.get_text()

            if not raw:
                continue

            try:
                payload = json.loads(raw)

            except (json.JSONDecodeError, TypeError):
                continue

            candidates = (
                payload
                if isinstance(payload, list)
                else [payload]
            )

            for candidate in candidates:

                if not isinstance(candidate, dict):
                    continue

                # Shopify variant prices are commonly stored in paise.
                # Do not apply this conversion to normal HTML text prices.
                variants = candidate.get("variants")

                if not isinstance(variants, list):
                    continue

                for variant in variants:

                    if not isinstance(variant, dict):
                        continue

                    raw_price = variant.get("price")

                    if raw_price is None:
                        continue

                    try:
                        # Shopify JSON prices are commonly integer paise.
                        if isinstance(raw_price, int):
                            candidate_price = raw_price / 100
                        else:
                            candidate_price = float(raw_price)

                        if candidate_price > 0:
                            price = (
                                int(candidate_price)
                                if candidate_price.is_integer()
                                else candidate_price
                            )
                            break

                    except (TypeError, ValueError):
                        continue

                if price is not None:
                    break

            if price is not None:
                break

    # --------------------------------------------------------
    # 7. Fallbacks if the live page omits MRP or discount
    # --------------------------------------------------------
    if original_price is None:
        original_price = product.get("original_price")

    if discount is None:
        discount = calculate_discount(
            original_price,
            price,
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
        "currency": product.get("currency") or "INR",
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
        "store_name": product.get("store_name") or STORE_NAME,
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

    logging.info(
        "[PRODUCT %s/%s] id=%s | %s | %s",
        index,
        total,
        product.get("product_id"),
        product.get("name"),
        url,
    )

    last_status = None

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
                "[NAVIGATION] id=%s attempt=%s/%s HTTP=%s",
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

            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=8000,
                )

            except PlaywrightTimeoutError:
                pass

            # Give the product price elements time to render.
            try:
                await page.locator(
                    ".f-price-item--sale.priceRHS"
                ).first.wait_for(
                    state="attached",
                    timeout=5000,
                )

            except PlaywrightTimeoutError:
                logging.debug(
                    "Primary ZOP price selector did not appear: %s",
                    url,
                )

            html = await page.content()

            (
                price,
                original_price,
                discount,
                rating,
            ) = extract_product_data(html, product)

            if price is None:

                logging.warning(
                    "[NO CURRENT PRICE] id=%s | %s",
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
                "[SCRAPED] id=%s | old_price=%s | "
                "current_price=%s | MRP=%s | discount=%s | "
                "price_changed=%s",
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
                "[RETRY ERROR] id=%s attempt=%s/%s: %s",
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
            viewport={"width": 1366, "height": 900},
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
        key=lambda result: order.get(
            result.get("product_id"),
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
        result.get("status") == "success"
        for result in results
    )

    logging.info(
        "ZOP tracking complete | total=%s | successful=%s | "
        "failed_or_missing=%s | output=%s",
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
        "results": asyncio.run(run_playwright(products)),
        "output_file": OUTPUT_FILE,
    }


# ============================================================
# SCRAPY SPIDER AND PIPELINE HANDOFF
# ============================================================

class ZOPPriceTrackingSpider(scrapy.Spider):

    name = "zop_price_tracking"

    allowed_domains = [
        "zop.in",
        "www.zop.in",
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
        self.logger.info("ZOP EXISTING PRODUCT PRICE TRACKER")
        self.logger.info("TEST MODE: %s products maximum", TEST_LIMIT)
        self.logger.info("Existing products only; no new discovery")
        self.logger.info("=" * 70)

        try:
            products = get_zop_products()

        except Exception as exc:
            self.logger.error(
                "Could not fetch existing ZOP products: %s",
                exc,
                exc_info=True,
            )
            return

        if not products:
            self.logger.warning(
                "No eligible existing ZOP products found."
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
            self.logger.error("ZOP tracking process failed.")
            return

        results = result.get("results") or []

        pipeline_count = 0
        skipped_count = 0

        for product in results:

            if (
                not isinstance(product, dict)
                or product.get("status") != "success"
                or product.get("price") is None
            ):
                skipped_count += 1
                continue

            # Keep the existing product_id for pipeline handling.
            # Verify that your pipeline records price history for this ID
            # and does not insert a duplicate product.
            pipeline_count += 1
            yield product

        self.logger.info(
            "Finished | scraped=%s | sent_to_pipeline=%s | "
            "skipped=%s | JSON=%s",
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

    products = get_zop_products()

    if not products:
        print("No eligible existing ZOP products found.")
        raise SystemExit(0)

    result = run_playwright_process(products)

    print(
        "Standalone mode writes JSON only. "
        "Scrapy pipelines run only with `scrapy crawl`."
    )
    print("Output:", result.get("output_file"))
