
import os
import re
import sys
import json
import asyncio
import logging
import psycopg2

from datetime import datetime
from urllib.parse import urlparse, urljoin
from concurrent.futures import ProcessPoolExecutor

from dotenv import load_dotenv
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from scrapy import Spider


# ============================================================
# CONFIG
# ============================================================

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

STORE_NAME = "Snapdeal"
BASE_URL = "https://www.snapdeal.com"

MAX_RETRIES = 3
PAGE_TIMEOUT = 60000
TEST_LIMIT = None  # Fetch all existing Snapdeal products

INPUT_FILE = "snapdeal_existing_products.json"
OUTPUT_FILE = "snapdeal_existing_scraped_results.json"
FAILED_FILE = "snapdeal_failed_products.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):

    if value is None:
        return None

    value = re.sub(r"\s+", " ", str(value)).strip()

    return value or None
def clean_rating(value):
    if value is None:
        return None

    value = str(value).strip()

    match = re.search(r"\d+(?:\.\d+)?", value)

    if not match:
        return None

    try:
        return float(match.group(0))
    except (ValueError, TypeError):
        return None

def clean_price(value):

    if value is None:
        return None

    value = clean_text(value)

    if not value:
        return None

    value = value.replace(",", "")

    match = re.search(r"\d+(?:\.\d+)?", value)

    if not match:
        return None

    try:
        return int(float(match.group(0)))
    except (ValueError, TypeError):
        return None


# ============================================================
# CORRECT DISCOUNT CALCULATION
# ============================================================

def calculate_discount(price, original_price):

    if price is None or original_price is None:
        return None

    try:

        price = float(price)
        original_price = float(original_price)

        if original_price <= 0:
            return None

        if price >= original_price:
            return 0

        discount = int(((original_price - price) / original_price) * 100 + 0.5)

        return round(discount)

    except (ValueError, TypeError, ZeroDivisionError):

        return None


def normalize_url(url):

    if not url:
        return None

    try:

        parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return urljoin(BASE_URL, url)

        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("?")

    except Exception:

        return url


# ============================================================
# PLAYWRIGHT SELECTOR HELPERS
# ============================================================

async def get_first_text(page, selectors):

    for selector in selectors:

        try:

            locator = page.locator(selector)

            count = await locator.count()

            for index in range(count):

                try:

                    value = clean_text(
                        await locator.nth(index).inner_text(
                            timeout=3000
                        )
                    )

                    if value:
                        return value

                except Exception:
                    continue

        except Exception:
            continue

    return None


async def get_first_attribute(page, selectors, attribute):

    for selector in selectors:

        try:

            locator = page.locator(selector)

            count = await locator.count()

            for index in range(count):

                try:

                    value = clean_text(
                        await locator.nth(index).get_attribute(
                            attribute,
                            timeout=3000
                        )
                    )

                    if value:
                        return value

                except Exception:
                    continue

        except Exception:
            continue

    return None


# ============================================================
# DESCRIPTION EXTRACTION
# ============================================================

def extract_description(html):

    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    selectors = [

        "p.mb-5.text-pretty.text-sm.leading-5.text-muted-foreground",

        "p.text-pretty.text-sm.leading-5.text-muted-foreground",

        "p[class*='text-pretty'][class*='leading-5']",

        "[data-testid*='description']",

        "[class*='product-description']",

        "[class*='productDescription']",

        "[class*='description']",

        "[class*='Description']",

        "[id*='description']",

        "[id*='Description']",

    ]

    for selector in selectors:

        texts = [

            clean_text(tag.get_text(" ", strip=True))

            for tag in soup.select(selector)

        ]

        texts = [

            text for text in texts

            if text and len(text) > 20

        ]

        if texts:
            return " ".join(dict.fromkeys(texts))

    meta = soup.select_one('meta[name="description"]')

    if meta:

        content = clean_text(meta.get("content"))

        if content:
            return content

    return None


# ============================================================
# IMAGE EXTRACTION
# ============================================================

