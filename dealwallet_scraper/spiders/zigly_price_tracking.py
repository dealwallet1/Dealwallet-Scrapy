
from itertools import product
import os
import re
import sys
import json
import asyncio
import logging
import multiprocessing

import psycopg2
import scrapy

from datetime import datetime
from urllib.parse import urlparse, urljoin
from concurrent.futures import ProcessPoolExecutor

from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

STORE_NAME = "Zigly"
BASE_URL = "https://zigly.com"

MAX_RETRIES = 3
PAGE_TIMEOUT = 45000
BATCH_SIZE = 25

# Test with one product first.
# Set to None to process every existing Zigly product.
TEST_LIMIT = 10

INPUT_FILE = "zigly_existing_products.json"
OUTPUT_FILE = "zigly_existing_scraped_results.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
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


def safe_float(value):
    if value is None:
        return None

    try:
        return round(float(value), 2)
    except (ValueError, TypeError):
        return None


def clean_discount(value):
    if value is None:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)", str(value))

    if not match:
        return None

    try:
        return int(round(float(match.group(1))))
    except (ValueError, TypeError):
        return None


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

        return int(round(
            ((original_price - price) / original_price) * 100
        ))

    except (ValueError, TypeError, ZeroDivisionError):
        return None


def normalize_url(url):
    """Remove query parameters and fragments from the scraping URL."""
    if not url:
        return None

    try:
        parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return urljoin(BASE_URL, url)

        return (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        )

    except Exception:
        return url


def safe_json_dump(data, filename):
    path = os.path.abspath(filename)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False,
            default=str,
        )

    return path


# ============================================================
# DATABASE
# ============================================================

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
            "Missing .env settings: " + ", ".join(missing)
        )

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_existing_zigly_products():
    """Fetch existing Zigly products and their database metadata."""
    connection = None
    cursor = None

    try:
        logger.info("Connecting to PostgreSQL")

        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                p.id AS product_id,
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
            JOIN public.organization AS o
                ON p.organization_id = o.id
            JOIN public.stores AS s
                ON p.store_id = s.id
            LEFT JOIN public.categories AS c
                ON p.categories_id = c.id
            WHERE LOWER(TRIM(s.name)) = LOWER(TRIM(%s))
              AND p.product_link IS NOT NULL
              AND TRIM(p.product_link) <> ''
            ORDER BY p.id;
        """

        cursor.execute(query, (STORE_NAME,))
        rows = cursor.fetchall()

        columns = [column[0] for column in cursor.description]

        products = []

        for row in rows:
            product = dict(zip(columns, row))

            product["product_id"] = str(product["product_id"])

            # The price-history pipeline expects names, not UUIDs.
            product["organization_id"] = product.pop(
                "organization_name", None
            )
            product["store_id"] = product.get("store_name")
            product["categories_id"] = product.get("category_name")

            product["scrape_url"] = normalize_url(
                product.get("product_link")
            )

            # Keep the stored database values for fallback/comparison.
            product["price"] = safe_float(product.get("price"))
            product["original_price"] = safe_float(
                product.get("original_price")
            )
            product["ratings"] = safe_float(
                product.get("ratings")
            )

            products.append(product)

        if TEST_LIMIT is not None:
            products = products[:TEST_LIMIT]

        path = safe_json_dump(products, INPUT_FILE)

        logger.info("Existing Zigly products fetched: %s", len(products))
        logger.info("Input JSON saved: %s", path)

        return products

    except Exception:
        logger.exception("Failed to fetch existing Zigly products")
        raise

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# PLAYWRIGHT HELPERS
# ============================================================

async def get_first_text(page, selectors, timeout=3000):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()

            for index in range(count):
                try:
                    value = clean_text(
                        await locator.nth(index).inner_text(
                            timeout=timeout
                        )
                    )

                    if value:
                        return value

                except Exception:
                    continue

        except Exception:
            continue

    return None


async def get_first_attribute(
    page,
    selectors,
    attribute,
    timeout=3000,
):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()

            for index in range(count):
                try:
                    value = clean_text(
                        await locator.nth(index).get_attribute(
                            attribute,
                            timeout=timeout,
                        )
                    )

                    if value:
                        return value

                except Exception:
                    continue

        except Exception:
            continue

    return None


async def get_selected_variant(page):
    """Read the selected variant JSON when Zigly exposes it."""
    try:
        locator = page.locator(
            'script[data-selected-variant]'
        ).first

        if await locator.count():
            raw_json = await locator.inner_text(timeout=3000)

            if raw_json:
                data = json.loads(raw_json)

                if isinstance(data, dict):
                    return data

    except Exception as error:
        logger.debug("Selected variant JSON unavailable: %s", error)

    return None


def variant_price_to_rupees(value):
    """Zigly's variant JSON prices are stored in paise."""
    if value is None:
        return None

    try:
        return int(round(float(value) / 100))
    except (ValueError, TypeError):
        return None


