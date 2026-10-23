
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

# Set to 10 for testing or None to process all existing products.
TEST_LIMIT = None

CONCURRENT_REQUESTS = 1
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "bovee_existing_products.json"
OUTPUT_FILE = "bovee_price_tracking_results.json"
FAILED_OUTPUT_FILE = "bovee_failed_products.json"

PRICE_SELECTOR = ".current-price .money"
ORIGINAL_PRICE_SELECTOR = ".was-price .money"


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

    if host != "boveee.com" and not host.endswith(".boveee.com"):
        return None

    return url


def get_bovee_products():
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
            "Connecting to PostgreSQL to fetch existing Bovee products..."
        )

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )

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
            if TEST_LIMIT is None:
                cursor.execute(query, ("%bovee%",))
            else:
                cursor.execute(
                    query + " LIMIT %s",
                    ("%bovee%", TEST_LIMIT),
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

            products.append(
                {
                    "product_id": str(product_id),

                    # Product name is taken only from PostgreSQL.
                    "name": name,

                    "price": (
                        float(price) if price is not None else None
                    ),
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
                }
            )

        save_json(INPUT_FILE, products)

        logging.info(
            "Database rows=%s | Eligible URLs=%s | Invalid URLs=%s",
            len(rows),
            len(products),
            invalid_urls,
        )

        return products

    finally:
        if connection:
            connection.close()


def extract_product_data(html, product):
    soup = BeautifulSoup(html or "", "html.parser")

    # Extract price fields only. Never scrape the product name.
    price_element = soup.select_one(PRICE_SELECTOR)
    original_price_element = soup.select_one(ORIGINAL_PRICE_SELECTOR)

    price = extract_price(
        price_element.get_text(" ", strip=True)
        if price_element
        else None
    )

    original_price = extract_price(
        original_price_element.get_text(" ", strip=True)
        if original_price_element
        else None
    )

    # Shopify product JSON provides a fallback for price and stock.
    product_json = {}
    json_element = soup.select_one('script[id^="ProductJson-"]')

    if json_element:
        try:
            product_json = json.loads(
                json_element.string or json_element.get_text()
            )
        except (json.JSONDecodeError, TypeError):
            product_json = {}

    if isinstance(product_json, dict):
        if price is None and product_json.get("price") is not None:
            try:
                price = float(
                    Decimal(str(product_json["price"])) / 100
                )
            except (InvalidOperation, ValueError):
                price = None

        if (
            original_price is None
            and product_json.get("compare_at_price") is not None
        ):
            try:
                original_price = float(
                    Decimal(
                        str(product_json["compare_at_price"])
                    ) / 100
                )
            except (InvalidOperation, ValueError):
                original_price = None

        available = product_json.get("available")
        out_of_stock = available is False

    else:
        out_of_stock = False

    discount = calculate_discount(price, original_price)

    # Preserve existing database values when page fields are unavailable.
    if original_price is None:
        original_price = product.get("original_price")

    if discount is None:
        discount = product.get("discount")

    logging.info(
        "[EXTRACTED] ID=%s | Name=%s | Price=%s | MRP=%s | "
        "Discount=%s | OutOfStock=%s",
        product.get("product_id"),
        product.get("name"),
        price,
        original_price,
        discount,
        out_of_stock,
    )

    return price, original_price, discount, out_of_stock


def make_result(
    product,
    price,
    original_price,
    discount,
    status,
    http_status,
    error=None,
):
    return {
        "product_id": product.get("product_id"),

        # Always use the name retrieved from the database.
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
        "ratings": product.get("ratings"),
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
                "[CRAWL4AI RESPONSE] ID=%s | HTTP=%s | Success=%s",
                product.get("product_id"),
                status_code,
                getattr(result, "success", None),
            )

            if status_code == 403:
                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    "blocked_403",
                    status_code,
                    "HTTP 403: Bovee denied the request.",
                )

            if status_code is not None and 400 <= status_code < 500:
                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    "http_error",
                    status_code,
                    f"HTTP {status_code}; client error.",
                )

            if status_code is not None and status_code >= 500:
                if attempt < MAX_RETRIES:
                    logging.warning(
                        "[SERVER ERROR RETRY] ID=%s | HTTP=%s",
                        product.get("product_id"),
                        status_code,
                    )
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    "http_error",
                    status_code,
                    f"HTTP {status_code} after {MAX_RETRIES} attempts.",
                )

            if not html.strip():
                if attempt < MAX_RETRIES:
                    logging.warning(
                        "[EMPTY HTML] ID=%s | Attempt=%s/%s",
                        product.get("product_id"),
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
                    "failed",
                    status_code,
                    error_message or "Crawl4AI returned empty HTML.",
                )

            (
                price,
                original_price,
                discount,
                out_of_stock,
            ) = extract_product_data(html, product)

            if price is not None:
                output = make_result(
                    product,
                    price,
                    original_price,
                    discount,
                    "success",
                    status_code,
                )

                output["out_of_stock"] = out_of_stock

                output["price_changed"] = values_differ(
                    product.get("price"), price
                )

                output["original_price_changed"] = values_differ(
                    product.get("original_price"), original_price
                )

                output["discount_changed"] = values_differ(
                    product.get("discount"), discount
                )

                output["ratings_changed"] = None

                output["price_difference"] = (
                    float(price) - float(product["price"])
                    if product.get("price") is not None
                    else None
                )

                logging.info(
                    "[SCRAPED] ID=%s | Old=%s | Current=%s | Changed=%s",
                    product.get("product_id"),
                    product.get("price"),
                    price,
                    output["price_changed"],
                )

                return output

            if out_of_stock:
                return make_result(
                    product,
                    None,
                    original_price,
                    discount,
                    "out_of_stock",
                    status_code,
                    "Product is out of stock and no live price was found.",
                )

            logging.warning(
                "[MISSING PRICE] ID=%s | HTTP=%s | HTML length=%s",
                product.get("product_id"),
                status_code,
                len(html),
            )

            return make_result(
                product,
                None,
                original_price,
                discount,
                "missing_price",
                status_code,
                "Page returned HTML, but no live price was found.",
            )

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

            else:
                return make_result(
                    product,
                    None,
                    product.get("original_price"),
                    product.get("discount"),
                    "failed",
                    None,
                    f"{type(exc).__name__}: {exc}",
                )

    return make_result(
        product,
        None,
        product.get("original_price"),
        product.get("discount"),
        "failed",
        None,
        f"Failed after {MAX_RETRIES} attempts.",
    )


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    logging.info("[JSON SAVED] %s", os.path.abspath(path))


