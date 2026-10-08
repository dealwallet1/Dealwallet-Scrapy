import os
import re
import sys
import json
import asyncio
import logging
import psycopg2

from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from concurrent.futures import ProcessPoolExecutor

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

STORE_NAME = "AJIO"

BASE_URL = "https://www.ajio.com"

MAX_RETRIES = 3
PAGE_TIMEOUT = 60000

INPUT_FILE = "ajio_existing_products.json"
OUTPUT_FILE = "ajio_existing_scraped_results.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db_connection():

    missing = []

    if not DB_HOST:
        missing.append("DB_HOST")

    if not DB_NAME:
        missing.append("DB_NAME")

    if not DB_USER:
        missing.append("DB_USER")

    if not DB_PASSWORD:
        missing.append("DB_PASSWORD")

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(value):

    if value is None:
        return ""

    text = str(value)

    text = text.replace("\xa0", " ")

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# PRICE CLEANING
# ============================================================

def clean_price(value):

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    text = text.replace("₹", "")
    text = text.replace(",", "")

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        text,
    )

    if not match:
        return None

    try:

        number = float(match.group(1))

        if number.is_integer():
            return int(number)

        return number

    except Exception:

        return None


# ============================================================
# DISCOUNT EXTRACTION
# ============================================================

def extract_discount(value):

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*off",
        text,
        re.I,
    )

    if not match:

        match = re.search(
            r"(\d+(?:\.\d+)?)\s*%",
            text,
            re.I,
        )

    if not match:
        return None

    try:

        discount = float(match.group(1))

        if discount.is_integer():
            return int(discount)

        return discount

    except Exception:

        return None


# ============================================================
# CALCULATE DISCOUNT
# ============================================================

def calculate_discount(
    price,
    original_price,
):

    if price is None:
        return None

    if original_price is None:
        return None

    try:

        price = float(price)
        original_price = float(original_price)

        if original_price <= 0:
            return None

        if price >= original_price:
            return 0

        discount = (
            (original_price - price)
            / original_price
        ) * 100

        return round(discount)

    except Exception:

        return None


# ============================================================
# NORMALIZE URL
# ============================================================

def normalize_url(url):

    if not url:
        return None

    url = str(url).strip()

    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url

    elif url.startswith("/"):
        url = BASE_URL + url

    elif not url.startswith(("http://", "https://")):
        url = BASE_URL + "/" + url.lstrip("/")

    parsed = urlparse(url)

    if parsed.scheme and parsed.netloc:

        # Remove query parameters and fragments.
        # This also removes trailing '?'
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    return url.split("?", 1)[0].split("#", 1)[0]


# ============================================================
# GET FIRST TEXT
# ============================================================

async def get_first_text(
    page,
    selectors,
):

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            text = await locator.inner_text(
                timeout=5000
            )

            text = clean_text(text)

            if text:
                return text

        except Exception:
            continue

    return None


# ============================================================
# GET FIRST ATTRIBUTE
# ============================================================

async def get_first_attribute(
    page,
    selectors,
    attribute,
):

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            value = await locator.get_attribute(
                attribute
            )

            if value:

                value = clean_text(value)

                if value:
                    return value

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT PRODUCT NAME
# ============================================================

async def extract_product_name(page):

    selectors = [

        "h1.prod-name",

        "h1[role='heading'][aria-level='2'].prod-name",

        "h1",

    ]

    return await get_first_text(
        page,
        selectors,
    )


# ============================================================
# EXTRACT BRAND
# ============================================================

async def extract_brand(page):

    selectors = [

        "h2.brand-name",

        "h2[role='heading'].brand-name",

    ]

    return await get_first_text(
        page,
        selectors,
    )


# ============================================================
# EXTRACT CURRENT PRICE
# ============================================================