async def get_selected_variant_title(page, variant):
    if isinstance(variant, dict):
        title = clean_text(variant.get("title"))

        if title:
            return title

    return await get_first_text(
        page,
        [
            "variant-selects .variant-name",
            "variant-selects select option:checked",
            "variant-selects input:checked",
            ".product-form__input select option:checked",
        ],
    )


async def get_main_image(page):
    """
    Return one primary product image, not a list of gallery images,
    recommendation images, payment icons, or delivery badges.
    """
    selectors = [
        ".product__media-item.is-active img",
        ".product__media-item:first-child img",
        ".product__media img",
        ".product-gallery img",
        ".product-single__media img",
        "img.product__image",
        'meta[property="og:image"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector).first

            if not await locator.count():
                continue

            if selector.startswith("meta"):
                image_url = await locator.get_attribute("content")
            else:
                image_url = (
                    await locator.get_attribute("src")
                    or await locator.get_attribute("data-src")
                    or await locator.get_attribute("data-original")
                )

            if not image_url:
                continue

            image_url = image_url.strip()

            if image_url.startswith("data:"):
                continue

            if image_url.startswith("//"):
                image_url = "https:" + image_url

            if image_url.startswith("/"):
                image_url = urljoin(BASE_URL, image_url)

            if not image_url.startswith(("https://", "http://")):
                continue

            # Avoid obvious non-product SVG icons.
            if image_url.lower().endswith(".svg"):
                continue

            return image_url

        except Exception:
            continue

    return None


async def get_product_description(page):
    """
    Prefer the product-description section rather than collecting
    every accordion, recommendation, and site-wide tip.
    """
    selectors = [
        '[itemprop="description"]',
        ".product__description",
        ".product-description",
        ".product__accordion .accordion-content",
        "div.accordion-content",
    ]

    for selector in selectors:
        try:
            locators = page.locator(selector)
            count = await locators.count()

            for index in range(count):
                try:
                    text = clean_text(
                        await locators.nth(index).inner_text(
                            timeout=3000
                        )
                    )

                    if not text:
                        continue

                    # Skip site-wide tips and category descriptions
                    # if they appear as standalone sections.
                    if text.lower().startswith(
                        ("zigly tip:", "subcategory description:")
                    ):
                        continue

                    return text

                except Exception:
                    continue

        except Exception:
            continue

    return None


async def is_access_challenge(page):
    # A real product page should not be marked blocked just because
    # some challenge-related words appear in its content.
    for selector in [
        "h1.main-heading",
        ".price-item--sale",
        ".compare-at-price",
        'script[data-selected-variant]',
    ]:
        try:
            if await page.locator(selector).count():
                return False
        except Exception:
            pass

    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""

    try:
        body = (
            await page.locator("body").inner_text(timeout=3000)
        ).lower()
    except Exception:
        body = ""

    content = title + " " + body

    return any(
        phrase in content
        for phrase in (
            "verify you are human",
            "access denied",
            "security check",
            "captcha",
            "temporarily blocked",
            "checking your browser",
        )
    )


# ============================================================
# RESULT BUILDER - MATCHES MYNTRA OUTPUT SCHEMA
# ============================================================

def make_result(
    product,
    url,
    price=None,
    original_price=None,
    discount=None,
    ratings=None,
    description=None,
    image_link=None,
    status="failed",
    http_status=None,
    scraped_name=None,
):
    database_price = product.get("price")

    price_changed = None
    price_difference = None

    if database_price is not None and price is not None:
        try:
            old_price = int(float(database_price))
            new_price = int(float(price))

            price_changed = new_price != old_price
            price_difference = new_price - old_price

        except (ValueError, TypeError):
            pass

    if discount is None:
        discount = calculate_discount(price, original_price)

    # Match Myntra fallback behavior for these fields.
    final_ratings = (
        ratings if ratings is not None else product.get("ratings")
    )

    final_description = (
        description if description else product.get("description")
    )

    final_image = (
        image_link if image_link else product.get("image_link")
    )

    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "database_price": database_price,
        "price": price,
        "original_price": original_price,
        "currency": product.get("currency") or "₹",
        "discount": discount,
        "ratings": final_ratings,
        "description": final_description,
        "image_link": final_image,
        "product_link": product.get("product_link"),
        "scrape_url": url,
        "organization_id": product.get("organization_id"),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name"),
        "categories_id": product.get("categories_id"),
        "category_name": product.get("category_name"),
        "affiliate_url": product.get("affiliate_url"),
        "price_changed": price_changed,
        "price_difference": price_difference,
        "created_at": datetime.now().isoformat(),
        "status": status,
        "http_status": http_status,
        "scraped_name": scraped_name,
    }


