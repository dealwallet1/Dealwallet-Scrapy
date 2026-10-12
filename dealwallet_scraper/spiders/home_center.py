import asyncio
import json
import logging
import random
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================
BASE_URL = "https://www.homecentre.in"
OUTPUT_FILE = "scrape_homecentre.json"
MAX_PAGES = 8
PAGE_TIMEOUT = 60000
LISTING_DELAY_MIN = 1.0
LISTING_DELAY_MAX = 2.0
DETAIL_PAGE_DELAY = 1.0

# Only Home Decor is enabled. Add other category templates if needed.
BASE_URLS = {
    "Home Decor": [
        "https://www.homecentre.in/in/en/search?q=decor&p={page}"
    ],
    "Furnitures": [
        "https://www.homecentre.in/in/en/search?q=sofa&p={page}",
        "https://www.homecentre.in/in/en/search?q=recliner&p={page}",
        "https://www.homecentre.in/in/en/search?q=coffee+table&p={page}",
        "https://www.homecentre.in/in/en/search?q=tv+unit&p={page}",
        "https://www.homecentre.in/in/en/search?q=queen+bed&p={page}",
        "https://www.homecentre.in/in/en/search?q=wardrobe&p={page}",
        "https://www.homecentre.in/in/en/search?q=dining+table&p={page}",
        "https://www.homecentre.in/in/en/search?q=dining+chair&p={page}",
        "https://www.homecentre.in/in/en/search?q=study+desk&p={page}",
        "https://www.homecentre.in/in/en/search?q=office+chair&p={page}",
        "https://www.homecentre.in/in/en/search?q=mattress&p={page}",
        "https://www.homecentre.in/in/en/search?q=shoe+rack&p={page}",
    ],
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) "
    "Gecko/20100101 Firefox/129.0",
]


# ============================================================
# HELPERS
# ============================================================
def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_price(value):
    """Convert price text such as '₹1,299.00' or '1,299' to integer rupees."""
    if value is None:
        return None

    text = normalize_text(value)
    if not text:
        return None

    # Capture one numeric price, ignoring currency symbols and other text.
    match = re.search(r"\d[\d,]*(?:\.\d{1,2})?", text)
    if not match:
        return None

    numeric_text = match.group(0).replace(",", "")
    try:
        amount = Decimal(numeric_text)
    except InvalidOperation:
        return None

    if amount <= 0:
        return None
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def extract_product_name_and_link(card):
    # Prefer the title anchor; avoid selecting an image-only anchor.
    tag = card.select_one("a[aria-label][href]")
    if tag:
        name = normalize_text(tag.get("aria-label")) or normalize_text(
            tag.get_text(" ", strip=True)
        )
        href = (tag.get("href") or "").strip()
        if name and href:
            return name, urljoin(BASE_URL, href)

    # Fallback for alternate Home Centre markup.
    for selector in ("a[href*='/in/en/p/']", "a[href*='/product/']", "a[href]"):
        for link in card.select(selector):
            href = (link.get("href") or "").strip()
            name = normalize_text(link.get("aria-label")) or normalize_text(
                link.get_text(" ", strip=True)
            )
            if href and name and not href.startswith("#"):
                return name, urljoin(BASE_URL, href)
    return None, None


def extract_prices(card):
    """Return (selling_price, original_price) from a listing card."""
    values = []

    # General method: numeric text next to a currency-symbol sibling.
    for tag in card.find_all(["div", "span", "p"]):
        direct_text = "".join(
            child for child in tag.children if isinstance(child, str)
        ).strip()
        if not direct_text or not re.fullmatch(r"\d[\d,]*(?:\.\d{1,2})?", direct_text):
            continue

        previous = tag.find_previous_sibling()
        if previous and normalize_text(previous.get_text(" ", strip=True)) == "₹":
            price = clean_price(direct_text)
            if price is not None and price not in values:
                values.append(price)

    # Fallback selectors from the supplied Home Centre HTML.
    if not values:
        current_tag = card.select_one(".jss643 .jss645")
        original_tag = card.select_one(".jss646 span:last-child")
        current = clean_price(current_tag.get_text(" ", strip=True) if current_tag else None)
        original = clean_price(original_tag.get_text(" ", strip=True) if original_tag else None)
        if current is not None:
            values.append(current)
        if original is not None and original not in values:
            values.append(original)

    # Additional generic fallback for markup with visible price classes.
    if not values:
        for selector in (".price", "[class*='price']", "[data-testid*='price']"):
            for tag in card.select(selector):
                value = clean_price(tag.get_text(" ", strip=True))
                if value is not None and value not in values:
                    values.append(value)
            if values:
                break

    current_price = values[0] if values else None
    original_price = values[1] if len(values) > 1 else None

    # If the selector order appears reversed, normalize using the lower price
    # as selling price and higher price as original price.
    if current_price is not None and original_price is not None:
        if original_price < current_price:
            current_price, original_price = original_price, current_price

    return current_price, original_price


