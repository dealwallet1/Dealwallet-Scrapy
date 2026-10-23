
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
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

TEST_LIMIT = None
CONCURRENT_REQUESTS = 1
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "neemans_existing_products.json"
OUTPUT_FILE = "neemans_price_tracking_results.json"
FAILED_OUTPUT_FILE = "neemans_failed_products.json"

PRICE_SELECTOR = "sale-price.nms-price-list__current-price"
ORIGINAL_PRICE_SELECTOR = (
    "compare-at-price.nms-price-list__original-price .line-through"
)
DISCOUNT_SELECTOR = ".nms-price-list__discount-percentage"
RATING_SELECTOR = ".jdgm-prev-badge"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
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

        return (
            int(amount)
            if amount == amount.to_integral_value()
            else float(amount)
        )
    except (InvalidOperation, ValueError):
        return None


def extract_discount(value):
    if not value:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value))
    if not match:
        return None

    number = float(match.group(1))
    return round(number) if 0 <= number <= 100 else None


def extract_rating(value):
    if value is None:
        return None

    match = re.search(
        r"([0-5](?:\.\d+)?)",
        str(value),
    )
    if not match:
        return None

    rating = float(match.group(1))
    return rating if 0 <= rating <= 5 else None


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    try:
        price = Decimal(str(price))
        original_price = Decimal(str(original_price))

        if original_price <= 0 or price >= original_price:
            return 0 if original_price > 0 and price >= original_price else None

        return round(
            float((original_price - price) / original_price * 100)
        )
    except (InvalidOperation, ValueError, TypeError):
        return None


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
    if host != "neemans.com" and not host.endswith(".neemans.com"):
        return None

    return url


def get_db_connection():
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

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_neemans_products():
    connection = None

    try:
        connection = get_db_connection()

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

        with connection.cursor() as cursor:
            params = ("%neeman%",)

            if TEST_LIMIT is not None:
                cursor.execute(
                    query + " LIMIT %s",
                    params + (TEST_LIMIT,),
                )
            else:
                cursor.execute(query, params)

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
                    "[INVALID URL] Name=%s | URL=%s",
                    name,
                    product_link,
                )
                continue

            products.append({
                "product_id": str(product_id),
                "name": name,
                "price": float(price) if price is not None else None,
                "original_price": (
                    float(original_price)
                    if original_price is not None else None
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
            "[DATABASE] Rows=%s | Selected=%s | Invalid URLs=%s",
            len(rows),
            len(products),
            invalid_urls,
        )
        logging.info("Saved input: %s", os.path.abspath(INPUT_FILE))

        return products

    finally:
        if connection:
            connection.close()


def first_selector_value(soup, selector):
    element = soup.select_one(selector)
    if not element:
        return None

    value = (
        element.get("content")
        or element.get("data-price")
        or element.get_text(" ", strip=True)
    )
    return clean_text(value)


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
                continue

            if not isinstance(entry, dict):
                continue

            graph = entry.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)

            entry_type = entry.get("@type")
            if entry_type == "Product" or (
                isinstance(entry_type, list) and "Product" in entry_type
            ):
                yield entry


def extract_product_data(html, product):
    soup = BeautifulSoup(html or "", "html.parser")

    # Always keep the product name fetched from PostgreSQL.
    # Only live price-related fields and rating are extracted from the page.
    price = extract_price(
        first_selector_value(soup, PRICE_SELECTOR)
    )
    original_price = extract_price(
        first_selector_value(soup, ORIGINAL_PRICE_SELECTOR)
    )
    discount = extract_discount(
        first_selector_value(soup, DISCOUNT_SELECTOR)
    )

    rating_element = soup.select_one(RATING_SELECTOR)
    rating = None

    if rating_element:
        rating = extract_rating(
            rating_element.get("data-average-rating")
        )

    product_json = next(iter(iter_product_jsonld(soup)), {})
    offers = product_json.get("offers", {}) if product_json else {}

    if isinstance(offers, list):
        offers = offers[0] if offers else {}

    if not isinstance(offers, dict):
        offers = {}

    if price is None:
        price = extract_price(
            offers.get("price") or offers.get("lowPrice")
        )

    if original_price is None:
        original_price = extract_price(offers.get("highPrice"))

    if discount is None:
        discount = calculate_discount(price, original_price)

    if rating is None:
        aggregate = product_json.get("aggregateRating", {})
        if isinstance(aggregate, dict):
            rating = extract_rating(aggregate.get("ratingValue"))

    # Existing database values are fallbacks, not proof of live prices.
    if original_price is None:
        original_price = product.get("original_price")

    if discount is None:
        discount = calculate_discount(price, original_price)

    return price, original_price, discount, rating


def make_result(
    product,
    price,
    original_price,
    discount,
    rating,
    status,
    http_status=None,
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
            discount if discount is not None else product.get("discount")
        ),
        "database_ratings": product.get("ratings"),
        "ratings": (
            rating if rating is not None else product.get("ratings")
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


async def scrape_one(crawler, run_config, product, index, total, semaphore):
    url = product["scrape_url"]

    logging.info(
        "[PRODUCT %s/%s] Name=%s | URL=%s",
        index,
        total,
        product.get("name"),
        url,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                result = await crawler.arun(url=url, config=run_config)

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
                "[RESPONSE] Name=%s | HTTP=%s | Success=%s",
                product.get("name"),
                status_code,
                getattr(result, "success", None),
            )

            if status_code is not None and 400 <= status_code < 500:
                return make_result(
                    product, None, None, None, None,
                    "http_error", status_code,
                    f"HTTP {status_code}; database values preserved.",
                )

            if status_code is not None and status_code >= 500:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product, None, None, None, None,
                    "http_error", status_code,
                    f"HTTP {status_code} after retries.",
                )

            if not html.strip():
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product, None, None, None, None,
                    "failed", status_code,
                    error_message or "Crawl4AI returned empty HTML.",
                )

            price, mrp, discount, rating = extract_product_data(
                html, product
            )

            if price is None:
                if attempt < MAX_RETRIES:
                    logging.warning(
                        "[MISSING PRICE] Name=%s | Attempt=%s/%s",
                        product.get("name"),
                        attempt,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product, None, mrp, discount, rating,
                    "missing_price", status_code,
                    "No live selling price found.",
                )

            output = make_result(
                product, price, mrp, discount, rating,
                "success", status_code,
            )

            output["price_changed"] = values_differ(
                product.get("price"), price
            )
            output["original_price_changed"] = values_differ(
                product.get("original_price"), mrp
            )
            output["discount_changed"] = values_differ(
                product.get("discount"), discount
            )
            output["ratings_changed"] = values_differ(
                product.get("ratings"), rating
            )
            output["price_difference"] = (
                float(price) - float(product["price"])
                if product.get("price") is not None else None
            )

            logging.info(
                "[SCRAPED] Name=%s | Old=%s | Current=%s | MRP=%s",
                output.get("name"),
                product.get("price"),
                price,
                mrp,
            )

            return output

        except Exception as exc:
            logging.warning(
                "[CRAWL ERROR] Name=%s | Attempt=%s/%s | %s",
                product.get("name"),
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(attempt * 2)

    return make_result(
        product, None, None, None, None,
        "failed", None,
        f"Failed after {MAX_RETRIES} attempts.",
    )


async def run_crawl4ai(products):
    if not products:
        return []

    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded",
        delay_before_return_html=1.0,
        verbose=False,
    )

    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await asyncio.gather(*[
            scrape_one(
                crawler,
                run_config,
                product,
                index,
                len(products),
                semaphore,
            )
            for index, product in enumerate(products, 1)
        ])

    order = {
        product.get("product_id"): index
        for index, product in enumerate(products)
    }
    results.sort(
        key=lambda item: order.get(item.get("product_id"), 10**9)
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            results, file, indent=2,
            ensure_ascii=False, default=str,
        )

    failed_results = [
        {
            "name": item.get("name"),
            "product_link": item.get("product_link"),
        }
        for item in results
        if item.get("status") != "success" or item.get("price") is None
    ]

    with open(FAILED_OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            failed_results, file, indent=2,
            ensure_ascii=False, default=str,
        )

    successful = sum(
        item.get("status") == "success" for item in results
    )

    logging.info(
        "[SUMMARY] Total=%s | Successful=%s | Failed=%s",
        len(results),
        successful,
        len(results) - successful,
    )
    logging.info("Results: %s", os.path.abspath(OUTPUT_FILE))
    logging.info("Failed: %s", os.path.abspath(FAILED_OUTPUT_FILE))

    return results