# ============================================================
# SCRAPE ONE PRODUCT
# ============================================================

async def scrape_product(browser, product, index, total):
    original_url = product.get("product_link")
    url = product.get("scrape_url") or normalize_url(original_url)

    product_name = product.get("name") or "Unknown product"

    logger.info(
        "SCRAPING PRODUCT %s/%s | %s",
        index,
        total,
        product_name,
    )
    logger.info("URL: %s", url)

    if not url:
        return make_result(
            product=product,
            url=url,
            status="failed",
            scraped_name=None,
        )

    last_error = None
    last_http_status = None

    for attempt in range(1, MAX_RETRIES + 1):
        page = None

        try:
            page = await browser.new_page()

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            if response is not None:
                last_http_status = response.status

                logger.info(
                    "HTTP STATUS | %s | %s",
                    response.status,
                    product_name,
                )

                if response.status >= 400:
                    raise RuntimeError(
                        f"HTTP status {response.status}"
                    )

            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=2000,
                )
            except PlaywrightTimeoutError:
                pass

            await page.wait_for_timeout(1000)

            if await is_access_challenge(page):
                raise RuntimeError(
                    "Access challenge or blocked page detected"
                )

            # ----------------------------------------------------
            # NAME
            # ----------------------------------------------------

            scraped_name = await get_first_text(
                page,
                [
                    "h1.main-heading",
                    "h1.product__title",
                    "h1",
                ],
            )

            # ----------------------------------------------------
            # SELECTED VARIANT
            # ----------------------------------------------------

            variant = await get_selected_variant(page)
            variant_title = await get_selected_variant_title(
                page,
                variant,
            )

            # ----------------------------------------------------
            # CURRENT PRICE
            # ----------------------------------------------------

            price = None

            if isinstance(variant, dict):
                if variant.get("price") is not None:
                    price = variant_price_to_rupees(
                        variant.get("price")
                    )

            if price is None:
                price_text = await get_first_text(
                    page,
                    [
                        ".price-item--sale.compare_main-price",
                        ".price-item--sale",
                        ".compare_main-price",
                        ".price__current",
                        ".product-price",
                    ],
                )

                price = clean_price(price_text)

            # ----------------------------------------------------
            # ORIGINAL PRICE
            # ----------------------------------------------------

            original_price = None

            if isinstance(variant, dict):
                if variant.get("compare_at_price") is not None:
                    original_price = variant_price_to_rupees(
                        variant.get("compare_at_price")
                    )

            if original_price is None:
                original_price_text = await get_first_text(
                    page,
                    [
                        ".price-item--regular.compare-at-price",
                        ".compare-at-price",
                        ".price__compare",
                        "s.price-item",
                        ".price-item--regular",
                    ],
                )

                original_price = clean_price(original_price_text)

            if (
                price is not None
                and original_price is not None
                and original_price < price
            ):
                original_price = None

            # ----------------------------------------------------
            # DISCOUNT
            # ----------------------------------------------------

            discount_text = await get_first_text(
                page,
                [
                    ".discount-container .card_product_price_percentoff",
                    ".card_product_price_percentoff",
                    ".discount-percentage",
                    ".price__badge-sale",
                ],
            )

            discount = clean_discount(discount_text)

            if discount is None:
                discount = calculate_discount(price, original_price)

            # ----------------------------------------------------
            # RATING
            # ----------------------------------------------------

            # ----------------------------------------------------
            # RATING
            # ----------------------------------------------------

            rating_text = await get_first_text(
                page,
                [
                    ".custom-reviews-main-svg.review-first .custom-average-number",
                    ".custom-average-number",
                    "[itemprop='ratingValue']",
                    "[data-rating]",
                ],
            )

            if rating_text is None:
                rating_text = await get_first_attribute(
                    page,
                    [
                        "[itemprop='ratingValue']",
                        "[data-rating]",
                    ],
                    "content",
                )

            ratings = safe_float(rating_text)

            # ----------------------------------------------------
            # DESCRIPTION
            # ----------------------------------------------------

            description = await get_product_description(page)

            # ----------------------------------------------------
            # ONE MAIN IMAGE
            # ----------------------------------------------------

            image_link = await get_main_image(page)

            # ----------------------------------------------------
            # BUILD RESULT
            # ----------------------------------------------------

            if price is None:
                raise RuntimeError(
                    "Could not extract the current product price"
                )

            result = make_result(
                product=product,
                url=url,
                price=price,
                original_price=original_price,
                discount=discount,
                ratings=ratings,
                description=description,
                image_link=image_link,
                status="success",
                http_status=last_http_status,
                scraped_name=scraped_name,
            )

            logger.info(
                "PRICE RESULT | DB=%s | CURRENT=%s | CHANGED=%s | DIFF=%s",
                result["database_price"],
                result["price"],
                result["price_changed"],
                result["price_difference"],
            )

            if variant_title:
                logger.info("SELECTED VARIANT | %s", variant_title)

            return result

        except Exception as error:
            last_error = str(error)

            logger.warning(
                "ATTEMPT %s/%s FAILED | %s | %s",
                attempt,
                MAX_RETRIES,
                product_name,
                last_error,
            )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(attempt * 2)

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    failed_result = make_result(
        product=product,
        url=url,
        price=None,
        original_price=None,
        discount=None,
        ratings=None,
        description=None,
        image_link=None,
        status="failed",
        http_status=last_http_status,
        scraped_name=None,
    )

    # Keep the same Myntra fields, adding an error only on failures.
    failed_result["error"] = last_error

    return failed_result


