import os
import re
import sys
import json
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse, urljoin
from concurrent.futures import ProcessPoolExecutor

import psycopg2
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from scrapy import Spider

# ============================================================
# CONFIGURATION
# ============================================================
load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

STORE_NAME = "Home Centre"
BASE_URL = "https://www.homecentre.in"
MAX_RETRIES = 3
PAGE_TIMEOUT = 60000
DETAIL_WAIT_MS = 1500
REQUEST_DELAY_SECONDS = 1
INPUT_FILE = "homecentre_existing_products.json"
OUTPUT_FILE = "homecentre_existing_scraped_results.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def clean_text(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def clean_price(value):
    """Convert visible price text such as '₹1,299.00' into integer rupees."""
    if value is None:
        return None
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", text)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
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
        return round(((original_price - price) / original_price) * 100)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def normalize_url(url):
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            url = urljoin(BASE_URL, url)
            parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    except Exception:
        return url


def get_db_connection():
    required = {
        "DB_HOST": DB_HOST,
        "DB_NAME": DB_NAME,
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_homecentre_products():
    """Fetch only already-existing Home Centre products; this does not discover products."""
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
            JOIN public.stores s ON p.store_id = s.id
            LEFT JOIN public.organization o ON p.organization_id = o.id
            LEFT JOIN public.categories c ON p.categories_id = c.id
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
                product_id, name, price, original_price, currency, discount,
                ratings, description, image_link, product_link,
                organization_name, store_name, category_name, affiliate_url,
            ) = row
            products.append({
                "product_id": str(product_id),
                "name": name,
                "price": price,
                "original_price": original_price,
                "currency": currency or "₹",
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
        with open(INPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(products, file, indent=2, ensure_ascii=False, default=str)
        logger.info("Fetched %s existing Home Centre products", len(products))
        logger.info("Input saved: %s", INPUT_FILE)
        return products
    except Exception:
        logger.exception("Could not fetch existing Home Centre products")
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


async def get_first_text(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(min(count, 10)):
                try:
                    value = clean_text(await locator.nth(index).inner_text(timeout=2500))
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
            for index in range(min(count, 10)):
                try:
                    value = clean_text(await locator.nth(index).get_attribute(attribute, timeout=2500))
                    if value:
                        return value
                except Exception:
                    continue
        except Exception:
            continue
    return None


def extract_prices_from_html(html):
    """Use JSON-LD/meta as a fallback when the visible Home Centre price selectors change."""
    soup = BeautifulSoup(html or "", "html.parser")
    current_price = None
    original_price = None

    for tag in soup.select('script[type="application/ld+json"]'):
        raw = tag.string or tag.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                candidates.extend(x for x in graph if isinstance(x, dict))
            if str(item.get("@type", "")).lower() == "product" or "offers" in item:
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    current_price = current_price or clean_price(offers.get("price"))
                    specs = offers.get("priceSpecification")
                    if isinstance(specs, list):
                        for spec in specs:
                            if isinstance(spec, dict):
                                val = clean_price(spec.get("price"))
                                if val and current_price and val > current_price:
                                    original_price = max(original_price or 0, val)
                    elif isinstance(specs, dict):
                        val = clean_price(specs.get("price"))
                        if val and current_price and val > current_price:
                            original_price = val

    if current_price is None:
        for selector in [
            '#details-price .jss538',
            '#details-price [class*="jss538"]',
            'meta[property="product:price:amount"]',
            'meta[itemprop="price"]',
            '[itemprop="price"]',
        ]:
            tag = soup.select_one(selector)
            if tag:
                current_price = clean_price(tag.get("content") or tag.get_text(" ", strip=True))
                if current_price is not None:
                    break

    if original_price is None:
        for selector in [
            '#details-pricepromotion #details-base-price .jss542',
            '#details-pricepromotion #details-base-price',
            '#details-base-price [class*="jss542"]',
            "span.line-through",
            "del",
            "s",
            '[class*="original-price"]',
            '[class*="originalPrice"]',
            '[class*="strike"]',
            '[class*="mrp"]',
        ]:
            for tag in soup.select(selector):
                candidate = clean_price(tag.get_text(" ", strip=True))
                if candidate is not None and current_price is not None and candidate > current_price:
                    original_price = candidate
                    break
            if original_price is not None:
                break

    return current_price, original_price


def extract_description(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for selector in [
        "div.MuiCollapse-wrapperInner",
        '[data-testid*="description"]',
        '[class*="product-description"]',
        '[class*="productDescription"]',
        '[class*="description"]',
        '[id*="description"]',
    ]:
        texts = [clean_text(tag.get_text(" ", strip=True)) for tag in soup.select(selector)]
        texts = [text for text in texts if text and len(text) > 20]
        if texts:
            return " ".join(dict.fromkeys(texts))
    meta = soup.select_one('meta[name="description"]')
    if meta:
        return clean_text(meta.get("content"))
    og = soup.select_one('meta[property="og:description"]')
    return clean_text(og.get("content")) if og else None


async def extract_image(page):
    selectors = [
        'img[src*="landmarkshops.in"]',
        'img[data-src*="landmarkshops.in"]',
        'img[src*="homecentre"]',
        "main img",
        'img[alt*="product" i]',
        "img",
    ]
    for selector in selectors:
        try:
            images = page.locator(selector)
            count = await images.count()
            for index in range(min(count, 20)):
                img = images.nth(index)
                for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                    try:
                        value = await img.get_attribute(attr)
                        if value and not value.strip().startswith("data:"):
                            value = urljoin(BASE_URL, value.strip())
                            if value.startswith("http"):
                                return value
                    except Exception:
                        continue
        except Exception:
            continue
    return None


def make_result(product, url, price=None, original_price=None, description=None,
                image_link=None, status="failed", http_status=None, scraped_name=None):
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

    # If the page doesn't expose an original price, preserve the existing value
    # rather than treating the missing value as zero or inventing a discount.
    effective_original_price = original_price
    if effective_original_price is None:
        effective_original_price = product.get("original_price")
    discount = calculate_discount(price, effective_original_price)

    return {
        "product_id": product.get("product_id"),
        "name": product.get("name"),
        "database_price": database_price,
        "price": price,
        "original_price": effective_original_price,
        "currency": product.get("currency") or "₹",
        "discount": discount,
        "ratings": product.get("ratings"),
        "description": description or product.get("description"),
        "image_link": image_link or product.get("image_link"),
        "product_link": product.get("product_link"),
        "scrape_url": url,
        "organization_id": product.get("organization_id"),
        "store_id": product.get("store_id"),
        "store_name": product.get("store_name"),
        "categories_id": product.get("categories_id"),
        "affiliate_url": product.get("affiliate_url"),
        "price_changed": changed,
        "price_difference": difference,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "http_status": http_status,
        "scraped_name": scraped_name,
    }


async def scrape_product(context, product, index, total):
    original_url = product.get("product_link")
    url = product.get("scrape_url") or normalize_url(original_url)
    logger.info("PRODUCT %s/%s | id=%s | %s", index, total, product.get("product_id"), url)

    if not url:
        return make_result(product, "", status="missing_url")

    for attempt in range(1, MAX_RETRIES + 1):
        page = None
        try:
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            status_code = response.status if response else None
            if status_code and status_code != 200:
                logger.warning("HTTP %s attempt %s for %s", status_code, attempt, url)
                if attempt < MAX_RETRIES and status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(attempt * 2)
                    continue
                return make_result(product, url, status="non_200", http_status=status_code)

            await page.wait_for_timeout(DETAIL_WAIT_MS)

            # Home Centre current selling-price selectors, followed by generic fallbacks.
            price_text = await get_first_text(page, [
                '#details-price .jss538',
                '#details-price [class*="jss538"]',
                '#details-price [class*="jss515"]',
                '[data-testid="selling-price"]',
                '[data-testid*="selling-price"]',
                '[class*="discountedPrice"]',
                '[class*="selling-price"]',
                '[class*="sellingPrice"]',
                'meta[property="product:price:amount"]',
                '[itemprop="price"]',
                'span[class*="price"]',
            ])
            price = clean_price(price_text)

            original_text = await get_first_text(page, [
                '#details-pricepromotion #details-base-price .jss542',
                '#details-pricepromotion #details-base-price [class*="jss542"]',
                '#details-base-price [class*="jss506"]',
                '[data-testid*="original-price"]',
                '[class*="original-price"]',
                '[class*="originalPrice"]',
                "span.line-through",
                "del",
                "s",
                '[class*="strike"]',
                '[class*="mrp"]',
            ])
            original_price = clean_price(original_text)

            html = await page.content()
            fallback_price, fallback_original = extract_prices_from_html(html)
            if price is None:
                price = fallback_price
            if original_price is None:
                original_price = fallback_original

            scraped_name = await get_first_text(page, [
                '#root-prod-details-inner h1',
                '#root-prod-details-outer h1',
                'h1.jss525',
                'h1',
                '[data-testid*="product-title"]'
            ])
            description = extract_description(html)
            image_link = await extract_image(page)
            discount = calculate_discount(price, original_price or product.get("original_price"))
            logger.info("Price=%s | Original=%s | Discount=%s%%", price, original_price, discount)

            if price is None:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(attempt * 2)
                    continue
                return make_result(
                    product, url, original_price=original_price, description=description,
                    image_link=image_link, status="price_missing", http_status=status_code,
                    scraped_name=scraped_name,
                )

            result = make_result(
                product, url, price=price, original_price=original_price,
                description=description, image_link=image_link, status="success",
                http_status=status_code, scraped_name=scraped_name,
            )
            logger.info(
                "DB Price=%s | Scraped Price=%s | Changed=%s | Difference=%s | Discount=%s%%",
                result["database_price"], result["price"], result["price_changed"],
                result["price_difference"], result["discount"],
            )
            return result

        except PlaywrightTimeoutError as exc:
            logger.warning("Timeout attempt %s/%s for %s: %s", attempt, MAX_RETRIES, url, exc)
        except Exception as exc:
            logger.exception("Scrape attempt %s failed for %s: %s", attempt, url, exc)
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
        if attempt < MAX_RETRIES:
            await asyncio.sleep(attempt * 2)

    return make_result(product, url, status="failed")


async def run_scraper(products):
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        try:
            for index, product in enumerate(products, start=1):
                results.append(await scrape_product(context, product, index, len(products)))
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
        finally:
            await context.close()
            await browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(results, file, indent=2, ensure_ascii=False, default=str)

    success = sum(result.get("status") == "success" for result in results)
    price_missing = sum(result.get("status") == "price_missing" for result in results)
    failed = sum(result.get("status") in ("failed", "non_200", "missing_url") for result in results)
    logger.info("Finished | Total=%s | Success=%s | Price Missing=%s | Failed=%s", len(results), success, price_missing, failed)
    logger.info("Output saved: %s", OUTPUT_FILE)
    return results


def run_playwright_process(products):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return {"success": True, "results": asyncio.run(run_scraper(products)), "output_file": OUTPUT_FILE}


class HomeCentreExistingProductsSpider(Spider):
    name = "homecentre_price_tracking"
    allowed_domains = ["homecentre.in", "www.homecentre.in"]
    custom_settings = {"CONCURRENT_REQUESTS": 1}

    async def start(self):
        self.logger.info("HOME CENTRE EXISTING PRODUCTS PRICE TRACKER — NO PRODUCT DISCOVERY")
        products = get_homecentre_products()
        if not products:
            self.logger.warning("No existing Home Centre products found. Check DB settings and store name.")
            return

        loop = asyncio.get_running_loop()
        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                result = await loop.run_in_executor(executor, run_playwright_process, products)
        except Exception as exc:
            self.logger.error("Playwright process failed: %s", exc, exc_info=True)
            return

        if not result or not result.get("success"):
            self.logger.error("Existing Home Centre product scraping failed.")
            return

        scraped = result.get("results") or []
        sent = 0
        skipped = 0
        for product in scraped:
            if not isinstance(product, dict) or product.get("status") != "success" or product.get("price") is None:
                skipped += 1
                continue
            sent += 1
            yield product

        self.logger.info("Products sent to pipeline: %s | skipped: %s", sent, skipped)


if __name__ == "__main__":
    products = get_homecentre_products()
    if not products:
        raise SystemExit("No existing Home Centre products found. Check DB settings and store name.")
    result = run_playwright_process(products)
    print("Results:", result.get("output_file"))
    print("Standalone mode does not run Scrapy item pipelines.")
