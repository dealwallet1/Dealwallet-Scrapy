import os
import json
import re
import sys
import asyncio
import psycopg2

from concurrent.futures import ProcessPoolExecutor

from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv
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

STORE_NAME = "Flipkart"

# Browser concurrency
CONCURRENT_REQUESTS = 50

# Number of retries for failed/blocked pages
MAX_RETRIES = 3

# Page timeout
PAGE_TIMEOUT = 45000

INPUT_FILE = "flipkart_products.json"
OUTPUT_FILE = "flipkart_scraped_results.json"

ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Flipkart"


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip()

    return value if value else None


def clean_price(value):
    if value is None:
        return None

    value = clean_text(value)

    if not value:
        return None

    value = value.replace("₹", "")
    value = value.replace("Rs.", "")
    value = value.replace("Rs", "")
    value = value.replace("INR", "")
    value = value.strip()

    # Remove currency symbols, commas, decimals, and other
    # non-numeric characters so the result is an integer.
    value = re.sub(r"[^\d]", "", value)

    if not value:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def clean_discount(value):
    if value is None:
        return None

    value = clean_text(value)

    if not value:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        value
    )

    if match:
        try:
            return int(float(match.group(1)))
        except (ValueError, TypeError):
            return None

    # Fallback if the % sign is not present.
    match = re.search(
        r"\d+(?:\.\d+)?",
        value
    )

    if match:
        try:
            return int(float(match.group(0)))
        except (ValueError, TypeError):
            return None

    return None


def clean_rating(value):
    if value is None:
        return None

    value = clean_text(value)

    if not value:
        return None

    match = re.search(
        r"\b([0-5](?:\.\d+)?)\b",
        value
    )

    if match:
        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            return None

    return None


# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_flipkart_url(url):
    """
    Remove search/tracking query parameters from the stored
    Flipkart URL.

    Example:

    Original:
    https://www.flipkart.com/product/p/itm123?
    pid=ABC&lid=XYZ&q=test&otracker=search...

    Result:
    https://www.flipkart.com/product/p/itm123
    """

    if not url:
        return None

    try:
        parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return url

        clean_url = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        )

        return clean_url.rstrip("?")

    except Exception:
        return url


# ============================================================
# DATABASE
# ============================================================

def get_flipkart_product_count(connection):
    cursor = None

    try:
        cursor = connection.cursor()

        query = """
            SELECT COUNT(*)
            FROM public.products AS p
            JOIN public.stores AS s
                ON p.store_id = s.id
            WHERE LOWER(TRIM(s.name))
                  = LOWER(TRIM(%s))
              AND p.product_link IS NOT NULL
              AND p.product_link <> '';
        """

        cursor.execute(
            query,
            (STORE_NAME,)
        )

        count = cursor.fetchone()[0]

        return count

    except Exception as error:

        print("\nCount query failed:")
        print(error)

        return 0

    finally:

        if cursor:
            cursor.close()