def calculate_discount(current_price, original_price):
    """Calculate the integer percentage discount from the two prices."""
    if current_price is None or original_price is None:
        return None
    if original_price <= 0 or current_price < 0 or original_price <= current_price:
        return None

    percentage = (Decimal(original_price - current_price) / Decimal(original_price)) * Decimal(100)
    return int(percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def extract_image(card):
    image = card.select_one("img.jss602") or card.select_one("img")
    if not image:
        return None

    image_url = (
        image.get("src")
        or image.get("data-src")
        or image.get("data-original")
        or image.get("data-lazy-src")
    )
    if not image_url or image_url.strip().startswith("data:"):
        return None
    return urljoin(BASE_URL, image_url.strip())


def extract_detail_description(html, product_name):
    if not html:
        return product_name

    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        "div.MuiCollapse-wrapperInner",
        '[class*="description"]',
        '[data-testid*="description"]',
        "meta[name='description']",
    ]

    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue

        if element.name == "meta":
            description = normalize_text(element.get("content"))
        else:
            description = normalize_text(element.get_text(" ", strip=True))

        if description and description != product_name:
            return description

    og_description = soup.select_one('meta[property="og:description"]')
    if og_description:
        description = normalize_text(og_description.get("content"))
        if description:
            return description

    return product_name


async def goto_with_retries(page, url, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT
            )
            if response and response.status == 429:
                if attempt < max_retries:
                    wait_seconds = 10 * attempt
                    logging.warning(
                        "HTTP 429 for %s; waiting %s seconds", url, wait_seconds
                    )
                    await page.wait_for_timeout(wait_seconds * 1000)
                    continue
                logging.error("HTTP 429 after retries: %s", url)
                return None
            return response
        except Exception as exc:
            logging.warning(
                "Navigation attempt %s failed for %s: %s", attempt, url, exc
            )
            if attempt < max_retries:
                await page.wait_for_timeout(2000 * attempt)
    return None