async def extract_current_price(page):

    selectors = [

        # VERIFIED AJIO SELECTOR
        "div.prod-sp",

        ".prod-price-section .prod-sp",

        ".prod-content .prod-sp",

        "[class*='prod-sp']",

    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            text = await locator.inner_text(
                timeout=5000
            )

            price = clean_price(text)

            if price is not None:

                logger.debug(
                    "AJIO CURRENT PRICE | selector=%s | value=%s",
                    selector,
                    price,
                )

                return price

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT MRP
# ============================================================

async def extract_original_price(page):

    selectors = [

        # VERIFIED AJIO SELECTOR
        "span.prod-cp",

        ".prod-price-sec .prod-cp",

        ".prod-price-section .prod-cp",

        "[class*='prod-cp']",

    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            text = await locator.inner_text(
                timeout=5000
            )

            price = clean_price(text)

            if price is not None:

                logger.debug(
                    "AJIO MRP | selector=%s | value=%s",
                    selector,
                    price,
                )

                return price

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT DISCOUNT
# ============================================================

async def extract_discount_from_page(page):

    selectors = [

        # VERIFIED AJIO SELECTOR
        "span.prod-discnt",

        ".prod-price-sec .prod-discnt",

        ".prod-price-section .prod-discnt",

        "[class*='discount-percent']",

        "[class*='prod-discnt']",

    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            text = await locator.inner_text(
                timeout=5000
            )

            discount = extract_discount(text)

            if discount is not None:

                logger.debug(
                    "AJIO DISCOUNT | selector=%s | value=%s",
                    selector,
                    discount,
                )

                return discount

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT IMAGE
# ============================================================

async def extract_image(page):

    selectors = [

        # VERIFIED AJIO PRODUCT IMAGE
        "img.img-alignment",

        ".product-image-gallery img.img-alignment",

        ".product-image-gallery img",

        "img[alt*='Product image']",

    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            ).first

            if await locator.count() == 0:
                continue

            for attribute in [
                "src",
                "data-src",
                "data-lazy-src",
            ]:

                value = await locator.get_attribute(
                    attribute
                )

                if not value:
                    continue

                value = value.strip()

                if value.startswith("data:"):
                    continue

                if value.startswith("//"):
                    value = "https:" + value

                elif value.startswith("/"):
                    value = BASE_URL + value

                if value.startswith("http"):
                    return value

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT RATING
# ============================================================

async def extract_rating(page):

    try:

        rating_section = page.locator(
            "#productrating"
        ).first

        if await rating_section.count():

            text = clean_text(
                await rating_section.inner_text(
                    timeout=5000
                )
            )

            match = re.search(
                r"\b([0-5](?:\.\d{1,2})?)\b",
                text,
            )

            if match:

                rating = float(
                    match.group(1)
                )

                if 0 <= rating <= 5:
                    return rating

    except Exception:
        pass

    selectors = [

        "[class*='rating']",

        "[class*='Rating']",

        "[aria-label*='rating' i]",

        "[aria-label*='stars' i]",

        "[data-rating]",

    ]

    for selector in selectors:

        try:

            locators = page.locator(
                selector
            )

            count = await locators.count()

            for index in range(
                min(count, 10)
            ):

                locator = locators.nth(index)

                values = []

                for attr in [
                    "aria-label",
                    "data-rating",
                    "title",
                ]:

                    value = await locator.get_attribute(
                        attr
                    )

                    if value:
                        values.append(value)

                try:

                    values.append(
                        await locator.inner_text(
                            timeout=2000
                        )
                    )

                except Exception:
                    pass

                for value in values:

                    match = re.search(
                        r"\b([0-5](?:\.\d{1,2})?)\b",
                        clean_text(value),
                    )

                    if match:

                        rating = float(
                            match.group(1)
                        )

                        if 0 <= rating <= 5:
                            return rating

        except Exception:
            continue

    return None


# ============================================================
# EXTRACT DESCRIPTION
# ============================================================

async def extract_description(page):

    selectors = [

        ".pdpTabSub",

        ".pdp-details",

        ".product-details",

        "#productdetails",

    ]

    details = []

    for selector in selectors:

        try:

            locators = page.locator(
                selector
            )

            count = await locators.count()

            if count == 0:
                continue

            for index in range(
                min(count, 50)
            ):

                locator = locators.nth(index)

                try:

                    text = clean_text(
                        await locator.inner_text(
                            timeout=2000
                        )
                    )

                    if text and text not in details:
                        details.append(text)

                except Exception:
                    continue

            if details:
                break

        except Exception:
            continue

    if not details:
        return None

    description = " ".join(details)

    return clean_text(description) or None


# ============================================================
# EXTRACT PRODUCT COLOR
# ============================================================

async def extract_color(page):

    selectors = [

        "p.prod-color",

        ".prod-color",

    ]

    return await get_first_text(
        page,
        selectors,
    )


# ============================================================
# EXTRACT AJIO PRODUCT CODE
# ============================================================

def extract_ajio_product_code(url):

    if not url:
        return None

    match = re.search(
        r"/p/([^/?#]+)",
        url,
        re.I,
    )

    if match:
        return match.group(1)

    return None


# ============================================================
# DATABASE - GET EXISTING AJIO PRODUCTS
# ============================================================

def get_ajio_products():

    connection = None
    cursor = None

    try:

        connection = get_db_connection()

        cursor = connection.cursor()

        query = """
            SELECT
                p.id,
                p.name,
                p.price,
                p.original_price,
                p.discount,
                p.product_link,
                p.ratings,
                p.description,
                p.image_link,
                p.organization_id,
                p.store_id,
                p.categories_id,
                p.affiliate_url,
                s.name AS store_name,
                c.name AS category_name
            FROM public.products p
            JOIN public.stores s
                ON p.store_id = s.id
            LEFT JOIN public.categories c
                ON p.categories_id = c.id
            WHERE LOWER(TRIM(s.name)) =
                  LOWER(TRIM(%s))
              AND p.product_link IS NOT NULL
              AND TRIM(p.product_link) <> ''
            ORDER BY p.id
        """

        cursor.execute(
            query,
            (STORE_NAME,),
        )

        rows = cursor.fetchall()

        products = []

        for row in rows:

            (
                product_id,
                name,
                price,
                original_price,
                discount,
                product_link,
                rating,
                description,
                image_link,
                organization_id,
                store_id,
                categories_id,
                affiliate_url,
                store_name,
                category_name,
            ) = row

            scrape_url = normalize_url(
                product_link
            )

            products.append(
                {
                    "product_id": (
                        str(product_id)
                        if product_id
                        else None
                    ),

                    "name": name,

                    "price": price,

                    "original_price": original_price,

                    "currency": "₹",

                    "discount": discount,

                    "ratings": rating,

                    "description": description,

                    "image_link": image_link,

                    "product_link": product_link,

                    "scrape_url": scrape_url,

                    "organization_id": (
                        str(organization_id)
                        if organization_id
                        else None
                    ),

                    "store_id": (
                        str(store_id)
                        if store_id
                        else None
                    ),

                    "store_name": store_name,

                    "categories_id": (
                        str(categories_id)
                        if categories_id
                        else None
                    ),

                    "category_name": category_name,

                    "affiliate_url": affiliate_url,
                }
            )

        logger.info(
            "AJIO existing products fetched: %s",
            len(products),
        )

        # Save products fetched from database
        with open(
            INPUT_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=4,
                default=str,
            )

        logger.info(
            "AJIO input JSON saved: %s",
            os.path.abspath(INPUT_FILE),
        )

        return products

    except Exception:

        logger.exception(
            "Could not fetch existing AJIO products"
        )

        raise

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# BUILD RESULT
# ============================================================

def make_result(
    product,
    scraped_price,
    scraped_original_price,
    scraped_discount,
    scraped_rating,
    scraped_description,
    scraped_image,
    scraped_name,
    http_status,
):

    database_price = product.get("price")

    original_price = (
        scraped_original_price
        if scraped_original_price is not None
        else product.get("original_price")
    )

    discount = scraped_discount

    if discount is None:

        discount = calculate_discount(
            scraped_price,
            original_price,
        )

    price_changed = False
    price_difference = 0

    if (
        database_price is not None
        and scraped_price is not None
    ):

        try:

            old_price = float(
                database_price
            )

            new_price = float(
                scraped_price
            )

            price_changed = (
                old_price != new_price
            )

            price_difference = (
                new_price - old_price
            )

        except Exception:

            price_changed = False
            price_difference = 0

    final_name = (
        scraped_name
        if scraped_name
        else product.get("name")
    )

    final_rating = (
        scraped_rating
        if scraped_rating is not None
        else product.get("ratings")
    )

    final_description = (
        scraped_description
        if scraped_description
        else product.get("description")
    )

    final_image = (
        scraped_image
        if scraped_image
        else product.get("image_link")
    )

    return {

        "product_id": product.get(
            "product_id"
        ),

        "name": final_name,

        "database_price": database_price,

        "price": scraped_price,

        "original_price": original_price,

        "currency": "₹",

        "discount": discount,

        "ratings": final_rating,

        "description": final_description,

        "image_link": final_image,

        "product_link": product.get(
            "product_link"
        ),

        "scrape_url": product.get(
            "scrape_url"
        ),

        "organization_id": product.get(
            "organization_id"
        ),

        "store_id": product.get(
            "store_id"
        ),

        "store_name": product.get(
            "store_name"
        ),

        "categories_id": product.get(
            "categories_id"
        ),

        "category_name": product.get(
            "category_name"
        ),

        "affiliate_url": product.get(
            "affiliate_url"
        ),

        "price_changed": price_changed,

        "price_difference": price_difference,

        "created_at": datetime.now().isoformat(),

        "status": "success",

        "http_status": http_status,

        "scraped_name": scraped_name,

    }


# ============================================================
# SCRAPE SINGLE AJIO PRODUCT
# ============================================================

async def scrape_product(
    page,
    product,
    index,
    total,
):

    url = product.get("scrape_url")

    product_name = product.get("name")

    if not url:

        logger.warning(
            "[%s/%s] No URL | Product: %s",
            index,
            total,
            product_name,
        )

        return {
            **product,
            "database_price": product.get("price"),
            "price": None,
            "price_changed": False,
            "price_difference": 0,
            "status": "failed",
            "http_status": None,
            "created_at": datetime.now().isoformat(),
        }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            logger.info(
                "[%s/%s] AJIO | Attempt %s/%s | %s",
                index,
                total,
                attempt,
                MAX_RETRIES,
                url,
            )

            # ------------------------------------------------
            # OPEN EXISTING PRODUCT URL
            # ------------------------------------------------

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            http_status = (
                response.status
                if response
                else None
            )

            logger.info(
                "AJIO HTTP STATUS | %s | %s",
                http_status,
                url,
            )

            # ------------------------------------------------
            # HANDLE HTTP ERRORS
            # ------------------------------------------------

            if http_status and http_status >= 400:

                logger.warning(
                    "AJIO HTTP ERROR | %s | %s",
                    http_status,
                    url,
                )

                if attempt < MAX_RETRIES:

                    # Longer delay for 403
                    if http_status == 403:
                        await asyncio.sleep(
                            5 * attempt
                        )
                    else:
                        await asyncio.sleep(
                            2 * attempt
                        )

                    continue

                return {
                    **product,
                    "database_price": product.get(
                        "price"
                    ),
                    "price": None,
                    "price_changed": False,
                    "price_difference": 0,
                    "status": "failed",
                    "http_status": http_status,
                    "created_at": datetime.now().isoformat(),
                }

            # ------------------------------------------------
            # WAIT FOR PRODUCT PRICE
            # ------------------------------------------------

            try:

                await page.locator(
                    "div.prod-sp"
                ).first.wait_for(
                    state="visible",
                    timeout=15000,
                )

            except Exception:

                await page.wait_for_timeout(
                    3000
                )

            # ------------------------------------------------
            # PRODUCT NAME
            # ------------------------------------------------

            scraped_name = (
                await extract_product_name(
                    page
                )
            )

            # ------------------------------------------------
            # CURRENT PRICE
            # ------------------------------------------------

            scraped_price = (
                await extract_current_price(
                    page
                )
            )

            # ------------------------------------------------
            # ORIGINAL PRICE / MRP
            # ------------------------------------------------

            scraped_original_price = (
                await extract_original_price(
                    page
                )
            )

            # ------------------------------------------------
            # DISCOUNT
            # ------------------------------------------------

            scraped_discount = (
                await extract_discount_from_page(
                    page
                )
            )

            # ------------------------------------------------
            # CALCULATE DISCOUNT IF NOT AVAILABLE
            # ------------------------------------------------

            if (
                scraped_discount is None
                and scraped_price is not None
                and scraped_original_price is not None
            ):

                scraped_discount = (
                    calculate_discount(
                        scraped_price,
                        scraped_original_price,
                    )
                )

            # ------------------------------------------------
            # RATING
            # ------------------------------------------------

            scraped_rating = (
                await extract_rating(
                    page
                )
            )

            # ------------------------------------------------
            # DESCRIPTION
            # ------------------------------------------------

            scraped_description = (
                await extract_description(
                    page
                )
            )

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            scraped_image = (
                await extract_image(
                    page
                )
            )

            # ------------------------------------------------
            # COLOR
            # ------------------------------------------------

            scraped_color = (
                await extract_color(
                    page
                )
            )

            # ------------------------------------------------
            # PRICE VALIDATION
            # ------------------------------------------------

            if scraped_price is None:

                logger.warning(
                    "AJIO PRICE MISSING | %s | DB price=%s",
                    url,
                    product.get("price"),
                )

                if attempt < MAX_RETRIES:

                    await page.wait_for_timeout(
                        3000
                    )

                    continue

                return {
                    **product,

                    "database_price": product.get(
                        "price"
                    ),

                    "price": None,

                    "original_price": (
                        scraped_original_price
                    ),

                    "discount": (
                        scraped_discount
                    ),

                    "ratings": (
                        scraped_rating
                        if scraped_rating is not None
                        else product.get("ratings")
                    ),

                    "description": (
                        scraped_description
                        if scraped_description
                        else product.get("description")
                    ),

                    "image_link": (
                        scraped_image
                        if scraped_image
                        else product.get("image_link")
                    ),

                    "price_changed": False,

                    "price_difference": 0,

                    "created_at": (
                        datetime.now().isoformat()
                    ),

                    "status": "price_missing",

                    "http_status": http_status,

                    "scraped_name": scraped_name,

                    "color": scraped_color,

                }

            # ------------------------------------------------
            # BUILD FINAL RESULT
            # ------------------------------------------------

            result = make_result(

                product=product,

                scraped_price=scraped_price,

                scraped_original_price=(
                    scraped_original_price
                ),

                scraped_discount=(
                    scraped_discount
                ),

                scraped_rating=(
                    scraped_rating
                ),

                scraped_description=(
                    scraped_description
                ),

                scraped_image=(
                    scraped_image
                ),

                scraped_name=(
                    scraped_name
                ),

                http_status=http_status,

            )

            result["color"] = scraped_color

            # ------------------------------------------------
            # LOG RESULT
            # ------------------------------------------------

            logger.info(
                "AJIO SUCCESS | %s | "
                "DB Price: %s | "
                "Current Price: %s | "
                "MRP: %s | "
                "Discount: %s | "
                "Changed: %s | "
                "Difference: %s",

                product_name,

                product.get("price"),

                scraped_price,

                scraped_original_price,

                scraped_discount,

                result.get(
                    "price_changed"
                ),

                result.get(
                    "price_difference"
                ),
            )

            return result

        except PlaywrightTimeoutError as exc:

            logger.warning(
                "AJIO TIMEOUT | "
                "%s | Attempt %s/%s | %s",

                product_name,

                attempt,

                MAX_RETRIES,

                exc,
            )

        except Exception as exc:

            logger.exception(
                "AJIO ERROR | "
                "%s | Attempt %s/%s | %s",

                product_name,

                attempt,

                MAX_RETRIES,

                exc,
            )

        if attempt < MAX_RETRIES:

            await asyncio.sleep(
                attempt * 2
            )

    # ========================================================
    # ALL RETRIES FAILED
    # ========================================================

    return {

        **product,

        "database_price": product.get(
            "price"
        ),

        "price": None,

        "price_changed": False,

        "price_difference": 0,

        "created_at": (
            datetime.now().isoformat()
        ),

        "status": "failed",

        "http_status": None,

        "scraped_name": None,

    }


# ============================================================
# RUN PLAYWRIGHT SCRAPER
# ============================================================

async def run_scraper(products):

    results = []

    # --------------------------------------------------------
    # TEST LIMIT
    # --------------------------------------------------------

    test_limit = int(
        os.getenv(
            "AJIO_MAX_PRODUCTS",
            "0"
        ) or "0"
    )

    if test_limit > 0:

        products = products[:test_limit]

        logger.info(
            "AJIO TEST LIMIT enabled: %s product(s)",
            test_limit,
        )

    total = len(products)

    logger.info("=" * 80)

    logger.info(
        "AJIO PRICE TRACKING STARTED"
    )

    logger.info(
        "Existing products: %s",
        total,
    )

    logger.info("=" * 80)

    async with async_playwright() as playwright:

        headless_mode = (
            os.getenv(
                "AJIO_HEADLESS",
                "false"
            ).lower()
            == "true"
        )

        browser = await playwright.chromium.launch(

            headless=headless_mode,

            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-http2",
            ],
        )

        context = await browser.new_context(

            locale="en-IN",

            timezone_id="Asia/Kolkata",

            viewport={
                "width": 1366,
                "height": 768,
            },

            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/154.0.0.0 "
                "Safari/537.36"
            ),

            extra_http_headers={
                "Accept-Language": (
                    "en-IN,en;q=0.9,en-US;q=0.8"
                ),

                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "image/avif,image/webp,"
                    "*/*;q=0.8"
                ),

                "Upgrade-Insecure-Requests": "1",

            },
        )

        # ----------------------------------------------------
        # STEALTH SCRIPT
        # ----------------------------------------------------

        await context.add_init_script(
            """
            Object.defineProperty(
                navigator,
                'webdriver',
                {
                    get: () => undefined
                }
            );

            Object.defineProperty(
                navigator,
                'languages',
                {
                    get: () => ['en-IN', 'en', 'en-US']
                }
            );

            Object.defineProperty(
                navigator,
                'platform',
                {
                    get: () => 'Win32'
                }
            );

            window.chrome = {
                runtime: {}
            };
            """
        )

        # ----------------------------------------------------
        # BLOCK ONLY UNNECESSARY RESOURCES
        # ----------------------------------------------------

        async def route_handler(route):

            request = route.request

            resource_type = (
                request.resource_type
            )

            if resource_type in {
                "font",
                "media",
            }:

                await route.abort()

            else:

                await route.continue_()

        await context.route(
            "**/*",
            route_handler,
        )

        # ----------------------------------------------------
        # WARM UP AJIO HOMEPAGE
        # ----------------------------------------------------

        warmup_page = await context.new_page()

        try:

            logger.info(
                "AJIO warm-up: opening homepage"
            )

            warmup_response = await warmup_page.goto(
                BASE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            warmup_status = (
                warmup_response.status
                if warmup_response
                else None
            )

            logger.info(
                "AJIO homepage HTTP STATUS | %s",
                warmup_status,
            )

            await warmup_page.wait_for_timeout(
                3000
            )

        except Exception as exc:

            logger.warning(
                "AJIO homepage warm-up failed: %s",
                exc,
            )

        finally:

            await warmup_page.close()

        # ----------------------------------------------------
        # PRODUCT PAGE
        # ----------------------------------------------------

        page = await context.new_page()

        for index, product in enumerate(
            products,
            start=1,
        ):

            result = await scrape_product(

                page=page,

                product=product,

                index=index,

                total=total,

            )

            results.append(result)

            await asyncio.sleep(
                2
            )

        await page.close()

        await context.close()

        await browser.close()

    # ========================================================
    # SAVE JSON
    # ========================================================

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            results,
            file,
            ensure_ascii=False,
            indent=4,
            default=str,
        )

    logger.info("=" * 80)

    logger.info(
        "AJIO JSON SAVED"
    )

    logger.info(
        "File: %s",
        os.path.abspath(
            OUTPUT_FILE
        ),
    )

    logger.info(
        "Total results: %s",
        len(results),
    )

    logger.info("=" * 80)

    # ========================================================
    # SUMMARY
    # ========================================================

    success_count = sum(

        1

        for item in results

        if item.get("status") == "success"

    )

    failed_count = sum(

        1

        for item in results

        if item.get("status") == "failed"

    )

    missing_price_count = sum(

        1

        for item in results

        if item.get("status")
        == "price_missing"

    )

    changed_count = sum(

        1

        for item in results

        if item.get("price_changed")

    )

    logger.info(
        "AJIO SUMMARY | "
        "Total=%s | "
        "Success=%s | "
        "Failed=%s | "
        "Price Missing=%s | "
        "Price Changed=%s",

        len(results),

        success_count,

        failed_count,

        missing_price_count,

        changed_count,
    )

    return results