def run_crawl4ai_process(products):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        force=True,
    )
    return {
        "success": True,
        "results": asyncio.run(run_crawl4ai(products)),
        "output_file": OUTPUT_FILE,
    }


class NeemansPriceTrackingSpider(scrapy.Spider):
    name = "neemans_price_tracking"
    allowed_domains = ["neemans.com", "www.neemans.com"]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "EXTENSIONS": {
            "scrapy.extensions.logstats.LogStats": None,
        },
        "CONCURRENT_REQUESTS": 1,
    }

    async def start(self):
        self.logger.info("=" * 70)
        self.logger.info("NEEMANS EXISTING PRODUCT PRICE TRACKER (DATABASE NAMES ONLY)")
        self.logger.info("TEST_LIMIT=%s", TEST_LIMIT)
        self.logger.info("Existing database URLs only; no product discovery.")
        self.logger.info("=" * 70)

        try:
            products = get_neemans_products()
        except Exception as exc:
            self.logger.error(
                "Could not fetch Neemans products: %s",
                exc,
                exc_info=True,
            )
            return

        if not products:
            self.logger.warning("No eligible Neemans products found.")
            return

        self.logger.info("Fetched %s existing products.", len(products))

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
            self.logger.error("Neemans price tracking failed.")
            return

        results = result.get("results") or []
        sent = 0
        skipped = 0

        for item in results:
            if (
                not isinstance(item, dict)
                or item.get("status") != "success"
                or item.get("price") is None
            ):
                skipped += 1
                self.logger.warning(
                    "[PIPELINE SKIP] Name=%s | Status=%s | HTTP=%s | "
                    "Error=%s | Database values preserved",
                    item.get("name") if isinstance(item, dict) else None,
                    item.get("status") if isinstance(item, dict) else None,
                    item.get("http_status") if isinstance(item, dict) else None,
                    item.get("error") if isinstance(item, dict) else None,
                )
                continue

            sent += 1
            self.logger.info(
                "[READY FOR PIPELINE] Name=%s | Old=%s | Current=%s",
                item.get("name"),
                item.get("database_price"),
                item.get("price"),
            )
            yield item

        self.logger.info(
            "Finished | Results=%s | Sent to pipeline=%s | Skipped=%s",
            len(results),
            sent,
            skipped,
        )


if __name__ == "__main__":
    products = get_neemans_products()
    if not products:
        print("No eligible Neemans products found.")
        raise SystemExit(0)

    run_crawl4ai_process(products)