# ============================================================
# PLAYWRIGHT SCRAPER WORKER (runs in separate process)
# ============================================================
async def scrape_homecentre_async():
    products = []
    seen_urls = set()
    seen_names = set()
    ist = timezone(timedelta(hours=5, minutes=30))

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)
        page.set_default_navigation_timeout(PAGE_TIMEOUT)

        try:
            for category, templates in BASE_URLS.items():
                for template in templates:
                    for page_number in range(1, MAX_PAGES + 1):
                        url = template.format(page=page_number)
                        logging.info(
                            "Home Centre listing page %s: %s", page_number, url
                        )
                        response = await goto_with_retries(page, url)
                        if response is None:
                            logging.warning("Could not open listing page: %s", url)
                            break

                        try:
                            await page.wait_for_selector(
                                'div.product[id^="product-"]', timeout=20000
                            )
                        except Exception:
                            logging.warning(
                                "No Home Centre product cards found on page %s: %s",
                                page_number,
                                url,
                            )
                            break

                        html = await page.content()
                        soup = BeautifulSoup(html, "html.parser")
                        cards = soup.select('div.product[id^="product-"]')
                        if not cards:
                            logging.info("No product cards on page %s", page_number)
                            break

                        added = 0
                        for card in cards:
                            try:
                                name, product_link = extract_product_name_and_link(card)
                                current_price, original_price = extract_prices(card)
                                image_link = extract_image(card)

                                if not name or not product_link:
                                    logging.warning("Skipping card without name/link")
                                    continue
                                if current_price is None:
                                    logging.warning(
                                        "Skipping product without current price: %s", name
                                    )
                                    continue
                                if not image_link:
                                    logging.warning(
                                        "Skipping product without image: %s", name
                                    )
                                    continue

                                # Only continue when both prices exist and there is
                                # a genuine discount. This check happens BEFORE
                                # opening the product detail page.
                                if original_price is None:
                                    logging.info(
                                        "Skipping %s before detail page: original price missing",
                                        name,
                                    )
                                    continue

                                if original_price <= current_price:
                                    logging.info(
                                        "Skipping %s before detail page: no discount "
                                        "(selling=%s, original=%s)",
                                        name,
                                        current_price,
                                        original_price,
                                    )
                                    continue

                                # Always calculate discount from prices. Do not use
                                # the website's rounded badge as the stored value.
                                discount = calculate_discount(current_price, original_price)
                                if discount is None or discount <= 0:
                                    logging.info(
                                        "Skipping %s before detail page: invalid discount=%s",
                                        name,
                                        discount,
                                    )
                                    continue

                                dedup_url = product_link.split("?")[0].rstrip("/").lower()
                                dedup_name = name.casefold()
                                if dedup_url in seen_urls or dedup_name in seen_names:
                                    continue

                                description = name
                                detail_page = await context.new_page()
                                try:
                                    await asyncio.sleep(DETAIL_PAGE_DELAY)
                                    detail_response = await goto_with_retries(
                                        detail_page, product_link
                                    )
                                    if detail_response:
                                        await detail_page.wait_for_timeout(1200)
                                        description = extract_detail_description(
                                            await detail_page.content(), name
                                        )
                                except Exception as exc:
                                    logging.warning(
                                        "Description extraction failed for %s: %s",
                                        product_link,
                                        exc,
                                    )
                                finally:
                                    await detail_page.close()

                                product_data = {
                                    "name": name,
                                    "price": current_price,
                                    "currency": "₹",
                                    "original_price": original_price,
                                    "discount": discount,
                                    "ratings": "N/A",
                                    "description": description,
                                    "image_link": image_link,
                                    "product_link": product_link,
                                    "organization_id": "Dealwallet",
                                    "store_id": "Home Centre",
                                    "categories_id": category,
                                    "created_at": datetime.now(ist).strftime(
                                        "%Y-%m-%dT%H:%M:%S"
                                    ),
                                }

                                products.append(product_data)
                                seen_urls.add(dedup_url)
                                seen_names.add(dedup_name)
                                added += 1
                                logging.info(
                                    "Scraped: %s | selling=%s | original=%s | discount=%s%%",
                                    name,
                                    current_price,
                                    original_price,
                                    discount,
                                )
                            except Exception:
                                logging.exception(
                                    "Failed to parse a Home Centre product card"
                                )

                        logging.info(
                            "Added %s products from page %s", added, page_number
                        )
                        await asyncio.sleep(
                            random.uniform(LISTING_DELAY_MIN, LISTING_DELAY_MAX)
                        )
        finally:
            await context.close()
            await browser.close()

    logging.info("Home Centre scraping completed. Total products: %s", len(products))
    return products


def run_homecentre_scraper():
    """Picklable worker entry point for ProcessPoolExecutor."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    return asyncio.run(scrape_homecentre_async())


# ============================================================
# SCRAPY SPIDER - same pattern as SoulFlowerSpider
# ============================================================
class HomeCentreSpider(scrapy.Spider):
    name = "homecentre"
    allowed_domains = ["homecentre.in", "www.homecentre.in"]

    async def start(self):
        self.logger.info("Starting Home Centre Playwright scraper")
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(executor, run_homecentre_scraper)

        # Save a raw JSON copy.
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(products, file, ensure_ascii=False, indent=2)
        self.logger.info("Saved %s products to %s", len(products), OUTPUT_FILE)

        for product in products:
            if not product.get("product_link") or not product.get("image_link"):
                continue
            yield product

        self.logger.info("Home Centre spider completed")
