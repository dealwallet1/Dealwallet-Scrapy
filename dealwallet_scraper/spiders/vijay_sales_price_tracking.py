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


STORE_NAME = "Vijay Sales"

BASE_URL = "https://www.vijaysales.com"

MAX_RETRIES = 3

PAGE_TIMEOUT = 60000

INPUT_FILE = "vijaysales_existing_products.json"

OUTPUT_FILE = "vijaysales_existing_scraped_results.json"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    """
    Clean extra whitespace.
    """

    if value is None:
        return None

    value = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    return value or None


def clean_price(value):
    """
    Convert price into integer.

    Examples:

        ₹13,999       -> 13999
        13,999        -> 13999
        13999.00      -> 13999
    """

    if value is None:
        return None

    value = clean_text(value)

    if not value:
        return None

    value = value.replace(
        ",",
        "",
    )

    match = re.search(
        r"\d+(?:\.\d+)?",
        value,
    )

    if not match:
        return None

    try:

        return int(
            float(
                match.group(0)
            )
        )

    except (
        ValueError,
        TypeError,
    ):

        return None


def calculate_discount(
    price,
    original_price,
):
    """
    Calculate discount using actual prices.
    """

    if (
        price is None
        or original_price is None
    ):
        return None

    try:

        price = float(price)

        original_price = float(
            original_price
        )

        if original_price <= 0:
            return None

        if price >= original_price:
            return 0

        discount = (
            (
                original_price
                - price
            )
            / original_price
        ) * 100

        return round(
            discount
        )

    except (
        ValueError,
        TypeError,
        ZeroDivisionError,
    ):

        return None


def normalize_url(url):
    """
    Normalize stored Vijay Sales URL.
    """

    if not url:
        return None

    try:

        parsed = urlparse(
            url
        )

        if (
            not parsed.scheme
            or not parsed.netloc
        ):

            return urljoin(
                BASE_URL,
                url,
            )

        return (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        ).rstrip("?")

    except Exception:

        return url


# ============================================================
# PLAYWRIGHT TEXT HELPER
# ============================================================