async def extract_image(page):

    selectors = [

        'img[src*="/product/images/"]',

        'img[data-src*="/product/images/"]',

        "main img",

        "img",

    ]

    for selector in selectors:

        try:

            images = page.locator(selector)

            count = await images.count()

            for index in range(count):

                img = images.nth(index)

                for attr in ("src", "data-src", "data-lazy-src"):

                    try:

                        value = await img.get_attribute(attr)

                        if value and not value.startswith("data:"):

                            value = urljoin(BASE_URL, value)

                            if value.startswith("http"):
                                return value

                    except Exception:
                        continue

        except Exception:
            continue

    return None


# ============================================================
# DATABASE: FETCH EXISTING WOODLAND PRODUCTS
# ============================================================

def get_snapdeal_products():

    connection = None
    cursor = None

    try:

        required = {
            "DB_HOST": DB_HOST,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD
        }

        missing = [
            key for key, value in required.items()
            if not value
        ]

        if missing:

            raise RuntimeError(
                "Missing environment variables: "
                + ", ".join(missing)
            )

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        cursor = connection.cursor()

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

            FROM public.products p

            JOIN public.stores s
                ON p.store_id = s.id

            LEFT JOIN public.organization o
                ON p.organization_id = o.id

            LEFT JOIN public.categories c
                ON p.categories_id = c.id

            WHERE LOWER(TRIM(s.name)) = LOWER(TRIM(%s))

              AND p.product_link IS NOT NULL

              AND TRIM(p.product_link) <> ''

            ORDER BY p.id;

        """

        cursor.execute(query, (STORE_NAME,))

        rows = cursor.fetchall()

        products = []

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
                affiliate_url
            ) = row

            products.append({

                "product_id": str(product_id),

                "name": name,

                "price": price,

                "original_price": original_price,

                "currency": currency or "INR",

                "discount": discount,

                "ratings": ratings,

                "description": description,

                "image_link": image_link,

                "product_link": product_link,

                "organization_id": organization_name,

                "store_id": store_name,

                "store_name": store_name,

                "categories_id": category_name,

                "affiliate_url": affiliate_url,

                "scrape_url": normalize_url(product_link),

            })

        with open(
            INPUT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                products,
                file,
                indent=4,
                ensure_ascii=False
            )

        logger.info(
            "Fetched ALL existing Snapdeal products | Total=%s",
            len(products)
        )

        logger.info("Input saved: %s", INPUT_FILE)

        return products

    except Exception:

        logger.exception(
            "Could not fetch existing Snapdeal products"
        )

        return []

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# RESULT BUILDER
# ============================================================

def make_result(
    product,
    url,
    price=None,
    original_price=None,
    description=None,
    image_link=None,
    status="failed",
    http_status=None,
    scraped_name=None
):

    database_price = product.get("price")

    changed = None
    difference = None

    if database_price is not None and price is not None:

        try:

            old = int(float(database_price))

            changed = int(price) != old

            difference = int(price) - old

        except (ValueError, TypeError):
            pass

    # Calculate discount from actual prices.
    discount = calculate_discount(
        price,
        original_price
    )

    # Print every unsuccessful product directly in the scraper logs.
    if status != "success":
        logger.error(
            "FAILED PRODUCT | product_id=%s | name=%s | product_link=%s | status=%s | http_status=%s | scrape_url=%s",
            product.get("product_id"),
            product.get("name"),
            product.get("product_link"),
            status,
            http_status,
            url
        )

    return {

        "product_id": product.get("product_id"),

        "name": product.get("name"),

        "database_price": database_price,

        "price": price,

        "original_price": original_price,

        "currency": product.get("currency") or "₹",

        "discount": discount,

        "ratings": clean_rating(product.get("ratings")),

        "description": (
            description
            if description
            else product.get("description")
        ),

        "image_link": (
            image_link
            if image_link
            else product.get("image_link")
        ),

        "product_link": product.get("product_link"),

        "scrape_url": url,

        "organization_id": product.get("organization_id"),

        "store_id": product.get("store_id"),

        "store_name": product.get("store_name"),

        "categories_id": product.get("categories_id"),

        "affiliate_url": product.get("affiliate_url"),

        "price_changed": changed,

        "price_difference": difference,

        "created_at": datetime.now().isoformat(),

        "status": status,

        "http_status": http_status,

        "scraped_name": scraped_name,

    }


# ============================================================
# SCRAPE ONE EXISTING PRODUCT
# ============================================================

async def scrape_product(context, product, index, total):

    original_url = product.get("product_link")

    url = product.get("scrape_url") or normalize_url(original_url)

    logger.info(
        "PRODUCT %s/%s | id=%s | %s",
        index,
        total,
        product.get("product_id"),
        url
    )

    for attempt in range(1, MAX_RETRIES + 1):

        page = None

        try:

            page = await context.new_page()

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            status_code = response.status if response else None

            if status_code and status_code != 200:

                logger.warning(
                    "HTTP %s attempt %s for %s",
                    status_code,
                    attempt,
                    url
                )

                if attempt < MAX_RETRIES:

                    await asyncio.sleep(attempt * 2)

                    continue

                return make_result(
                    product,
                    url,
                    status="non_200",
                    http_status=status_code
                )

            await page.wait_for_timeout(2000)

            # ====================================================
            # CURRENT PRICE
            # ====================================================

            price_text = await get_first_text(page, [
                "[ itemprop='price' ]",
                "[itemprop='price']",
                ".payBlkBig",
                ".pdp-e-i-PAY-l",
                ".product-price",
                "[class*='selling-price']",
                "[class*='price']",
            ])

            price = clean_price(price_text)

            # ====================================================
            # ORIGINAL PRICE
            # ====================================================

            original_price_text = await get_first_text(page, [
                ".pdpCutPrice",
                ".pdp-e-i-PAY-l .strike",
                "del",
                "s",
                "[class*='cut-price']",
                "[class*='original-price']",
                "[class*='strike']",
            ])

            original_price = clean_price(original_price_text)

            # ====================================================
            # PRODUCT NAME
            # ====================================================

            scraped_name = await get_first_text(page, [
                "h1.pdp-e-i-head",
                "h1[itemprop='name']",
                "h1",
            ])

            # ====================================================
            # DESCRIPTION
            # ====================================================

            description = extract_description(
                await page.content()
            )

            # ====================================================
            # IMAGE
            # ====================================================

            image_link = await extract_image(page)

            # ====================================================
            # DISCOUNT CALCULATION
            # ====================================================

            discount = calculate_discount(
                price,
                original_price
            )

            logger.info(
                "Price=%s | Original=%s | Discount=%s%%",
                price,
                original_price,
                discount
            )

            if price is None:

                logger.warning(
                    "Price not found | attempt=%s/%s | %s",
                    attempt,
                    MAX_RETRIES,
                    url
                )

                if attempt < MAX_RETRIES:

                    await asyncio.sleep(attempt * 2)

                    continue

                return make_result(
                    product,
                    url,
                    original_price=original_price,
                    description=description,
                    image_link=image_link,
                    status="price_missing",
                    http_status=status_code,
                    scraped_name=scraped_name
                )

            result = make_result(

                product,
                url,
                price=price,
                original_price=original_price,
                description=description,
                image_link=image_link,
                status="success",
                http_status=status_code,
                scraped_name=scraped_name

            )

            logger.info(
                "DB Price=%s | Scraped Price=%s | Changed=%s | Difference=%s | Discount=%s%%",
                result["database_price"],
                result["price"],
                result["price_changed"],
                result["price_difference"],
                result["discount"]
            )

            return result

        except PlaywrightTimeoutError as exc:

            logger.warning(
                "Timeout attempt %s/%s: %s",
                attempt,
                MAX_RETRIES,
                exc
            )

        except Exception as exc:

            logger.exception(
                "Scrape attempt %s failed for %s: %s",
                attempt,
                url,
                exc
            )

        finally:

            if page:

                try:
                    await page.close()
                except Exception:
                    pass

        if attempt < MAX_RETRIES:

            await asyncio.sleep(attempt * 2)

    return make_result(
        product,
        url,
        status="failed"
    )


# ============================================================
# PLAYWRIGHT SCRAPER
# ============================================================

async def run_scraper(products):

    results = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(

            headless=True,

            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox"
            ]

        )

        context = await browser.new_context(

            viewport={
                "width": 1366,
                "height": 768
            },

            locale="en-IN",

            timezone_id="Asia/Kolkata",

        )

        for index, product in enumerate(products, start=1):

            result = await scrape_product(
                context,
                product,
                index,
                len(products)
            )

            results.append(result)

            await asyncio.sleep(1)

        await context.close()

        await browser.close()

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            results,
            file,
            indent=4,
            ensure_ascii=False
        )

    failed_products = [
        {
            "product_id": item.get("product_id"),
            "name": item.get("name"),
            "status": item.get("status"),
            "http_status": item.get("http_status"),
            "product_link": item.get("product_link"),
            "scrape_url": item.get("scrape_url"),
        }
        for item in results
        if item.get("status") != "success"
    ]

    with open(FAILED_FILE, "w", encoding="utf-8") as file:
        json.dump(failed_products, file, indent=4, ensure_ascii=False)

    logger.info("Failed product ID/name report saved: %s | Count=%s", FAILED_FILE, len(failed_products))

    # Print every unsuccessful product together in one terminal section.
    print("\n" + "=" * 110)
    print("ALL FAILED SNAPDEAL PRODUCTS (including price_missing)")
    print("=" * 110)

    if not failed_products:
        print("No failed products found.")
    else:
        for index, item in enumerate(failed_products, start=1):
            print(f"\nFAILED PRODUCT {index}")
            print(f"Product ID   : {item.get('product_id')}")
            print(f"Product Name : {item.get('name')}")
            print(f"Product Link : {item.get('product_link')}")
            print(f"Status       : {item.get('status')}")
            print(f"HTTP Status  : {item.get('http_status')}")

    print("\n" + "=" * 110)
    print(f"TOTAL FAILED PRODUCTS: {len(failed_products)}")
    print("=" * 110 + "\n")

    success = sum(
        r.get("status") == "success"
        for r in results
    )

    price_missing = sum(
        r.get("status") == "price_missing"
        for r in results
    )

    failed = sum(
        r.get("status") in ("failed", "non_200")
        for r in results
    )

    logger.info(
        "Finished | Total=%s | Success=%s | Price Missing=%s | Failed=%s",
        len(results),
        success,
        price_missing,
        failed
    )

    logger.info("Output saved: %s", OUTPUT_FILE)
    logger.info("Failed products saved: %s", FAILED_FILE)

    return results


# ============================================================
# WINDOWS-SAFE PLAYWRIGHT PROCESS
# ============================================================

def run_playwright_process(products):

    if sys.platform == "win32":

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return {

        "success": True,

        "results": asyncio.run(
            run_scraper(products)
        ),

        "output_file": OUTPUT_FILE,
        "failed_file": FAILED_FILE

    }


# ============================================================
# SCRAPY SPIDER
# ============================================================

class SnapdealExistingProductsSpider(Spider):

    name = "snapdeal_price_tracker"

    allowed_domains = [
        "snapdeal.com"
    ]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
    }

    async def start(self):

        self.logger.info(
            "SNAPDEAL EXISTING PRODUCTS PRICE TRACKER — NO PRODUCT DISCOVERY"
        )

        products = get_snapdeal_products()

        if not products:

            self.logger.warning(
                "No existing Snapdeal products found."
            )

            return

        loop = asyncio.get_running_loop()

        try:

            with ProcessPoolExecutor(max_workers=1) as executor:

                result = await loop.run_in_executor(

                    executor,

                    run_playwright_process,

                    products

                )

        except Exception as exc:

            self.logger.error(
                "Playwright process failed: %s",
                exc,
                exc_info=True
            )

            return

        if not result or not result.get("success"):

            self.logger.error(
                "Existing product scraping failed."
            )

            return

        scraped = result.get("results") or []

        sent = 0
        skipped = 0

        for product in scraped:

            if (

                not isinstance(product, dict)

                or product.get("status") != "success"

                or product.get("price") is None

            ):

                skipped += 1

                continue

            sent += 1

            yield product

        self.logger.info(
            "Products sent to pipeline: %s | skipped: %s",
            sent,
            skipped
        )


# ============================================================
# STANDALONE EXECUTION
# ============================================================

if __name__ == "__main__":

    products = get_snapdeal_products()

    if not products:

        raise SystemExit(
            "No existing Snapdeal products found."
        )

    result = run_playwright_process(products)

    print(
        "Results:",
        result.get("output_file")
    )

    print("Failed product report:", result.get("failed_file")
    )

    print(
        "Standalone mode does not run Scrapy item pipelines."
    )