# ============================================================
# PLAYWRIGHT SCRAPER
# ============================================================

async def scrape_zigly_products(products):
    results = []
    total = len(products)

    if not products:
        return results

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
        )

        try:
            context = await browser.new_context(
                viewport={
                    "width": 1366,
                    "height": 768,
                },
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )

            try:
                await context.add_init_script(
                    """
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    """
                )

                for start in range(0, total, BATCH_SIZE):
                    batch = products[start:start + BATCH_SIZE]

                    logger.info(
                        "PROCESSING BATCH | %s-%s / %s",
                        start + 1,
                        min(start + BATCH_SIZE, total),
                        total,
                    )

                    for offset, product in enumerate(
                        batch,
                        start=start + 1,
                    ):
                        result = await scrape_product(
                            browser=context,
                            product=product,
                            index=offset,
                            total=total,
                        )

                        results.append(result)

                    if start + BATCH_SIZE < total:
                        await asyncio.sleep(1)

            finally:
                await context.close()

        finally:
            await browser.close()

    return results


def run_playwright_process(products):
    """Run Playwright in a separate process on Windows."""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(
                scrape_zigly_products(products)
            )
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass

            asyncio.set_event_loop(None)
            loop.close()

    return asyncio.run(scrape_zigly_products(products))


# ============================================================
# SAVE RESULTS AND SUMMARY
# ============================================================

def save_results(results):
    output_path = safe_json_dump(results, OUTPUT_FILE)

    success = sum(
        1 for item in results
        if item.get("status") == "success"
    )

    failed = len(results) - success

    changed = sum(
        1 for item in results
        if item.get("price_changed") is True
    )

    unchanged = sum(
        1 for item in results
        if item.get("price_changed") is False
    )

    logger.info("=" * 80)
    logger.info("ZIGLY PRICE TRACKING COMPLETED")
    logger.info("TOTAL PRODUCTS : %s", len(results))
    logger.info("SUCCESS        : %s", success)
    logger.info("FAILED         : %s", failed)
    logger.info("PRICE CHANGED  : %s", changed)
    logger.info("PRICE UNCHANGED: %s", unchanged)
    logger.info("OUTPUT JSON    : %s", output_path)
    logger.info("=" * 80)

    return output_path


# ============================================================
# SCRAPY SPIDER
# ============================================================

class ZiglyPriceTrackingSpider(scrapy.Spider):
    name = "zigly_price_tracking"
    allowed_domains = ["zigly.com"]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "CONCURRENT_REQUESTS": 1,
        "RETRY_ENABLED": False,
        "DOWNLOAD_DELAY": 1,
    }

    async def start(self):
        logger.info("=" * 80)
        logger.info("ZIGLY EXISTING PRODUCT PRICE TRACKING STARTED")
        logger.info("MODE: EXISTING PRODUCTS ONLY")
        logger.info("=" * 80)

        try:
            products = get_existing_zigly_products()
        except Exception:
            logger.exception("Could not fetch existing Zigly products")
            return

        if not products:
            logger.warning("No existing Zigly products found")
            return

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                results = await loop.run_in_executor(
                    executor,
                    run_playwright_process,
                    products,
                )

            save_results(results)

            # Yield results through the existing Scrapy pipeline.
            for result in results:
                yield result

        except Exception:
            logger.exception("Zigly price tracking failed")


# ============================================================
# WINDOWS ENTRY POINT
# ============================================================

if __name__ == "__main__":
    multiprocessing.freeze_support()

    products = get_existing_zigly_products()
    results = run_playwright_process(products)
    save_results(results)