# ============================================================
# WINDOWS SAFE PROCESS RUNNER
# ============================================================

def run_playwright_process(products):

    if sys.platform.startswith("win"):

        try:

            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )

        except AttributeError:

            pass

    return asyncio.run(
        run_scraper(products)
    )


# ============================================================
# SCRAPY SPIDER
# ============================================================

class AjioExistingProductsSpider(Spider):

    name = "ajio_price_tracker"

    allowed_domains = [
        "ajio.com",
        "www.ajio.com",
    ]

    custom_settings = {

        "CONCURRENT_REQUESTS": 1,

        "DOWNLOAD_DELAY": 0,

        "LOG_LEVEL": "INFO",

        "ROBOTSTXT_OBEY": False,

    }

    async def start(self):

        logger.info("=" * 80)

        logger.info(
            "AJIO EXISTING PRODUCT PRICE TRACKER"
        )

        logger.info("=" * 80)

        # ----------------------------------------------------
        # LOAD EXISTING PRODUCTS
        # ----------------------------------------------------

        try:

            products = get_ajio_products()

        except Exception as exc:

            logger.exception(
                "Failed to fetch AJIO products: %s",
                exc,
            )

            return

        if not products:

            logger.warning(
                "No existing AJIO products found."
            )

            return

        logger.info(
            "AJIO products to scrape: %s",
            len(products),
        )

        # ----------------------------------------------------
        # RUN PLAYWRIGHT
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            results = await loop.run_in_executor(

                executor,

                run_playwright_process,

                products,

            )

        # ----------------------------------------------------
        # YIELD RESULTS TO SCRAPY
        # ----------------------------------------------------

        for result in results:

            yield result


# ============================================================
# STANDALONE EXECUTION
# ============================================================

def main():

    logger.info("=" * 80)

    logger.info(
        "AJIO STANDALONE PRICE TRACKER"
    )

    logger.info("=" * 80)

    try:

        products = get_ajio_products()

    except Exception as exc:

        logger.exception(
            "Could not load AJIO products: %s",
            exc,
        )

        return

    if not products:

        logger.warning(
            "No AJIO products found."
        )

        return

    run_playwright_process(
        products
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()