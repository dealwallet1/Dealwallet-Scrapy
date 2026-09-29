import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.woodlandworldwide.com"

MAX_PAGES = 2
MAX_PRODUCTS_PER_CATEGORY = 30


BASE_URLS = {
    "https://www.woodlandworldwide.com/collections?search=jackets": "Seasonal Wear",
    "https://www.woodlandworldwide.com/collections/women/gloves": "Seasonal Wear",
    "https://www.woodlandworldwide.com/collections/men/t-shirts": "Fashion & Lifestyle",
    "https://www.woodlandworldwide.com/collections/men/tshirts": "Fashion & Lifestyle",
    "https://www.woodlandworldwide.com/collections/men/sneakers": "Shoes",
    "https://www.woodlandworldwide.com/collections/women/boots": "Shoes",
    "https://www.woodlandworldwide.com/collections/women/backpacks": "Bags",
    "https://www.woodlandworldwide.com/collections/women/bags": "Bags",
    "https://www.woodlandworldwide.com/collections/men/shirts": "Shirts"
}

LISTING_PAGE_DELAY = 3
DETAIL_PAGE_DELAY = 3
RATE_LIMIT_DELAY = 30
MAX_429_RETRIES = 3
PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000

OUTPUT_FILE = "scrape_woodland.json"


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def parse_price(value):
    if not value:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value).replace("₹", ""))
    if not match:
        return None
    number = float(match.group().replace(",", ""))
    return int(number) if number.is_integer() else number


def clean_to_int(value):
    parsed = parse_price(value)
    return int(parsed) if parsed is not None else None


def make_page_url(base_url, page_number):
    """Add/update the page query parameter without losing existing query values."""
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page_number)]
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params,
         urlencode(query, doseq=True), parsed.fragment)
    )


def absolute_url(url):
    if not url:
        return None
    url = url.strip()
    if url.startswith("data:"):
        return None
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE_URL, url)


def extract_image_from_card(card):
    if not card:
        return None

    img = (
        card.select_one("div.plp-card-media img")
        or card.select_one("picture img")
        or card.select_one("img")
    )
    if not img:
        return None

    image = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-lazy-src")
        or img.get("data-original")
    )

    if not image:
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            image = srcset.split(",")[0].strip().split()[0]

    return absolute_url(image)


def extract_product_description(product_html):
    if not product_html:
        return None

    soup = BeautifulSoup(product_html, "html.parser")

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
        tags = soup.select(selector)
        texts = [
            normalize_text(tag.get_text(" ", strip=True))
            for tag in tags
        ]
        texts = [text for text in texts if len(text) > 20]
        if texts:
            # Deduplicate while preserving order.
            return " ".join(dict.fromkeys(texts))

    # Metadata fallbacks.
    meta = soup.select_one('meta[name="description"]')
    if meta and meta.get("content"):
        return normalize_text(meta.get("content"))

    og = soup.select_one('meta[property="og:description"]')
    if og and og.get("content"):
        return normalize_text(og.get("content"))

    scripts = soup.select('script[type="application/ld+json"]')
    for script in scripts:
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = []
        if isinstance(data, dict):
            objects.append(data)
            if isinstance(data.get("@graph"), list):
                objects.extend(data["@graph"])
        elif isinstance(data, list):
            objects.extend(data)

        for item in objects:
            if isinstance(item, dict) and item.get("description"):
                return normalize_text(item["description"])

    return None


async def open_page_with_retry(page, url, label="page"):
    for attempt in range(1, MAX_429_RETRIES + 1):
        try:
            logging.info(
                "Opening %s (attempt %s/%s): %s",
                label, attempt, MAX_429_RETRIES, url
            )
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            if response and response.status == 429:
                if attempt < MAX_429_RETRIES:
                    logging.warning(
                        "HTTP 429 for %s. Waiting %s seconds.",
                        url, RATE_LIMIT_DELAY
                    )
                    await page.wait_for_timeout(RATE_LIMIT_DELAY * 1000)
                    continue
                logging.error("HTTP 429 after retries: %s", url)
                return None

            if response and response.status >= 400:
                logging.warning(
                    "%s returned HTTP %s: %s",
                    label, response.status, url
                )
                return None

            return response

        except Exception as exc:
            logging.warning("Failed %s %s: %s", label, url, exc)
            if attempt < MAX_429_RETRIES:
                await page.wait_for_timeout(DETAIL_PAGE_DELAY * 1000)

    return None


async def scrape_product_description(context, product_url):
    if not product_url:
        return None

    product_page = await context.new_page()
    product_page.set_default_timeout(PAGE_TIMEOUT)
    product_page.set_default_navigation_timeout(PAGE_TIMEOUT)

    try:
        response = await open_page_with_retry(
            product_page, product_url, "Woodland product page"
        )
        if not response:
            return None

        await product_page.wait_for_timeout(2000)
        html = await product_page.content()
        return extract_product_description(html)

    except Exception as exc:
        logging.warning("Description scrape failed for %s: %s", product_url, exc)
        return None

    finally:
        await product_page.close()


# ============================================================
# WOODLAND PLAYWRIGHT SCRAPER
# ============================================================

