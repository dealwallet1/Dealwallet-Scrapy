import asyncio
import json
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import psycopg2
import scrapy
from bs4 import BeautifulSoup
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

TEST_LIMIT = 10
CONCURRENT_REQUESTS = 1
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "vaaree_existing_products.json"
OUTPUT_FILE = "vaaree_price_tracking_results.json"
FAILED_OUTPUT_FILE = "vaaree_failed_products.json"

PRICE_SELECTORS = (
    ".pdp-price-value",
    ".pdp-mobile-price-value",
    '[itemprop="price"]',
    'meta[property="product:price:amount"]',
    "[data-testid*='price']",
    "[class*='sellingPrice']",
    "[class*='selling-price']",
    "[class*='sale-price']",
    "[class*='salePrice']",
    "[class*='current-price']",
    "[class*='currentPrice']",
    "[class*='price__current']",
    "[class*='ProductPrice']",
    "[class*='product-price']",
    "meta[property='og:price:amount']",
)

ORIGINAL_PRICE_SELECTORS = (
    ".pdp-price-compare",
    ".pdp-mobile-price-mrp-value",
    "del [class*='price']",
    "s [class*='price']",
    "del",
    "s",
    "[class*='mrp']",
    "[class*='MRP']",
    "[class*='original-price']",
    "[class*='originalPrice']",
    "[class*='list-price']",
    "[class*='listPrice']",
    "[class*='strike']",
    "[class*='was-price']",
)

DISCOUNT_SELECTORS = (
    ".pdp-price-discount",
    ".pdp-mobile-price-discount",
    "[class*='discount']",
    "[class*='Discount']",
    "[class*='off-percent']",
    "[class*='offer-percent']",
    "[class*='percentage']",
)

RATING_SELECTORS = (
    ".pdp-rating-copy",
    '[aria-label*="rating from"]',
    "[itemprop='ratingValue']",
    "[class*='rating-value']",
    "[class*='ratingValue']",
    "[class*='average-rating']",
    "[class*='product-rating']",
    "[class*='rating']",
)


def clean_text(value):
    if value is None:
        return None

    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def extract_price(value):
    if value is None:
        return None

    match = re.search(
        r"(?<!\d)(\d[\d,]*(?:\.\d{1,2})?)(?!\d)",
        str(value).replace("\u00a0", " "),
    )

    if not match:
        return None

    try:
        amount = Decimal(match.group(1).replace(",", ""))

        if not amount.is_finite() or amount <= 0:
            return None

        if amount == amount.to_integral_value():
            return int(amount)

        return float(amount)

    except (InvalidOperation, ValueError):
        return None


def extract_discount(value):
    if value is None:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value))

    if not match:
        return None

    number = float(match.group(1))

    if 0 <= number <= 100:
        return round(number)

    return None


def extract_rating(value):
    if value is None:
        return None

    match = re.search(
        r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
        str(value),
    )

    if not match:
        return None

    number = float(match.group(1))

    if 0 <= number <= 5:
        return number

    return None


def calculate_discount(price, original_price):
    if (
        price is None
        or original_price is None
        or original_price <= price
        or original_price <= 0
    ):
        return None

    return round((original_price - price) / original_price * 100)


def values_differ(old_value, new_value):
    if old_value is None or new_value is None:
        return None

    try:
        return Decimal(str(old_value)) != Decimal(str(new_value))
    except Exception:
        return str(old_value).strip() != str(new_value).strip()


def normalize_url(url):
    if not url:
        return None

    url = clean_text(url)
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None

    host = parsed.hostname.lower()

    if host != "vaaree.com" and not host.endswith(".vaaree.com"):
        return None

    return url


