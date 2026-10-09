import os
import re
import sys
import json
import asyncio
import logging
import psycopg2
import multiprocessing

from datetime import datetime
from urllib.parse import urlparse, urljoin
from concurrent.futures import ProcessPoolExecutor

from dotenv import load_dotenv
from bs4 import BeautifulSoup
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)
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

STORE_NAME = "Myntra"
BASE_URL = "https://www.myntra.com"

MAX_RETRIES = 3
PAGE_TIMEOUT = 60000
PRODUCT_WAIT_TIMEOUT = 15000
BATCH_SIZE = 25

# 5 = test five products
# None = process all products
TEST_LIMIT = 5

INPUT_FILE = "myntra_existing_products.json"
OUTPUT_FILE = "myntra_existing_scraped_results.json"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)

# ============================================================
# TEXT / PRICE HELPERS
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

    value = (
        value.replace(",", "")
        .replace("₹", "")
        .replace("Rs.", "")
        .replace("Rs", "")
    )

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
        value = str(value).strip()

        if not value:
            return None

        return round(float(value), 2)

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

        return round(
            (original_price - price) / original_price * 100
        )

    except (ValueError, TypeError, ZeroDivisionError):
        return None


# ============================================================
# URL / JSON HELPERS
# ============================================================


def normalize_url(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return urljoin(BASE_URL, url)

        return (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        ).rstrip("?")

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
# PLAYWRIGHT GENERIC HELPERS
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


# ============================================================
# MYNTRA PRODUCT NAME
# ============================================================


async def extract_product_name(page):
    return await get_first_text(
        page,
        [
            "h1.pdp-name",
            "h1.title",
            "h1",
        ],
    )


# ============================================================
# MYNTRA CURRENT PRICE
# ============================================================


async def extract_price(page):
    value = await get_first_text(
        page,
        [
            "span.pdp-selling-price",
            "span.pdp-price strong",
            "div.pdp-price strong",
            ".pdp-price",
        ],
    )

    return clean_price(value)


# ============================================================
# MYNTRA ORIGINAL PRICE
# ============================================================


async def extract_original_price(page):
    value = await get_first_text(
        page,
        [
            "span.pdp-mrp",
            "span.pdp-mrp-verbiage-amt",
            "span.pdp-mrp s",
            ".pdp-mrp",
        ],
    )

    return clean_price(value)


# ============================================================
# MYNTRA DISCOUNT
# ============================================================


async def extract_discount(page):
    value = await get_first_text(
        page,
        [
            "span.pdp-discount",
            ".pdp-discount",
        ],
    )

    if value:
        match = re.search(r"(\d+)", value)

        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


# ============================================================
# MYNTRA RATING
# ============================================================


async def extract_rating(page):
    selectors = [
        "div.pdp-rating",
        "div.index-overallRating",
        ".pdp-rating-container",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()

            for index in range(count):
                element = locator.nth(index)

                for attribute in [
                    "data-rating",
                    "aria-label",
                ]:
                    try:
                        value = await element.get_attribute(attribute)
                        rating = safe_float(value)

                        if rating is not None and rating <= 5:
                            return rating

                    except Exception:
                        continue

                try:
                    text = clean_text(
                        await element.inner_text(timeout=3000)
                    )

                    if text:
                        match = re.search(
                            r"(\d+(?:\.\d+)?)",
                            text,
                        )

                        if match:
                            rating = safe_float(match.group(1))

                            if rating is not None and rating <= 5:
                                return rating

                except Exception:
                    continue

        except Exception:
            continue

    return None


# ============================================================
# MYNTRA PRODUCT IMAGE
# ============================================================

async def extract_image(page):
    """
    Supports Myntra product images rendered using CSS background-image:

    <div class="image-grid-imageContainer">
        <div class="image-grid-image"
             style="background-image: url(&quot;https://assets.myntassets.com/...jpg&quot;);">
        </div>
    </div>

    Returns a valid product-image URL or None.
    """

    from html import unescape

    def valid_product_image(raw_url):
        if not raw_url:
            return None

        raw_url = unescape(str(raw_url)).strip().strip("\"'")

        if not raw_url:
            return None

        if raw_url.lower().startswith(
            ("data:", "blob:", "javascript:")
        ):
            return None

        image_url = urljoin(BASE_URL, raw_url)
        parsed = urlparse(image_url)

        if parsed.scheme not in ("http", "https"):
            return None

        host = (parsed.hostname or "").lower()
        path_lower = parsed.path.lower()

        # Allow Myntra's product-image hosts.
        if (
            "myntassets.com" not in host
            and "myntra.com" not in host
        ):
            return None

        # Reject logos and placeholder assets.
        blocked_terms = (
            "logo",
            "myntra-logo",
            "myntralogo",
            "placeholder",
            "sprite",
            "icon",
            "no-image",
            "noimage",
            "default-image",
        )

        if any(term in path_lower for term in blocked_terms):
            return None

        # Ignore SVG and non-image assets.
        if not path_lower.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".avif")
        ):
            return None

        return image_url

    # --------------------------------------------------------
    # 1. Extract CSS background-image URLs
    # --------------------------------------------------------

    background_selectors = [
        "div.image-grid-imageContainer div.image-grid-image",
        "div.image-grid-image",
        ".image-grid-imageContainer [style*='background-image']",
    ]

    for selector in background_selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()

            for index in range(min(count, 12)):
                element = locator.nth(index)

                try:
                    style = await element.get_attribute("style")

                    if not style:
                        continue

                    style = (
                        style.replace("&quot;", '"')
                        .replace("&#39;", "'")
                    )

                    match = re.search(
                        r"url\(\s*(['\"]?)(.*?)\1\s*\)",
                        style,
                        flags=re.IGNORECASE,
                    )

                    if match:
                        image = valid_product_image(match.group(2))

                        if image:
                            logger.info(
                                "PRODUCT IMAGE FOUND: %s",
                                image,
                            )
                            return image

                except Exception:
                    continue

        except Exception:
            continue

    # --------------------------------------------------------
    # 2. Fallback: extract image URLs from img/source elements
    # --------------------------------------------------------

    image_selectors = [
        "div.image-grid-imageContainer img",
        "img.image-grid-image",
        'img[class*="image-grid-image"]',
        "div.image-grid-imageContainer source",
    ]

    for selector in image_selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()

            for index in range(min(count, 20)):
                element = locator.nth(index)

                try:
                    candidates = []

                    for attribute in (
                        "src",
                        "data-src",
                        "data-original",
                        "data-lazy-src",
                        "srcset",
                        "data-srcset",
                    ):
                        value = await element.get_attribute(attribute)

                        if value:
                            candidate = (
                                value.split(",")[0]
                                .strip()
                                .split()[0]
                            )
                            candidates.append(candidate)

                    for candidate in candidates:
                        image = valid_product_image(candidate)

                        if image:
                            logger.info(
                                "PRODUCT IMAGE FOUND: %s",
                                image,
                            )
                            return image

                except Exception:
                    continue

        except Exception:
            continue

    logger.warning(
        "No valid Myntra product image found; returning None"
    )

    return None