async def run_crawl4ai(products):
    if not products:
        save_json(OUTPUT_FILE, [])
        save_json(FAILED_OUTPUT_FILE, [])
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

    save_json(OUTPUT_FILE, results)

    # Failed JSON contains product ID and product link.
    failed_results = [
        {
            "product_id": item.get("product_id"),
            "product_link": item.get("product_link"),
        }
        for item in results
        if item.get("status") != "success"
        or item.get("price") is None
    ]

    save_json(FAILED_OUTPUT_FILE, failed_results)

    successful = sum(
        item.get("status") == "success" and item.get("price") is not None
        for item in results
    )

    blocked = sum(
        item.get("status") == "blocked_403"
        for item in results
    )

    out_of_stock = sum(
        item.get("status") == "out_of_stock"
        for item in results
    )

    logging.info(
        "[SUMMARY] Total=%s | Successful=%s | Blocked403=%s | "
        "OutOfStock=%s | Failed/Missing=%s",
        len(results),
        successful,
        blocked,
        out_of_stock,
        len(failed_results),
    )

    logging.info(
        "[FAILED JSON] Saved %s failed products to %s",
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
        "failed_output_file": FAILED_OUTPUT_FILE,
    }


class BoveePriceTrackingSpider(scrapy.Spider):
    name = "bovee_price_tracking"

    allowed_domains = [
        "boveee.com",
        "www.boveee.com",
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
            "BOVEE EXISTING PRODUCT PRICE TRACKER - "
            "CRAWL4AI + BEAUTIFULSOUP"
        )
        self.logger.info("TEST_LIMIT=%s", TEST_LIMIT)
        self.logger.info("Using existing database product URLs only")
        self.logger.info("Product names will remain from the database")
        self.logger.info("Failed products JSON: %s", FAILED_OUTPUT_FILE)
        self.logger.info("=" * 70)

        try:
            products = get_bovee_products()

        except Exception as exc:
            self.logger.error(
                "Could not fetch existing Bovee products: %s",
                exc,
                exc_info=True,
            )
            return

        if not products:
            self.logger.warning(
                "No eligible existing Bovee products found."
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
            self.logger.error("Bovee price tracking failed.")
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

            # Preserve existing database prices for failed products.
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
            "Finished | Scraped=%s | Sent to pipeline=%s | "
            "Skipped=%s | All JSON=%s | Failed JSON=%s",
            len(results),
            pipeline_count,
            skipped_count,
            result.get("output_file"),
            result.get("failed_output_file"),
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    products = get_bovee_products()

    if not products:
        print("No eligible existing Bovee products found.")
        raise SystemExit(0)

    result = run_crawl4ai_process(products)

    print("Standalone mode writes JSON only.")
    print("Scrapy pipelines run only with `scrapy crawl`.")
    print("All results:", result.get("output_file"))
    print("Failed products:", result.get("failed_output_file"))