def get_flipkart_products():

    connection = None
    cursor = None

    try:

        print("\n" + "=" * 80)
        print("CONNECTING TO POSTGRESQL")
        print("=" * 80)

        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        print("PostgreSQL connection successful.")

        # ====================================================
        # COUNT
        # ====================================================

        total_count = get_flipkart_product_count(
            connection
        )

        print("\n" + "=" * 80)
        print("FLIPKART PRODUCT COUNT")
        print("=" * 80)

        print(
            f"Store             : {STORE_NAME}"
        )

        print(
            f"Eligible products : {total_count}"
        )

        print("=" * 80)

        if total_count == 0:

            print(
                "\nNo Flipkart products found."
            )

            return []

        # ====================================================
        # FETCH ALL PRODUCTS
        # ====================================================

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
            FROM public.products AS p
            JOIN public.stores AS s
                ON p.store_id = s.id
            LEFT JOIN public.organization AS o
                ON p.organization_id = o.id
            LEFT JOIN public.categories AS c
                ON p.categories_id = c.id
            WHERE LOWER(TRIM(s.name))
                  = LOWER(TRIM(%s))
              AND p.product_link IS NOT NULL
              AND p.product_link <> '';
        """

        print(
            "\nFetching ALL Flipkart products..."
        )

        cursor.execute(
            query,
            (STORE_NAME,)
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
                affiliate_url
            ) = row

            normalized_url = normalize_flipkart_url(
                product_link
            )

            products.append({

                "product_id":
                    str(product_id),

                "name":
                    str(name)
                    if name is not None
                    else None,

                "price":
                    price,

                "original_price":
                    original_price,

                "currency":
                    currency,

                "discount":
                    discount,

                "ratings":
                    ratings,

                "description":
                    description,

                "image_link":
                    image_link,

                "product_link":
                    product_link,

                "organization_id":
                    organization_name,

                "store_id":
                    store_name,

                "store_name":
                    store_name,

                "categories_id":
                    category_name,

                "affiliate_url":
                    affiliate_url,

                "scrape_url":
                    normalized_url
            })

        # ====================================================
        # SAVE INPUT JSON
        # ====================================================

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

        print("\n" + "=" * 80)
        print("DATABASE READ COMPLETED")
        print("=" * 80)

        print(
            f"Expected products : {total_count}"
        )

        print(
            f"Fetched products  : {len(products)}"
        )

        print(
            f"Input JSON        : {INPUT_FILE}"
        )

        print("=" * 80)

        if len(products) != total_count:

            print(
                "\nWARNING:"
            )

            print(
                f"Expected {total_count}, "
                f"but fetched {len(products)}."
            )

        return products

    except Exception as error:

        print("\n" + "=" * 80)
        print("DATABASE ERROR")
        print("=" * 80)

        print(error)

        print("=" * 80)

        return []

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()

        print(
            "\nPostgreSQL connection closed."
        )


# ============================================================
# PLAYWRIGHT SELECTOR HELPERS
# ============================================================

async def get_first_text(page, selectors):

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(count):

                try:

                    value = await locator.nth(
                        index
                    ).inner_text(
                        timeout=3000
                    )

                    value = clean_text(
                        value
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
    attribute
):

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            count = await locator.count()

            for index in range(count):

                try:

                    value = await locator.nth(
                        index
                    ).get_attribute(
                        attribute,
                        timeout=3000
                    )

                    value = clean_text(
                        value
                    )

                    if value:
                        return value

                except Exception:
                    continue

        except Exception:
            continue

    return None


# ============================================================
# IMAGE FUNCTIONS
# ============================================================

def is_valid_flipkart_product_image(url):

    if not url:
        return False

    url = url.lower()

    if "rukminim2.flixcart.com" not in url:
        return False

    if "/www/" in url:
        return False

    if "batman-returns" in url:
        return False

    if ".svg" in url:
        return False

    return True


def extract_image_from_srcset(srcset):

    if not srcset:
        return None

    candidates = []

    parts = srcset.split(",")

    for part in parts:

        part = part.strip()

        if not part:
            continue

        match = re.match(
            r"(.+?)\s+\d+(?:\.\d+)?x$",
            part
        )

        if match:

            url = match.group(1).strip()

        else:

            url = part.split()[0]

        if is_valid_flipkart_product_image(
            url
        ):

            candidates.append(url)

    if not candidates:
        return None

    return candidates[-1]


async def extract_product_image(page):

    # ========================================================
    # 1. PICTURE SOURCE
    # ========================================================

    try:

        pictures = page.locator(
            "picture"
        )

        picture_count = await pictures.count()

        for picture_index in range(
            picture_count
        ):

            picture = pictures.nth(
                picture_index
            )

            sources = picture.locator(
                "source"
            )

            source_count = await sources.count()

            for source_index in range(
                source_count
            ):

                srcset = await sources.nth(
                    source_index
                ).get_attribute(
                    "srcset"
                )

                image = extract_image_from_srcset(
                    srcset
                )

                if image:
                    return image

            imgs = picture.locator(
                "img"
            )

            img_count = await imgs.count()

            for img_index in range(
                img_count
            ):

                img = imgs.nth(
                    img_index
                )

                srcset = await img.get_attribute(
                    "srcset"
                )

                image = extract_image_from_srcset(
                    srcset
                )

                if image:
                    return image

                src = await img.get_attribute(
                    "src"
                )

                if is_valid_flipkart_product_image(
                    src
                ):

                    return src

    except Exception:
        pass

    # ========================================================
    # 2. ALL IMAGE SRCSET
    # ========================================================

    try:

        imgs = page.locator(
            "img"
        )

        count = await imgs.count()

        for index in range(count):

            img = imgs.nth(index)

            srcset = await img.get_attribute(
                "srcset"
            )

            image = extract_image_from_srcset(
                srcset
            )

            if image:
                return image

    except Exception:
        pass

    # ========================================================
    # 3. IMAGE SRC
    # ========================================================

    try:

        imgs = page.locator(
            "img"
        )

        count = await imgs.count()

        for index in range(count):

            src = await imgs.nth(
                index
            ).get_attribute(
                "src"
            )

            if is_valid_flipkart_product_image(
                src
            ):

                return src

    except Exception:
        pass

    # ========================================================
    # 4. DATA-SRC
    # ========================================================

    try:

        imgs = page.locator(
            "img"
        )

        count = await imgs.count()

        for index in range(count):

            src = await imgs.nth(
                index
            ).get_attribute(
                "data-src"
            )

            if is_valid_flipkart_product_image(
                src
            ):

                return src

    except Exception:
        pass

    return None


# ============================================================
# JSON-LD
# ============================================================

async def get_json_ld(page):

    try:

        scripts = page.locator(
            'script[type="application/ld+json"]'
        )

        count = await scripts.count()

        for index in range(count):

            try:

                script_text = await scripts.nth(
                    index
                ).text_content()

                if not script_text:
                    continue

                data = json.loads(
                    script_text
                )

                # Direct object

                if isinstance(
                    data,
                    dict
                ):

                    data_type = data.get(
                        "@type"
                    )

                    if data_type == "Product":

                        return data

                    # @graph

                    graph = data.get(
                        "@graph"
                    )

                    if isinstance(
                        graph,
                        list
                    ):

                        for item in graph:

                            if not isinstance(
                                item,
                                dict
                            ):
                                continue

                            if item.get(
                                "@type"
                            ) == "Product":

                                return item

                # List

                elif isinstance(
                    data,
                    list
                ):

                    for item in data:

                        if not isinstance(
                            item,
                            dict
                        ):
                            continue

                        if item.get(
                            "@type"
                        ) == "Product":

                            return item

            except Exception:
                continue

    except Exception:
        pass

    return {}


# ============================================================
# SCRAPE ONE PRODUCT
# ============================================================

async def scrape_product(context, product, index, total):
    """Scrape only fields that can change for price tracking."""
    original_url = product.get("product_link")
    scrape_url = product.get("scrape_url") or normalize_flipkart_url(original_url)

    print("\\n" + "=" * 80)
    print(f"PRODUCT {index}/{total}")
    print(f"Product ID      : {product.get('product_id')}")
    print(f"Name            : {product.get('name')}")
    print(f"Database Price  : {product.get('price')}")
    print(f"URL             : {scrape_url}")
    print("=" * 80)

    page = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            page = await context.new_page()
            response = await page.goto(scrape_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            status_code = response.status if response else None
            print(f"Attempt {attempt}/{MAX_RETRIES} HTTP Status: {status_code}")

            if status_code == 403:
                print("403 received from Flipkart.")
                if attempt < MAX_RETRIES:
                    await page.close(); page = None
                    await asyncio.sleep(2 * attempt)
                    continue
                return tracking_result(product, scrape_url, original_url, None, None, None, None, "403", 403)

            if status_code and status_code != 200:
                print(f"Non-200 response: {status_code}")
                if attempt < MAX_RETRIES:
                    await page.close(); page = None
                    await asyncio.sleep(2 * attempt)
                    continue
                return tracking_result(product, scrape_url, original_url, None, None, None, None, "non_200", status_code)

            try:
                await page.wait_for_selector("h1", timeout=10000)
            except PlaywrightTimeoutError:
                print("h1 not found immediately.")

            await page.wait_for_timeout(1500)
            json_ld_data = await get_json_ld(page)

            # CURRENT PRICE
            price = await get_first_text(page, [
                "div.v1zwn21n.v1zwn20",
                "div.v1zwn21n.v1zwn20._1psv1ze0",
                "div.Nx9bqj",
                "div.Nx9bqj.CxhGGd",
                "div._30jeq3",
                "div._16Jk6d",
                "div.CEmiEU",
                "div.hl05eU"
            ])
            price = clean_price(price)

            offers = json_ld_data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                offers = {}
            if price is None:
                price = clean_price(offers.get("price"))

            # ORIGINAL PRICE
            original_price = await get_first_text(page, [
                "div.v1zwn21o.v1zwn21",
                "div.v1zwn21o.v1zwn21._1psv1zeif",
                "div.yRaY8j",
                "div._3I9_wc",
                "div._3auQ3N",
                "div._25b18c"
            ])
            original_price = clean_price(original_price)

            # DISCOUNT
            discount = await get_first_text(page, [
                "div.v1zwn222.v1zwn20",
                "div.v1zwn222",
                "div.UkRMB",
                "div._3Ay6Sb",
                "span._3Ay6Sb",
                "div._2Tpdn3",
                "div._3G2wX"
            ])
            discount = clean_discount(discount)

            # RATING
            rating = None
            try:
                elements = page.locator("div.css-146c3p1")
                for i in range(await elements.count()):
                    value = clean_text(await elements.nth(i).inner_text())
                    if not value:
                        continue
                    match = re.fullmatch(r"([0-5](?:\.\d+)?)", value)
                    if match:
                        numeric = float(match.group(1))
                        if 0 <= numeric <= 5:
                            rating = numeric
                            break
            except Exception:
                pass

            if rating is None:
                rating = await get_first_text(page, [
                    "div.XQDdHH",
                    "div._3LWZlK",
                    "span._1lRcqv",
                    "div._3LWZlK._1BLPMq"
                ])
                rating = clean_rating(rating)

            database_price = product.get("price")
            price_changed = None
            price_difference = None
            if database_price is not None and price is not None:
                try:
                    db_price_number = int(float(database_price))
                    price_changed = price != db_price_number
                    price_difference = price - db_price_number
                except (ValueError, TypeError):
                    pass

            result = tracking_result(
                product, scrape_url, original_url, price,
                original_price, discount, rating, "success", status_code
            )
            result["price_changed"] = price_changed
            result["price_difference"] = price_difference

            print("\\n" + "=" * 80)
            print("PRICE TRACKING RESULT")
            print("=" * 80)
            print(f"Progress          : {index}/{total}")
            print(f"Product ID        : {result['product_id']}")
            print(f"Database Price    : {result['database_price']}")
            print(f"Current Price     : {result['price']}")
            print(f"Original Price    : {result['original_price']}")
            print(f"Discount          : {result['discount']}")
            print(f"Rating            : {result['ratings']}")
            print(f"Price Changed     : {result['price_changed']}")
            print(f"Price Difference  : {result['price_difference']}")
            print(f"Status            : {result['status']}")
            print("=" * 80)
            return result

        except PlaywrightTimeoutError as error:
            print(f"Timeout on attempt {attempt}/{MAX_RETRIES}: {error}")
        except Exception as error:
            print(f"Error on attempt {attempt}/{MAX_RETRIES}: {error}")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
                page = None

        if attempt < MAX_RETRIES:
            await asyncio.sleep(2 * attempt)

    return tracking_result(product, scrape_url, original_url, None, None, None, None, "failed", None)


def tracking_result(product, scrape_url, original_url, price, original_price, discount, ratings, status, http_status):
    """Return all existing DB fields plus the newly scraped tracking fields."""
    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "database_price": product.get("price"),
        "price": price,
        "original_price": original_price if original_price is not None else product.get("original_price"),
        "currency": product.get("currency"),
        "discount": discount if discount is not None else product.get("discount"),
        "ratings": ratings if ratings is not None else product.get("ratings"),
        "description": product.get("description"),
        "image_link": product.get("image_link"),
        "product_link": original_url,
        "scrape_url": scrape_url,
        "organization_id": product.get("organization_id"),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name"),
        "categories_id": product.get("categories_id"),
        "affiliate_url": product.get("affiliate_url"),
        "created_at": datetime.now().isoformat(),
        "status": status,
        "http_status": http_status
    }


# ============================================================
# WORKER
# ============================================================

async def worker(
    worker_id,
    context,
    queue,
    results,
    total
):

    while True:

        item = await queue.get()

        if item is None:

            queue.task_done()

            break

        index, product = item

        try:

            result = await scrape_product(
                context=context,
                product=product,
                index=index,
                total=total
            )

            results.append(
                result
            )

        except Exception as error:

            print(
                f"\nWorker {worker_id} "
                f"failed: {error}"
            )

            failed_result = tracking_result(
                product,
                product.get("scrape_url"),
                product.get("product_link"),
                None, None, None, None,
                "failed", None
            )
            failed_result["error"] = str(error)
            results.append(failed_result)

        finally:

            queue.task_done()


# ============================================================
# PLAYWRIGHT SCRAPER
# ============================================================

async def run_scraper(products):

    total = len(products)

    print("\n" + "=" * 80)
    print("STARTING PLAYWRIGHT")
    print("=" * 80)

    print(
        f"Total products : {total}"
    )

    print(
        f"Concurrency    : "
        f"{CONCURRENT_REQUESTS}"
    )

    print("=" * 80)

    results = []

    queue = asyncio.Queue()

    # ========================================================
    # ADD PRODUCTS TO QUEUE
    # ========================================================

    for index, product in enumerate(
        products,
        start=1
    ):

        await queue.put(
            (
                index,
                product
            )
        )

    async with async_playwright() as playwright:

        # ====================================================
        # LAUNCH CHROMIUM
        # ====================================================

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox"
            ]
        )

        # ====================================================
        # BROWSER CONTEXT
        # ====================================================

        context = await browser.new_context(

            viewport={
                "width": 1366,
                "height": 768
            },

            screen={
                "width": 1366,
                "height": 768
            },

            locale="en-IN",

            timezone_id="Asia/Kolkata",

            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/153.0.0.0 "
                "Safari/537.36"
            ),

            extra_http_headers={

                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "image/avif,image/webp,"
                    "*/*;q=0.8"
                ),

                "Accept-Language":
                    "en-IN,en;q=0.9",

                "Upgrade-Insecure-Requests":
                    "1"
            }
        )

        # ====================================================
        # HIDE BASIC AUTOMATION FLAG
        # ====================================================

        await context.add_init_script(
            """
            Object.defineProperty(
                navigator,
                'webdriver',
                {
                    get: () => undefined
                }
            );
            """
        )

        # ====================================================
        # START WORKERS
        # ====================================================

        workers = []

        worker_count = min(
            CONCURRENT_REQUESTS,
            total
        )

        for worker_id in range(
            worker_count
        ):

            task = asyncio.create_task(
                worker(
                    worker_id=worker_id + 1,
                    context=context,
                    queue=queue,
                    results=results,
                    total=total
                )
            )

            workers.append(task)

        # ====================================================
        # WAIT UNTIL ALL PRODUCTS COMPLETE
        # ====================================================

        await queue.join()

        # ====================================================
        # STOP WORKERS
        # ====================================================

        for _ in workers:

            await queue.put(
                None
            )

        await asyncio.gather(
            *workers
        )

        # ====================================================
        # CLOSE BROWSER
        # ====================================================

        await context.close()

        await browser.close()

    # ========================================================
    # SORT RESULTS
    # ========================================================

    product_order = {
        product.get("product_id"): index
        for index, product
        in enumerate(products)
    }

    results.sort(
        key=lambda item:
            product_order.get(
                item.get("product_id"),
                999999999
            )
    )

    # ========================================================
    # SAVE RESULTS
    # ========================================================

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

    # ========================================================
    # STATISTICS
    # ========================================================

    success_count = sum(
        1
        for item in results
        if item.get("status") == "success"
    )

    blocked_count = sum(
        1
        for item in results
        if item.get("status") == "403"
    )

    failed_count = sum(
        1
        for item in results
        if item.get("status") == "failed"
    )

    non_200_count = sum(
        1
        for item in results
        if item.get("status") == "non_200"
    )

    print("\n" + "=" * 80)
    print("SCRAPING COMPLETED")
    print("=" * 80)

    print(
        f"Total products : "
        f"{total}"
    )

    print(
        f"Success        : "
        f"{success_count}"
    )

    print(
        f"403 blocked    : "
        f"{blocked_count}"
    )

    print(
        f"Failed         : "
        f"{failed_count}"
    )

    print(
        f"Other non-200  : "
        f"{non_200_count}"
    )

    print(
        f"Results JSON   : "
        f"{OUTPUT_FILE}"
    )

    print("=" * 80)

    # Return scraped results to the parent Scrapy process so the
    # normal DealwalletScraperPipeline can process them.
    return results


# ============================================================
# WINDOWS-SAFE PLAYWRIGHT PROCESS
# ============================================================

def run_playwright_process(products):
    """
    Run Playwright in a separate process.

    Scrapy on Windows uses AsyncioSelectorReactor, which runs on
    WindowsSelectorEventLoop. Playwright needs Windows subprocess
    support, so Playwright is isolated in a child process and the
    Proactor event-loop policy is applied only there.
    """

    print("\n" + "=" * 80)
    print("PLAYWRIGHT CHILD PROCESS STARTED")
    print("=" * 80)

    print(f"Products     : {len(products)}")
    print(f"Concurrency  : {CONCURRENT_REQUESTS}")
    print(f"Python       : {sys.version.split()[0]}")
    print(f"Platform     : {sys.platform}")

    if sys.platform == "win32":
        print("Event loop   : WindowsProactorEventLoopPolicy")

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )
    else:
        print("Event loop   : platform default")

    print("=" * 80)

    try:
        results = asyncio.run(run_scraper(products))

        print("\n" + "=" * 80)
        print("PLAYWRIGHT CHILD PROCESS FINISHED")
        print("=" * 80)
        print(f"Results JSON : {OUTPUT_FILE}")
        print(f"Results      : {len(results)}")
        print("=" * 80)

        return {
            "success": True,
            "total": len(products),
            "output_file": OUTPUT_FILE,
            "results": results
        }

    except Exception as error:
        print("\n" + "=" * 80)
        print("PLAYWRIGHT CHILD PROCESS ERROR")
        print("=" * 80)
        print(f"Error : {error}")
        print("=" * 80)

        return {
            "success": False,
            "total": len(products),
            "output_file": OUTPUT_FILE,
            "error": str(error)
        }


# ============================================================
# SCRAPY SPIDER
# ============================================================

class FlipkartPriceSpider(Spider):

    name = "flipkart_price"

    allowed_domains = [
        "flipkart.com"
    ]

    custom_settings = {
        # Scrapy is used as the job/scheduler entry point.
        # Playwright itself uses CONCURRENT_REQUESTS = 50.
        "CONCURRENT_REQUESTS": 1,

        # IMPORTANT:
        # Do not disable ITEM_PIPELINES here.
        # The project's DealwalletScraperPipeline must receive
        # each successfully scraped Flipkart product and call
        # send_to_database(product) -> price_history.py.
    }

    async def start(self):

        self.logger.info("=" * 80)
        self.logger.info(
            "FLIPKART EXISTING PRODUCT PRICE TRACKER"
        )
        self.logger.info("=" * 80)

        self.logger.info(
            "Store       : %s",
            STORE_NAME
        )

        self.logger.info(
            "Mode        : EXISTING PRODUCTS ONLY"
        )

        self.logger.info(
            "Concurrency : %s",
            CONCURRENT_REQUESTS
        )

        self.logger.info(
            "Browser     : Playwright Chromium"
        )

        self.logger.info(
            "Input JSON  : %s",
            INPUT_FILE
        )

        self.logger.info(
            "Output JSON : %s",
            OUTPUT_FILE
        )

        self.logger.info("=" * 80)

        # ====================================================
        # LOAD PRODUCTS FROM POSTGRESQL
        # ====================================================

        self.logger.info(
            "Connecting to PostgreSQL..."
        )

        products = get_flipkart_products()

        if not products:

            self.logger.warning(
                "No Flipkart products found."
            )

            if False:
                yield None

            return

        self.logger.info("=" * 80)
        self.logger.info(
            "POSTGRESQL READ COMPLETED"
        )
        self.logger.info("=" * 80)

        self.logger.info(
            "Products fetched : %s",
            len(products)
        )

        self.logger.info(
            "Playwright workers: %s",
            CONCURRENT_REQUESTS
        )

        self.logger.info("=" * 80)

        # ====================================================
        # RUN PLAYWRIGHT IN SEPARATE PROCESS
        # ====================================================

        self.logger.info(
            "Starting Playwright in a separate process..."
        )

        loop = asyncio.get_running_loop()

        try:

            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:

                result = await loop.run_in_executor(
                    executor,
                    run_playwright_process,
                    products
                )

        except Exception as error:

            self.logger.error(
                "Playwright process failed: %s",
                error,
                exc_info=True
            )

            if False:
                yield None

            return

        # ====================================================
        # SEND RESULTS THROUGH NORMAL SCRAPY PIPELINE
        # ====================================================

        if not result or not result.get("success"):
            self.logger.error("=" * 80)
            self.logger.error(
                "FLIPKART PRICE SCRAPING FAILED"
            )
            self.logger.error("=" * 80)

            if result:
                self.logger.error(
                    "Error : %s",
                    result.get("error")
                )

            self.logger.error("=" * 80)
            return

        results = result.get("results") or []

        self.logger.info("=" * 80)
        self.logger.info(
            "FLIPKART PRICE TRACKING FINISHED"
        )
        self.logger.info("=" * 80)

        self.logger.info(
            "Products scraped    : %s",
            len(results)
        )

        self.logger.info(
            "Input JSON           : %s",
            INPUT_FILE
        )

        self.logger.info(
            "Output JSON          : %s",
            result.get("output_file")
        )

        self.logger.info(
            "Sending results to DealwalletScraperPipeline..."
        )

        pipeline_count = 0
        skipped_count = 0

        for product in results:
            # Do not send failed/blocked products to the database.
            # A valid current price is required by price_history.py.
            if (
                not isinstance(product, dict)
                or product.get("status") != "success"
                or product.get("price") is None
            ):
                skipped_count += 1
                continue

            pipeline_count += 1
            yield product

        self.logger.info(
            "Products sent to pipeline : %s",
            pipeline_count
        )

        self.logger.info(
            "Products skipped           : %s",
            skipped_count
        )

        self.logger.info(
            "Database/price history     : HANDLED BY PIPELINE"
        )

        self.logger.info("=" * 80)


# ============================================================
# STANDALONE MAIN
# ============================================================
#
# Normal Scrapyd/Scrapy usage:
#
#     scrapy crawl flipkart_price
#
# Direct usage is also supported:
#
#     python flipkart_price_spider.py
#
# ============================================================

if __name__ == "__main__":

    print("\n" + "=" * 80)
    print("FLIPKART EXISTING PRODUCT PRICE TRACKER")
    print("=" * 80)

    print(
        f"Store       : {STORE_NAME}"
    )

    print(
        "Mode        : EXISTING PRODUCTS ONLY"
    )

    print(
        f"Concurrency : {CONCURRENT_REQUESTS}"
    )

    print(
        "Browser     : Playwright Chromium"
    )

    print("=" * 80)

    products = get_flipkart_products()

    if not products:

        print(
            "\nNo Flipkart products found."
        )

        raise SystemExit

    # For standalone execution, configure Proactor before
    # starting the Playwright child process.
    if sys.platform == "win32":

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    run_playwright_process(
        products
    )

    print("\n" + "=" * 80)
    print("FLIPKART PRICE TRACKING FINISHED")
    print("=" * 80)

    print(
        f"Input JSON  : {INPUT_FILE}"
    )

    print(
        f"Output JSON : {OUTPUT_FILE}"
    )

    print("=" * 80)

    print(
        "\nStandalone mode does not run the Scrapy pipeline."
    )

    print(
        "Use 'scrapy crawl flipkart_price' through Scrapyd/Scrapy "
        "to send results to the database/price history."
    )

    print("=" * 80)