# ============================================================
# MYNTRA DESCRIPTION
# ============================================================


async def extract_description(page):
    selectors = [
        "div.pdp-product-description-content",
        "div.pdp-product-description",
        "div.pdp-sizeFitDesc",
        "div.pdp-sizeFitDescContent",
        "div.pdp-product-description-content p",
    ]

    value = await get_first_text(
        page,
        selectors,
        timeout=3000,
    )

    if value:
        return value

    try:
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        for selector in selectors:
            node = soup.select_one(selector)

            if node:
                value = clean_text(
                    node.get_text(" ", strip=True)
                )

                if value:
                    return value

    except Exception:
        pass

    return None


# ============================================================
# DATABASE: FETCH EXISTING PRODUCTS ONLY
# ============================================================


def get_existing_myntra_products():
    connection = None
    cursor = None

    try:
        required = {
            "DB_HOST": DB_HOST,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
        }

        missing = [
            key
            for key, value in required.items()
            if not value
        ]

        if missing:
            raise RuntimeError(
                "Missing environment variables: "
                + ", ".join(missing)
            )

        logger.info("=" * 80)
        logger.info("CONNECTING TO POSTGRESQL")
        logger.info("=" * 80)

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
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
                p.store_id,
                p.categories_id,
                p.affiliate_url,
                s.name AS store_name,
                c.name AS category_name
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

        logger.info("Executing Myntra product query...")

        cursor.execute(query, (STORE_NAME,))
        rows = cursor.fetchall()

        logger.info("DATABASE ROWS FETCHED: %s", len(rows))

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
                store_id,
                categories_id,
                affiliate_url,
                store_name,
                category_name,
            ) = row

            normalized_url = normalize_url(product_link)

            products.append(
                {
                    "product_id": str(product_id),
                    "name": name,
                    "price": price,
                    "original_price": original_price,
                    "currency": currency or "INR",
                    "discount": discount,
                    "ratings": safe_float(ratings),
                    "description": description,
                    "image_link": image_link,
                    "product_link": product_link,

                    # Matches the organization-name convention
                    # used by the Flipkart spider.
                    "organization_id": organization_name,

                    "store_id": (
                        str(store_id) if store_id else None
                    ),
                    "store_name": store_name,
                    
                    "categories_id": category_name,
                    "category_name": category_name,

                    "category_name": category_name,
                    "affiliate_url": affiliate_url,
                    "scrape_url": normalized_url,
                }
            )

        logger.info("MYNTRA PRODUCTS FOUND: %s", len(products))

        path = safe_json_dump(products, INPUT_FILE)

        logger.info("INPUT JSON SAVED: %s", path)

        return products

    except Exception:
        logger.exception("FAILED TO FETCH MYNTRA PRODUCTS")
        return []

    finally:
        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# BUILD SCRAPED RESULT
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

    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "database_price": database_price,
        "price": price,
        "original_price": original_price,
        "currency": product.get("currency") or "₹",
        "discount": discount,
        "ratings": (
            ratings
            if ratings is not None
            else product.get("ratings")
        ),
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
       "store_id": product.get("store_name") or "Myntra",
       "store_name": product.get("store_name") or "Myntra",  
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
# SCRAPE ONE EXISTING PRODUCT
# ============================================================