def get_vaaree_products():
    missing = [
        name
        for name, value in {
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
            "Connecting to PostgreSQL to fetch existing Vaaree products..."
        )

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )

        with connection.cursor() as cursor:
            query = """
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
            """

            if TEST_LIMIT is None:
                cursor.execute(query, ("%vaaree%",))
            else:
                cursor.execute(
                    query + " LIMIT %s",
                    ("%vaaree%", TEST_LIMIT),
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
                    "[INVALID URL] ID=%s | URL=%s",
                    product_id,
                    product_link,
                )
                continue

            products.append({
                "product_id": str(product_id),
                "name": name,
                "price": float(price) if price is not None else None,
                "original_price": (
                    float(original_price)
                    if original_price is not None
                    else None
                ),
                "currency": currency or "₹",
                "discount": (
                    int(discount) if discount is not None else None
                ),
                "ratings": (
                    float(ratings) if ratings is not None else None
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
            "Database rows=%s | Eligible URLs=%s | Invalid URLs=%s",
            len(rows),
            len(products),
            invalid_urls,
        )
        logging.info("Saved input file: %s", os.path.abspath(INPUT_FILE))

        return products

    finally:
        if connection:
            connection.close()


def first_selector_value(soup, selectors):
    for selector in selectors:
        try:
            element = soup.select_one(selector)
        except Exception:
            continue

        if element:
            value = (
                element.get("content")
                or element.get("data-price")
                or element.get("data-original-price")
                or element.get("value")
                or element.get_text(" ", strip=True)
            )

            value = clean_text(value)

            if value:
                return value

    return None


def iter_product_jsonld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
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

            if isinstance(entry, list):
                stack.extend(entry)

            elif isinstance(entry, dict):
                graph = entry.get("@graph")

                if isinstance(graph, list):
                    stack.extend(graph)

                entry_type = entry.get("@type")

                if entry_type == "Product" or (
                    isinstance(entry_type, list)
                    and "Product" in entry_type
                ):
                    yield entry


def extract_product_data(html, product):
    soup = BeautifulSoup(html or "", "html.parser")

    price = extract_price(
        first_selector_value(soup, PRICE_SELECTORS)
    )

    original_price = extract_price(
        first_selector_value(soup, ORIGINAL_PRICE_SELECTORS)
    )

    discount = extract_discount(
        first_selector_value(soup, DISCOUNT_SELECTORS)
    )

    rating = extract_rating(
        first_selector_value(soup, RATING_SELECTORS)
    )

    product_json = next(iter(iter_product_jsonld(soup)), {})

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
            offers.get("price") or offers.get("lowPrice")
        )

    if original_price is None:
        price_specification = offers.get("priceSpecification", {})

        if isinstance(price_specification, dict):
            original_value = (
                offers.get("highPrice")
                or price_specification.get("price")
            )
        else:
            original_value = offers.get("highPrice")

        original_price = extract_price(original_value)

    if discount is None:
        discount = extract_discount(product_json.get("discount"))

    if discount is None:
        discount = calculate_discount(price, original_price)

    if rating is None:
        aggregate = product_json.get("aggregateRating", {})

        if isinstance(aggregate, dict):
            rating = extract_rating(aggregate.get("ratingValue"))

    # Do not scrape or replace the product name.
    # Original price and discount can fall back to database values.
    if original_price is None:
        original_price = product.get("original_price")

    if discount is None:
        discount = product.get("discount")

    return price, original_price, discount, rating


def make_result(
    product,
    price,
    original_price,
    discount,
    rating,
    status,
    http_status,
    error=None,
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
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "http_status": http_status,
        "error": error,
    }


async def scrape_one(
    crawler,
    run_config,
    product,
    index,
    total,
    semaphore,
):
    url = product["scrape_url"]

    logging.info(
        "[PRODUCT %s/%s] ID=%s | Name=%s | URL=%s",
        index,
        total,
        product.get("product_id"),
        product.get("name"),
        url,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                logging.info(
                    "[CRAWL4AI REQUEST] ID=%s | Attempt=%s/%s",
                    product.get("product_id"),
                    attempt,
                    MAX_RETRIES,
                )

                result = await crawler.arun(
                    url=url,
                    config=run_config,
                )

            status_code = getattr(result, "status_code", None)

            html = (
                getattr(result, "html", None)
                or getattr(result, "cleaned_html", None)
                or ""
            )

            error_message = clean_text(
                getattr(result, "error_message", None)
            )

            logging.info(
                "[CRAWL4AI RESPONSE] ID=%s | HTTP=%s | Success=%s | URL=%s",
                product.get("product_id"),
                status_code,
                getattr(result, "success", None),
                url,
            )

            if status_code == 403:
                message = (
                    "HTTP 403: Vaaree denied the request; "
                    "no retry attempted."
                )

                logging.warning(
                    "[BLOCKED 403] ID=%s | Existing DB price retained=%s",
                    product.get("product_id"),
                    product.get("price"),
                )

                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    product.get("ratings"),
                    "blocked_403",
                    status_code,
                    message,
                )

            if status_code is not None and 400 <= status_code < 500:
                message = f"HTTP {status_code}; permanent client error."

                logging.warning(
                    "[CLIENT ERROR] ID=%s | %s",
                    product.get("product_id"),
                    message,
                )

                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    product.get("ratings"),
                    "http_error",
                    status_code,
                    message,
                )

            if status_code is not None and status_code >= 500:
                if attempt < MAX_RETRIES:
                    logging.warning(
                        "[SERVER ERROR RETRY] ID=%s | HTTP=%s | Attempt=%s/%s",
                        product.get("product_id"),
                        status_code,
                        attempt,
                        MAX_RETRIES,
                    )

                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    product.get("ratings"),
                    "http_error",
                    status_code,
                    f"HTTP {status_code} after {MAX_RETRIES} attempts.",
                )

            if not html.strip():
                if attempt < MAX_RETRIES:
                    logging.warning(
                        "[EMPTY HTML] ID=%s | Attempt=%s/%s | Error=%s",
                        product.get("product_id"),
                        attempt,
                        MAX_RETRIES,
                        error_message,
                    )

                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    product.get("ratings"),
                    "failed",
                    status_code,
                    error_message or "Crawl4AI returned empty HTML.",
                )

            price, original_price, discount, rating = extract_product_data(
                html,
                product,
            )

            if price is None:
                logging.warning(
                    "[MISSING PRICE] ID=%s | HTTP=%s | HTML length=%s | Error=%s",
                    product.get("product_id"),
                    status_code,
                    len(html),
                    error_message,
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    None,
                    original_price,
                    discount,
                    rating,
                    "missing_price",
                    status_code,
                    "Page returned HTML, but no live price was found.",
                )

            output = make_result(
                product,
                price,
                original_price,
                discount,
                rating,
                "success",
                status_code,
            )

            output["price_changed"] = values_differ(
                product.get("price"),
                price,
            )

            output["original_price_changed"] = values_differ(
                product.get("original_price"),
                original_price,
            )

            output["discount_changed"] = values_differ(
                product.get("discount"),
                discount,
            )

            output["ratings_changed"] = values_differ(
                product.get("ratings"),
                rating,
            )

            output["price_difference"] = (
                float(price) - float(product["price"])
                if product.get("price") is not None
                else None
            )

            logging.info(
                "[SCRAPED] ID=%s | Old=%s | Current=%s | Difference=%s | Changed=%s",
                product.get("product_id"),
                product.get("price"),
                price,
                output["price_difference"],
                output["price_changed"],
            )

            return output

        except Exception as exc:
            logging.warning(
                "[CRAWL4AI ERROR] ID=%s | Attempt=%s/%s | %s",
                product.get("product_id"),
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(attempt * 2)

    return make_result(
        product,
        None,
        product.get("original_price"),
        product.get("discount"),
        product.get("ratings"),
        "failed",
        None,
        f"Failed after {MAX_RETRIES} attempts.",
    )


async def run_crawl4ai(products):
    if not products:
        return []

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded",
        delay_before_return_html=1.0,
        verbose=False,
    )

    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    logging.info(
        "[CRAWL4AI] Starting | Products=%s | Workers=%s",
        len(products),
        CONCURRENT_REQUESTS,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        tasks = [
            scrape_one(
                crawler,
                run_config,
                product,
                index,
                len(products),
                semaphore,
            )
            for index, product in enumerate(products, 1)
        ]

        results = await asyncio.gather(*tasks)

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

    failed_results = [
        {
            "product_id": item.get("product_id"),
            "product_link": item.get("product_link"),
            "status": item.get("status"),
            "error": item.get("error"),
        }
        for item in results
        if item.get("status") != "success"
        or item.get("price") is None
    ]

    with open(FAILED_OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            failed_results,
            file,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    successful = sum(
        item.get("status") == "success"
        for item in results
    )

    blocked = sum(
        item.get("status") == "blocked_403"
        for item in results
    )

    logging.info(
        "[SUMMARY] Total=%s | Successful=%s | Blocked403=%s | "
        "Failed/Missing=%s | Output=%s",
        len(results),
        successful,
        blocked,
        len(results) - successful,
        os.path.abspath(OUTPUT_FILE),
    )

    logging.info(
        "[FAILED JSON] Failed=%s | File=%s",
        len(failed_results),
        os.path.abspath(FAILED_OUTPUT_FILE),
    )

    return results


def run_crawl4ai_process(products):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        force=True,
    )

    logging.info(
        "[CRAWL4AI CHILD PROCESS] Initializing | Products=%s",
        len(products),
    )

    results = asyncio.run(run_crawl4ai(products))

    return {
        "success": True,
        "results": results,
        "output_file": OUTPUT_FILE,
    }


class VaareePriceTrackingSpider(scrapy.Spider):
    name = "vaaree_price_tracking"

    allowed_domains = [
        "vaaree.com",
        "www.vaaree.com",
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
        self.logger.info(
            "VAAREE EXISTING PRODUCT PRICE TRACKER - "
            "CRAWL4AI + BEAUTIFULSOUP"
        )
        self.logger.info("TEST_LIMIT=%s", TEST_LIMIT)
        self.logger.info(
            "Output JSON=%s | Failed JSON=%s",
            OUTPUT_FILE,
            FAILED_OUTPUT_FILE,
        )
        self.logger.info("Using stored URLs only; no product discovery")
        self.logger.info("Product names are sourced from PostgreSQL only")
        self.logger.info("=" * 70)

        try:
            products = get_vaaree_products()
        except Exception as exc:
            self.logger.error(
                "Could not fetch existing Vaaree products: %s",
                exc,
                exc_info=True,
            )
            return

        if not products:
            self.logger.warning(
                "No eligible existing Vaaree products found."
            )
            return

        self.logger.info(
            "Fetched %s existing products. Starting Crawl4AI...",
            len(products),
        )

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(
                    executor,
                    run_crawl4ai_process,
                    products,
                )

        except Exception as exc:
            self.logger.error(
                "Crawl4AI process failed: %s",
                exc,
                exc_info=True,
            )
            return

        if not result or not result.get("success"):
            self.logger.error("Vaaree price tracking failed.")
            return

        results = result.get("results") or []
        pipeline_count = 0
        skipped_count = 0

        self.logger.info(
            "[CRAWL4AI COMPLETE] Received %s results",
            len(results),
        )

        for item in results:
            if not isinstance(item, dict):
                skipped_count += 1
                self.logger.warning(
                    "[SKIPPED] Invalid result object: %r",
                    item,
                )
                continue

            if item.get("status") != "success" or item.get("price") is None:
                skipped_count += 1

                self.logger.warning(
                    "[PIPELINE SKIP] ID=%s | Status=%s | "
                    "DB price preserved=%s | HTTP=%s | Error=%s",
                    item.get("product_id"),
                    item.get("status"),
                    item.get("database_price"),
                    item.get("http_status"),
                    item.get("error"),
                )
                continue

            self.logger.info(
                "[READY FOR PIPELINE] ID=%s | Old=%s | Current=%s | "
                "Difference=%s | Changed=%s",
                item.get("product_id"),
                item.get("database_price"),
                item.get("price"),
                item.get("price_difference"),
                item.get("price_changed"),
            )

            pipeline_count += 1
            yield item

        self.logger.info(
            "Finished | Scraped=%s | Sent to pipeline=%s | Skipped=%s | JSON=%s",
            len(results),
            pipeline_count,
            skipped_count,
            result.get("output_file"),
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    products = get_vaaree_products()

    if not products:
        print("No eligible existing Vaaree products found.")
        raise SystemExit(0)

    result = run_crawl4ai_process(products)

    print(
        "Standalone mode writes JSON only. "
        "Scrapy pipelines run only with `scrapy crawl`."
    )
    print("Output:", result.get("output_file"))