async def scrape_woodland_async():
    results = []
    seen_links = set()
    seen_names = set()
    category_counts = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)
        page.set_default_navigation_timeout(PAGE_TIMEOUT)

        try:
            for category, urls in BASE_URLS.items():
                category_results = []

                for base_url in urls:
                    for page_number in range(1, MAX_PAGES + 1):
                        if page_number > 1:
                            await page.wait_for_timeout(
                                LISTING_PAGE_DELAY * 1000
                            )

                        listing_url = make_page_url(base_url, page_number)
                        logging.info("=" * 60)
                        logging.info("Category: %s | URL: %s", category, listing_url)

                        response = await open_page_with_retry(
                            page, listing_url, "Woodland listing page"
                        )
                        if not response:
                            break

                        try:
                            await page.wait_for_selector(
                                "div.plp-grid article",
                                timeout=PRODUCT_SELECTOR_TIMEOUT,
                            )
                        except Exception:
                            logging.warning(
                                "Woodland product cards not found: %s",
                                listing_url
                            )
                            break

                        # Trigger lazy-loaded listing content.
                        for _ in range(3):
                            await page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight)"
                            )
                            await page.wait_for_timeout(1200)

                        soup = BeautifulSoup(
                            await page.content(), "html.parser"
                        )
                        cards = soup.select("div.plp-grid article")

                        logging.info("Cards found: %s", len(cards))
                        if not cards:
                            break

                        for index, card in enumerate(cards, start=1):
                            if len(category_results) >= MAX_PRODUCTS_PER_CATEGORY:
                                break

                            try:
                                # PRODUCT NAME
                                name_tag = card.select_one("h3")
                                name = (
                                    normalize_text(name_tag.get_text(" ", strip=True))
                                    if name_tag else None
                                )
                                if not name:
                                    logging.warning(
                                        "Name missing on card %s", index
                                    )
                                    continue

                                # PRODUCT LINK
                                link_tag = card.select_one(
                                    'a[href^="/product/"], a[href*="/product/"]'
                                )
                                if not link_tag:
                                    continue

                                product_link = absolute_url(
                                    link_tag.get("href")
                                )
                                if not product_link:
                                    continue

                                if product_link in seen_links or name.lower() in seen_names:
                                    continue

                                # PRICE CONTAINER
                                price_container = (
                                    card.select_one("p.text-sm.font-semibold")
                                    or card.select_one("p.font-semibold")
                                )
                                if not price_container:
                                    logging.info("Price container missing: %s", name)
                                    continue

                                original_tag = price_container.select_one(
                                    "span.line-through"
                                )
                                original_price = (
                                    parse_price(
                                        original_tag.get_text(" ", strip=True)
                                    )
                                    if original_tag else None
                                )

                                price_copy = BeautifulSoup(
                                    str(price_container), "html.parser"
                                )
                                old_span = price_copy.select_one("span.line-through")
                                if old_span:
                                    old_span.decompose()

                                price = parse_price(
                                    price_copy.get_text(" ", strip=True)
                                )
                                if price is None or price <= 0:
                                    logging.info("Selling price missing: %s", name)
                                    continue

                                discount = None
                                if (
                                    original_price is not None
                                    and original_price > 0
                                    and price <= original_price
                                ):
                                    discount = round(
                                        ((original_price - price) / original_price)
                                        * 100
                                    )

                                # IMAGE
                                image_link = extract_image_from_card(card)

                                # DESCRIPTION FROM PDP
                                logging.info(
                                    "Waiting %s seconds before PDP: %s",
                                    DETAIL_PAGE_DELAY, name
                                )
                                await page.wait_for_timeout(
                                    DETAIL_PAGE_DELAY * 1000
                                )
                                description = await scrape_product_description(
                                    context, product_link
                                )

                                timestamp = datetime.now(
                                    timezone(timedelta(hours=5, minutes=30))
                                ).strftime("%Y-%m-%dT%H:%M:%S")

                                product = {
                                    "name": name,
                                    "price": clean_to_int(price),
                                    "currency": "₹",
                                    "original_price": clean_to_int(original_price),
                                    "discount": discount,
                                    "ratings": None,
                                    "description": description,
                                    "image_link": image_link,
                                    "product_link": product_link,
                                    "organization_id": "DealWallet",
                                    "store_id": "Woodland",
                                    "categories_id": category,
                                    "created_at": timestamp,
                                }

                                # Keep the product even if description is unavailable.
                                results.append(product)
                                category_results.append(product)
                                seen_links.add(product_link)
                                seen_names.add(name.lower())

                                logging.info(
                                    "Scraped: %s | Price: %s | Original: %s",
                                    name, price, original_price
                                )

                            except Exception:
                                logging.exception(
                                    "Error parsing card #%s on page %s",
                                    index, page_number
                                )

                        if len(category_results) >= MAX_PRODUCTS_PER_CATEGORY:
                            break

                category_counts[category] = len(category_results)
                logging.info(
                    "Category completed: %s | Products: %s",
                    category, len(category_results)
                )

        finally:
            await page.close()
            await context.close()
            await browser.close()

    logging.info("Woodland total products: %s", len(results))
    logging.info("Category counts: %s", category_counts)
    return results


# ============================================================
# PROCESS RUNNER (SOULFLOWER SPIDER STYLE)
# ============================================================

def run_woodland_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)
    logging.info("Woodland browser process started.")
    logging.info("=" * 60)

    try:
        products = asyncio.run(scrape_woodland_async())
        logging.info(
            "Woodland browser process finished. Products: %s",
            len(products)
        )
        return products
    except Exception:
        logging.exception("Woodland browser process failed.")
        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class WoodlandSpider(scrapy.Spider):
    name = "woodland"
    allowed_domains = ["woodlandworldwide.com", "www.woodlandworldwide.com"]

    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting Woodland Playwright scraper...")
        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_woodland_scraper
            )

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)

        self.logger.info(
            "Woodland scraper returned %s products.", len(products)
        )

        for product in products:
            if not product.get("product_link"):
                continue
            if not product.get("image_link"):
                continue

            # Description is optional; missing description does not discard item.
            yield product

        self.logger.info("=" * 60)
        self.logger.info("Woodland spider completed.")
        self.logger.info("=" * 60)