async def scrape_product(context, product, index, total):
    original_url = product.get("product_link")

    url = (
        product.get("scrape_url")
        or normalize_url(original_url)
    )

    logger.info(
        "PRODUCT %s/%s | id=%s | %s",
        index,
        total,
        product.get("product_id"),
        url,
    )

    if not url:
        return make_result(
            product,
            url,
            status="url_missing",
        )

    for attempt in range(1, MAX_RETRIES + 1):
        page = None

        try:
            page = await context.new_page()

            logger.info(
                "OPENING URL | attempt %s/%s",
                attempt,
                MAX_RETRIES,
            )

            response = await page.goto(
                url,
                wait_until="commit",
                timeout=PAGE_TIMEOUT,
            )

            status_code = (
                response.status if response else None
            )

            logger.info("HTTP STATUS | %s | %s", status_code, url)

            if status_code and status_code != 200:
                logger.warning(
                    "HTTP %s | attempt %s/%s | %s",
                    status_code,
                    attempt,
                    MAX_RETRIES,
                    url,
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    url,
                    status="non_200",
                    http_status=status_code,
                )

            # Wait for the product detail page.
            try:
                await page.locator("h1.pdp-name").wait_for(
                    state="attached",
                    timeout=PRODUCT_WAIT_TIMEOUT,
                )

            except Exception:
                logger.warning(
                    "PDP NAME NOT FOUND WITHIN WAIT TIME"
                )
                await page.wait_for_timeout(3000)

            # Extract product details.
            price = await extract_price(page)
            original_price = await extract_original_price(page)
            discount = await extract_discount(page)
            ratings = await extract_rating(page)
            scraped_name = await extract_product_name(page)
            image_link = await extract_image(page)
            description = await extract_description(page)

            logger.info(
                "EXTRACTED | NAME=%s | PRICE=%s | MRP=%s | "
                "DISCOUNT=%s | RATING=%s",
                scraped_name,
                price,
                original_price,
                discount,
                ratings,
            )

            # If price is missing, retry.
            if price is None:
                logger.warning(
                    "PRICE NOT FOUND | attempt %s/%s | %s",
                    attempt,
                    MAX_RETRIES,
                    url,
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue

                return make_result(
                    product,
                    url,
                    original_price=original_price,
                    discount=discount,
                    ratings=ratings,
                    description=description,
                    image_link=image_link,
                    status="price_not_found",
                    http_status=status_code,
                    scraped_name=scraped_name,
                )

            result = make_result(
                product,
                url,
                price=price,
                original_price=original_price,
                discount=discount,
                ratings=ratings,
                description=description,
                image_link=image_link,
                status="success",
                http_status=status_code,
                scraped_name=scraped_name,
            )

            logger.info(
                "RESULT | %s | DB=%s | CURRENT=%s | "
                "CHANGED=%s | DIFF=%s",
                product.get("name"),
                result.get("database_price"),
                result.get("price"),
                result.get("price_changed"),
                result.get("price_difference"),
            )

            return result

        except PlaywrightTimeoutError:
            logger.warning(
                "TIMEOUT | attempt %s/%s | %s",
                attempt,
                MAX_RETRIES,
                url,
            )

            if attempt == MAX_RETRIES:
                return make_result(
                    product,
                    url,
                    status="timeout",
                )

        except Exception as exc:
            logger.warning(
                "SCRAPE ERROR | attempt %s/%s | %s | %s",
                attempt,
                MAX_RETRIES,
                url,
                exc,
            )

            if attempt == MAX_RETRIES:
                return make_result(
                    product,
                    url,
                    status="error",
                )

        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    return make_result(
        product,
        url,
        status="failed",
    )


# ============================================================
# PLAYWRIGHT SCRAPER
# ============================================================


async def scrape_myntra_products(products):
    results = []
    total = len(products)

    if not products:
        return results

    async with async_playwright() as playwright:
        browser = None
        context = None

        try:
            logger.info("STARTING MYNTRA PLAYWRIGHT")
            logger.info("LAUNCHING INSTALLED GOOGLE CHROME")

            browser = await playwright.chromium.launch(
                channel="chrome",
                headless=True,
                args=[
                    "--disable-quic",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )

            logger.info("GOOGLE CHROME STARTED")

            context = await browser.new_context(
                viewport={
                    "width": 1366,
                    "height": 768,
                },
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                service_workers="block",
                ignore_https_errors=True,
                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/154.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,image/avif,"
                        "image/webp,image/apng,*/*;q=0.8"
                    ),
                    "Accept-Language": "en-IN,en;q=0.9",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Upgrade-Insecure-Requests": "1",
                },
            )

            await context.add_init_script(
                """
                Object.defineProperty(
                    navigator,
                    'webdriver',
                    { get: () => undefined }
                );

                Object.defineProperty(
                    navigator,
                    'languages',
                    { get: () => ['en-IN', 'en'] }
                );
                """
            )

            # Process existing products in batches.
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
                        context,
                        product,
                        offset,
                        total,
                    )

                    results.append(result)

                if start + BATCH_SIZE < total:
                    await asyncio.sleep(1)

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

    return results