async def get_first_text(
    page,
    selectors,
):
    """
    Return the first non-empty text
    from the supplied selectors.
    """

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(
                count
            ):

                try:

                    value = clean_text(
                        await locator
                        .nth(index)
                        .inner_text(
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
# PLAYWRIGHT ATTRIBUTE HELPER
# ============================================================

async def get_first_attribute(
    page,
    selectors,
    attribute,
):
    """
    Return the first non-empty
    attribute value.
    """

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(
                count
            ):

                try:

                    value = clean_text(
                        await locator
                        .nth(index)
                        .get_attribute(
                            attribute,
                            timeout=3000,
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
# CURRENT PRICE
# ============================================================

async def extract_price(page):
    """
    Vijay Sales current/final price.

    Primary selector:

        p.product__price--price[data-final-price]

    Example from page:

        data-final-price="13999"
    """

    selectors = [
        'p.product__price--price[data-final-price]',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(
                count
            ):

                element = locator.nth(
                    index
                )

                value = await element.get_attribute(
                    "data-final-price"
                )

                if value:

                    price = clean_price(
                        value
                    )

                    if price is not None:
                        return price

        except Exception:

            continue

    # Fallback to visible price
    text = await get_first_text(
        page,
        [
            "p.product__price--price",
        ],
    )

    return clean_price(
        text
    )


# ============================================================
# ORIGINAL PRICE / MRP
# ============================================================

async def extract_original_price(page):
    """
    Vijay Sales MRP.

    Primary selector:

        p.product__price--mrp span[data-mrp]

    Example:

        data-mrp="26999"
    """

    selectors = [
        'p.product__price--mrp span[data-mrp]',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(
                count
            ):

                element = locator.nth(
                    index
                )

                value = await element.get_attribute(
                    "data-mrp"
                )

                if value:

                    original_price = clean_price(
                        value
                    )

                    if (
                        original_price
                        is not None
                    ):

                        return original_price

        except Exception:

            continue

    # Fallback
    text = await get_first_text(
        page,
        [
            "p.product__price--mrp",
            "p.product__price--mrp span",
        ],
    )

    return clean_price(
        text
    )


# ============================================================
# DISCOUNT
# ============================================================

async def extract_discount(page):
    """
    Vijay Sales displayed discount.

    Selector:

        p.product__price--discount-label

    Example:

        48% off
    """

    try:

        locator = page.locator(
            "p.product__price--discount-label"
        )

        count = await locator.count()

        for index in range(
            count
        ):

            text = clean_text(
                await locator
                .nth(index)
                .inner_text(
                    timeout=3000
                )
            )

            if not text:
                continue

            match = re.search(
                r"(\d+)",
                text,
            )

            if match:

                return int(
                    match.group(1)
                )

    except Exception:

        pass

    return None


# ============================================================
# PRODUCT NAME
# ============================================================

async def extract_product_name(page):
    """
    Exact Vijay Sales product name selector.
    """

    selectors = [
        'h1.productFullDetail__productName span[role="name"]',

        "h1.productFullDetail__productName",

        "h1",
    ]

    return await get_first_text(
        page,
        selectors,
    )


# ============================================================
# RATING
# ============================================================

async def extract_rating(page):
    """
    Exact Vijay Sales rating selector.

    Primary:

        data-rating-summary

    Fallback:

        style="--rating: 4.5;"
    """

    selector = (
        "div.product__title--reviews-star.stars"
    )

    try:

        locator = page.locator(
            selector
        )

        count = await locator.count()

        for index in range(
            count
        ):

            element = locator.nth(
                index
            )

            # -----------------------------------------------
            # PRIMARY: data-rating-summary
            # -----------------------------------------------

            rating = await element.get_attribute(
                "data-rating-summary"
            )

            if rating:

                parsed = clean_price(
                    rating
                )

                if parsed is not None:
                    return float(
                        rating
                    )

            # -----------------------------------------------
            # FALLBACK: CSS VARIABLE
            # -----------------------------------------------

            style = await element.get_attribute(
                "style"
            )

            if style:

                match = re.search(
                    r"--rating:\s*([\d.]+)",
                    style,
                )

                if match:

                    return float(
                        match.group(1)
                    )

            # -----------------------------------------------
            # FALLBACK: VISIBLE TEXT
            # -----------------------------------------------

            text = await element.inner_text(
                timeout=3000
            )

            if text:

                match = re.search(
                    r"(\d+(?:\.\d+)?)",
                    text,
                )

                if match:

                    return float(
                        match.group(1)
                    )

    except Exception:

        pass

    return None


# ============================================================
# IMAGE
# ============================================================

async def extract_image(page):
    """
    Exact Vijay Sales main image selector.

    Selector:

        img.carousel__currentImage[
            data-gallery-role="currentimage"
        ]
    """

    selectors = [
        'img.carousel__currentImage[data-gallery-role="currentimage"]',

        "img.carousel__currentImage",

        'img[data-gallery-role="currentimage"]',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(
                count
            ):

                image = await locator.nth(
                    index
                ).get_attribute(
                    "src"
                )

                if not image:

                    image = await locator.nth(
                        index
                    ).get_attribute(
                        "data-src"
                    )

                if not image:
                    continue

                image = clean_text(
                    image
                )

                if (
                    not image
                    or image.startswith(
                        "data:"
                    )
                ):
                    continue

                image = urljoin(
                    BASE_URL,
                    image,
                )

                if image.startswith(
                    "http"
                ):

                    return image

        except Exception:

            continue

    return None


# ============================================================
# DESCRIPTION
# ============================================================

def extract_description(html):
    """
    Extract Vijay Sales key features.

    Exact selector:

        ul.product__keyfeatures--list li
    """

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    features = []

    for li in soup.select(
        "ul.product__keyfeatures--list li"
    ):

        text = clean_text(
            li.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            features.append(
                text
            )

    if features:

        # Remove duplicate features
        features = list(
            dict.fromkeys(
                features
            )
        )

        return ", ".join(
            features
        )

    # --------------------------------------------------------
    # Fallback: product description section
    # --------------------------------------------------------

    description_div = soup.select_one(
        "section.productFullDetail__description "
        "div.richText__root"
    )

    if description_div:

        text = clean_text(
            description_div.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            return text

    return None


# ============================================================
# SKU
# ============================================================

async def extract_sku(page):
    """
    Vijay Sales SKU selector.
    """

    selector = (
        'strong[role="sku"]'
    )

    try:

        locator = page.locator(
            selector
        )

        if await locator.count() > 0:

            value = await locator.first.inner_text(
                timeout=3000
            )

            return clean_text(
                value
            )

    except Exception:

        pass

    return None


# ============================================================
# DATABASE:
# FETCH EXISTING VIJAY SALES PRODUCTS
# ============================================================

def get_vijay_sales_products():
    """
    Fetch ONLY existing Vijay Sales products
    from PostgreSQL.

    No product discovery.
    """

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
            for key, value
            in required.items()
            if not value
        ]

        if missing:

            raise RuntimeError(
                "Missing environment variables: "
                + ", ".join(
                    missing
                )
            )

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

            WHERE LOWER(TRIM(s.name))
                = LOWER(TRIM(%s))

              AND p.product_link IS NOT NULL

              AND TRIM(p.product_link) <> ''

            ORDER BY p.id;
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

            normalized_url = normalize_url(
                product_link
            )

            products.append(
                {
                    "product_id": str(
                        product_id
                    ),

                    "name": name,

                    "price": price,

                    "original_price": (
                        original_price
                    ),

                    "currency": (
                        currency
                        or "INR"
                    ),

                    "discount": discount,

                    "ratings": ratings,

                    "description": (
                        description
                    ),

                    "image_link": (
                        image_link
                    ),

                    "product_link": (
                        product_link
                    ),

                    "organization_id": (
                        organization_name
                    ),

                    "store_id": (
                        store_name
                    ),

                    "store_name": (
                        store_name
                    ),

                    "categories_id": (
                        category_name
                    ),

                    "affiliate_url": (
                        affiliate_url
                    ),

                    "scrape_url": (
                        normalized_url
                    ),
                }
            )

        # ----------------------------------------------------
        # Save DB products before scraping
        # ----------------------------------------------------

        with open(
            INPUT_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                products,
                file,
                indent=4,
                ensure_ascii=False,
            )

        logger.info(
            "Fetched %s existing Vijay Sales products",
            len(products),
        )

        logger.info(
            "Input saved: %s",
            INPUT_FILE,
        )

        return products

    except Exception:

        logger.exception(
            "Could not fetch existing "
            "Vijay Sales products"
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
    discount=None,
    ratings=None,
    description=None,
    image_link=None,
    status="failed",
    http_status=None,
    scraped_name=None,
    sku=None,
):
    """
    Build final product result.
    """

    database_price = product.get(
        "price"
    )

    price_changed = None

    price_difference = None

    if (
        database_price is not None
        and price is not None
    ):

        try:

            old_price = int(
                float(
                    database_price
                )
            )

            new_price = int(
                float(
                    price
                )
            )

            price_changed = (
                new_price
                != old_price
            )

            price_difference = (
                new_price
                - old_price
            )

        except (
            ValueError,
            TypeError,
        ):

            pass

    # --------------------------------------------------------
    # If displayed discount is missing,
    # calculate it from actual prices.
    # --------------------------------------------------------

    if discount is None:

        discount = calculate_discount(
            price,
            original_price,
        )

    return {
        "product_id": product.get(
            "product_id"
        ),

        "name": product.get(
            "name"
        ),

        "database_price": (
            database_price
        ),

        "price": price,

        "original_price": (
            original_price
        ),

        "currency": (
            product.get(
                "currency"
            )
            or "₹"
        ),

        "discount": discount,

        "ratings": (
            ratings
            if ratings is not None
            else product.get(
                "ratings"
            )
        ),

        "description": (
            description
            if description
            else product.get(
                "description"
            )
        ),

        "image_link": (
            image_link
            if image_link
            else product.get(
                "image_link"
            )
        ),

        "product_link": (
            product.get(
                "product_link"
            )
        ),

        "scrape_url": url,

        "organization_id": (
            product.get(
                "organization_id"
            )
        ),

        "store_id": (
            product.get(
                "store_id"
            )
        ),

        "store_name": (
            product.get(
                "store_name"
            )
        ),

        "categories_id": (
            product.get(
                "categories_id"
            )
        ),

        "affiliate_url": (
            product.get(
                "affiliate_url"
            )
        ),

        "price_changed": (
            price_changed
        ),

        "price_difference": (
            price_difference
        ),

        "created_at": (
            datetime.now().isoformat()
        ),

        "status": status,

        "http_status": (
            http_status
        ),

        "scraped_name": (
            scraped_name
        ),

        "sku": sku,
    }


# ============================================================
# SCRAPE ONE EXISTING PRODUCT
# ============================================================

async def scrape_product(
    context,
    product,
    index,
    total,
):
    """
    Scrape one existing Vijay Sales
    product using its stored URL.
    """

    original_url = product.get(
        "product_link"
    )

    url = (
        product.get(
            "scrape_url"
        )
        or normalize_url(
            original_url
        )
    )

    logger.info(
        "PRODUCT %s/%s | id=%s | %s",
        index,
        total,
        product.get(
            "product_id"
        ),
        url,
    )

    if not url:

        return make_result(
            product,
            url,
            status="url_missing",
        )

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        page = None

        try:

            page = await context.new_page()

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            status_code = (
                response.status
                if response
                else None
            )

            # ------------------------------------------------
            # HTTP STATUS
            # ------------------------------------------------

            if (
                status_code
                and status_code != 200
            ):

                logger.warning(
                    "HTTP %s | attempt %s/%s | %s",
                    status_code,
                    attempt,
                    MAX_RETRIES,
                    url,
                )

                if (
                    attempt
                    < MAX_RETRIES
                ):

                    await asyncio.sleep(
                        attempt * 2
                    )

                    continue

                return make_result(
                    product,
                    url,
                    status="non_200",
                    http_status=status_code,
                )

            # ------------------------------------------------
            # WAIT FOR PRODUCT CONTENT
            # ------------------------------------------------

            try:

                await page.locator(
                    "form.productFullDetail__root"
                ).wait_for(
                    state="attached",
                    timeout=15000,
                )

            except Exception:

                # Page may still contain usable data
                await page.wait_for_timeout(
                    2500
                )

            # ------------------------------------------------
            # CURRENT PRICE
            # ------------------------------------------------

            price = await extract_price(
                page
            )

            # ------------------------------------------------
            # ORIGINAL PRICE
            # ------------------------------------------------

            original_price = (
                await extract_original_price(
                    page
                )
            )

            # ------------------------------------------------
            # DISCOUNT
            # ------------------------------------------------

            discount = (
                await extract_discount(
                    page
                )
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
            # RATING
            # ------------------------------------------------

            ratings = (
                await extract_rating(
                    page
                )
            )

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            image_link = (
                await extract_image(
                    page
                )
            )

            # ------------------------------------------------
            # SKU
            # ------------------------------------------------

            sku = await extract_sku(
                page
            )

            # ------------------------------------------------
            # DESCRIPTION
            # ------------------------------------------------

            html = await page.content()

            description = (
                extract_description(
                    html
                )
            )

            # ------------------------------------------------
            # VALIDATE DISCOUNT
            # ------------------------------------------------

            calculated_discount = (
                calculate_discount(
                    price,
                    original_price,
                )
            )

            if calculated_discount is not None:

                if (
                    discount is None
                    or discount
                    != calculated_discount
                ):

                    logger.info(
                        "Discount validation | "
                        "Displayed=%s%% | "
                        "Calculated=%s%%",
                        discount,
                        calculated_discount,
                    )

                    # Use calculated discount
                    # as the standardized value.
                    discount = (
                        calculated_discount
                    )

            # ------------------------------------------------
            # LOG SCRAPED DATA
            # ------------------------------------------------

            logger.info(
                "Name=%s",
                scraped_name,
            )

            logger.info(
                "Price=%s | Original=%s | Discount=%s%%",
                price,
                original_price,
                discount,
            )

            logger.info(
                "Rating=%s | SKU=%s",
                ratings,
                sku,
            )

            # ------------------------------------------------
            # PRICE REQUIRED
            # ------------------------------------------------

            if price is None:

                logger.warning(
                    "Price not found | "
                    "attempt=%s/%s | %s",
                    attempt,
                    MAX_RETRIES,
                    url,
                )

                if (
                    attempt
                    < MAX_RETRIES
                ):

                    await asyncio.sleep(
                        attempt * 2
                    )

                    continue

                return make_result(
                    product,
                    url,
                    original_price=(
                        original_price
                    ),
                    discount=discount,
                    ratings=ratings,
                    description=description,
                    image_link=image_link,
                    status="price_missing",
                    http_status=status_code,
                    scraped_name=scraped_name,
                    sku=sku,
                )

            # ------------------------------------------------
            # FINAL RESULT
            # ------------------------------------------------

            result = make_result(
                product,
                url,
                price=price,
                original_price=(
                    original_price
                ),
                discount=discount,
                ratings=ratings,
                description=description,
                image_link=image_link,
                status="success",
                http_status=status_code,
                scraped_name=scraped_name,
                sku=sku,
            )

            logger.info(
                "DB Price=%s | "
                "Scraped Price=%s | "
                "Changed=%s | "
                "Difference=%s",
                result[
                    "database_price"
                ],
                result[
                    "price"
                ],
                result[
                    "price_changed"
                ],
                result[
                    "price_difference"
                ],
            )

            return result

        except PlaywrightTimeoutError as exc:

            logger.warning(
                "Timeout | attempt %s/%s | %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

        except Exception as exc:

            logger.exception(
                "Scrape attempt %s/%s "
                "failed for %s: %s",
                attempt,
                MAX_RETRIES,
                url,
                exc,
            )

        finally:

            if page:

                try:

                    await page.close()

                except Exception:

                    pass

        if (
            attempt
            < MAX_RETRIES
        ):

            await asyncio.sleep(
                attempt * 2
            )

    return make_result(
        product,
        url,
        status="failed",
    )


# ============================================================
# PLAYWRIGHT SCRAPER
# ============================================================

async def run_scraper(
    products
):
    """
    Scrape all existing Vijay Sales
    products sequentially.
    """

    results = []

    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            viewport={
                "width": 1366,
                "height": 768,
            },
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        try:

            for index, product in enumerate(
                products,
                start=1,
            ):

                result = (
                    await scrape_product(
                        context,
                        product,
                        index,
                        len(products),
                    )
                )

                results.append(
                    result
                )

                await asyncio.sleep(
                    1
                )

        finally:

            await context.close()

            await browser.close()

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            results,
            file,
            indent=4,
            ensure_ascii=False,
        )

    # ========================================================
    # STATISTICS
    # ========================================================

    success = sum(
        r.get("status")
        == "success"
        for r in results
    )

    price_missing = sum(
        r.get("status")
        == "price_missing"
        for r in results
    )

    failed = sum(
        r.get("status")
        in (
            "failed",
            "non_200",
        )
        for r in results
    )

    changed = sum(
        r.get(
            "price_changed"
        )
        is True
        for r in results
        if r.get("status")
        == "success"
    )

    unchanged = sum(
        r.get(
            "price_changed"
        )
        is False
        for r in results
        if r.get("status")
        == "success"
    )

    logger.info(
        "=" * 70
    )

    logger.info(
        "VIJAY SALES SCRAPING FINISHED"
    )

    logger.info(
        "Total           : %s",
        len(results),
    )

    logger.info(
        "Success         : %s",
        success,
    )

    logger.info(
        "Price missing   : %s",
        price_missing,
    )

    logger.info(
        "Failed          : %s",
        failed,
    )

    logger.info(
        "Price changed   : %s",
        changed,
    )

    logger.info(
        "Price unchanged : %s",
        unchanged,
    )

    logger.info(
        "Output          : %s",
        OUTPUT_FILE,
    )

    logger.info(
        "=" * 70
    )

    return results


# ============================================================
# WINDOWS-SAFE PLAYWRIGHT PROCESS
# ============================================================

def run_playwright_process(
    products
):
    """
    Run Playwright inside a separate process.
    """

    if sys.platform == "win32":

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return {
        "success": True,

        "results": asyncio.run(
            run_scraper(
                products
            )
        ),

        "output_file": (
            OUTPUT_FILE
        ),
    }


# ============================================================
# SCRAPY SPIDER
# ============================================================

class VijaySalesExistingProductsSpider(
    Spider
):

    name = (
        "vijay_sales_price_tracking"
    )

    allowed_domains = [
        "vijaysales.com",
        "www.vijaysales.com",
    ]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
    }

    async def start(self):

        self.logger.info(
            "=" * 70
        )

        self.logger.info(
            "VIJAY SALES EXISTING PRODUCTS "
            "PRICE TRACKER"
        )

        self.logger.info(
            "NO PRODUCT DISCOVERY"
        )

        self.logger.info(
            "=" * 70
        )

        # ====================================================
        # FETCH EXISTING PRODUCTS
        # ====================================================

        products = (
            get_vijay_sales_products()
        )

        if not products:

            self.logger.warning(
                "No existing Vijay Sales "
                "products found."
            )

            return

        self.logger.info(
            "Existing products loaded: %s",
            len(products),
        )

        # ====================================================
        # RUN PLAYWRIGHT
        # ====================================================

        loop = (
            asyncio.get_running_loop()
        )

        try:

            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:

                result = (
                    await loop.run_in_executor(
                        executor,
                        run_playwright_process,
                        products,
                    )
                )

        except Exception as exc:

            self.logger.error(
                "Playwright process failed: %s",
                exc,
                exc_info=True,
            )

            return

        # ====================================================
        # PROCESS RESULT
        # ====================================================

        if (
            not result
            or not result.get(
                "success"
            )
        ):

            self.logger.error(
                "Existing Vijay Sales "
                "scraping failed."
            )

            return

        scraped = (
            result.get(
                "results"
            )
            or []
        )

        # ====================================================
        # SEND TO SCRAPY PIPELINE
        # ====================================================

        sent = 0

        skipped = 0

        for product in scraped:

            if (
                not isinstance(
                    product,
                    dict,
                )
                or product.get(
                    "status"
                )
                != "success"
                or product.get(
                    "price"
                )
                is None
            ):

                skipped += 1

                continue

            sent += 1

            yield product

        # ====================================================
        # PRICE STATISTICS
        # ====================================================

        changed = sum(
            1
            for product in scraped
            if (
                product.get(
                    "status"
                )
                == "success"
                and product.get(
                    "price_changed"
                )
                is True
            )
        )

        unchanged = sum(
            1
            for product in scraped
            if (
                product.get(
                    "status"
                )
                == "success"
                and product.get(
                    "price_changed"
                )
                is False
            )
        )

        # ====================================================
        # SUMMARY
        # ====================================================

        self.logger.info(
            "=" * 70
        )

        self.logger.info(
            "VIJAY SALES SCRAPER COMPLETED"
        )

        self.logger.info(
            "Existing products : %s",
            len(products),
        )

        self.logger.info(
            "Scraped products   : %s",
            len(scraped),
        )

        self.logger.info(
            "Sent to pipeline   : %s",
            sent,
        )

        self.logger.info(
            "Skipped            : %s",
            skipped,
        )

        self.logger.info(
            "Price changed      : %s",
            changed,
        )

        self.logger.info(
            "Price unchanged    : %s",
            unchanged,
        )

        self.logger.info(
            "Output JSON        : %s",
            OUTPUT_FILE,
        )

        self.logger.info(
            "=" * 70
        )


# ============================================================
# STANDALONE EXECUTION
# ============================================================

if __name__ == "__main__":

    products = (
        get_vijay_sales_products()
    )

    if not products:

        raise SystemExit(
            "No existing Vijay Sales "
            "products found."
        )

    result = (
        run_playwright_process(
            products
        )
    )

    print(
        "Results:",
        result.get(
            "output_file"
        ),
    )

    print(
        "Standalone mode does not run "
        "Scrapy item pipelines."
    )