# ============================================================
# PLAYWRIGHT CHILD PROCESS
# ============================================================


def run_playwright_process(products):
    logger.info("PLAYWRIGHT CHILD PROCESS STARTED")

    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(
                scrape_myntra_products(products)
            )

        finally:
            try:
                loop.run_until_complete(
                    loop.shutdown_asyncgens()
                )
            except Exception:
                pass

            asyncio.set_event_loop(None)
            loop.close()

    return asyncio.run(
        scrape_myntra_products(products)
    )


# ============================================================
# SAVE RESULTS
# ============================================================


def save_results(results):
    output_path = safe_json_dump(
        results,
        OUTPUT_FILE,
    )

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
    logger.info("MYNTRA PRICE TRACKING COMPLETED")
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


class MyntraPriceTrackingSpider(Spider):
    name = "myntra_price_tracking"
    allowed_domains = ["myntra.com"]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "CONCURRENT_REQUESTS": 1,
        "RETRY_ENABLED": False,
        "DOWNLOAD_DELAY": 1,
    }

    async def start(self):
        logger.info("=" * 80)
        logger.info("MYNTRA EXISTING PRODUCT PRICE TRACKING STARTED")
        logger.info("NO PRODUCT DISCOVERY")
        logger.info("=" * 80)

        # Fetch only products already stored in PostgreSQL.
        products = get_existing_myntra_products()

        if not products:
            logger.warning("NO EXISTING MYNTRA PRODUCTS FOUND")
            return

        logger.info(
            "TOTAL EXISTING MYNTRA PRODUCTS: %s",
            len(products),
        )

        # Test only a few products first.
        if TEST_LIMIT is not None:
            products = products[:TEST_LIMIT]

            logger.info(
                "TEST MODE ENABLED | PROCESSING ONLY %s PRODUCT(S)",
                len(products),
            )

        else:
            logger.info(
                "FULL MODE ENABLED | PROCESSING ALL %s PRODUCTS",
                len(products),
            )

        logger.info("STARTING PLAYWRIGHT PROCESS")

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                results = await loop.run_in_executor(
                    executor,
                    run_playwright_process,
                    products,
                )

            logger.info(
                "PLAYWRIGHT PROCESS COMPLETED | RESULTS: %s",
                len(results),
            )

            save_results(results)

            # Send each result to the configured Scrapy pipeline.
            for result in results:
                yield result

        except Exception:
            logger.exception("MYNTRA PLAYWRIGHT SCRAPING FAILED")


# ============================================================
# MAIN
# ============================================================


if __name__ == "__main__":
    multiprocessing.freeze_support()

    logger.info("Run this spider using:")
    logger.info("scrapy crawl myntra_price_tracking")