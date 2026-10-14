import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.zop.in"

BASE_URLS = {
    "Baby & Kids": [
        "https://www.zop.in/collections/baby-care",
        "https://www.zop.in/collections/baby-care?f.Category=feeding",
        "https://www.zop.in/collections/baby-care?f.Category=baby+lotionscomma+oils+and+powders"
    ],
    
    "Bags": ["https://www.zop.in/collections/bags",],
    "Beauty": ["https://www.zop.in/collections/skincare",
     "https://www.zop.in/collections/skincare?f.Category=masks+%26+peel",
        "https://www.zop.in/collections/skincare?f.Category=serum",
        "https://www.zop.in/collections/skincare?f.Category=sunscreen"
        ],


    "Electronics": ["https://www.zop.in/collections/audio-gaming",
     "https://www.zop.in/collections/audio-gaming?f.Category=bluetooth+speakers",
     "https://www.zop.in/collections/audio-gaming?f.Category=wired+headphones",
     "https://www.zop.in/collections/audio-gaming?f.Category=bluetooth+headphones",
     "https://www.zop.in/collections/audio-gaming?f.Category=wireless+earbuds"],

    "Fashion & Lifestyle": [
        "https://www.zop.in/collections/mens-ethnic-wear",
        "https://www.zop.in/collections/mens-fashion?f.Category=men%27s+t-shirts",
        "https://www.zop.in/collections/womens-ethnic-wear",
        "https://www.zop.in/collections/womens-fashion?f.Category=women%27s+sarees"
        "https://www.zop.in/collections/womens-fashion?f.Category=women%27s+kurtascomma+kurtis+%26+suits",
        "https://www.zop.in/collections/womens-fashion?f.Category=women%27s+tops"
    ],


    "Food": ["https://www.zop.in/collections/food-beverage?f.Category=chocolates+%26+sweets",
             "https://www.zop.in/collections/food-beverage?f.Category=healthy+eating",
             "https://www.zop.in/collections/food-beverage?f.Category=dry+fruits",
             "https://www.zop.in/collections/food-beverage?f.Category=pappad+%26+fryums",
             "https://www.zop.in/collections/food-beverage?f.Category=popcorns"
             ],

                   
            

    "Grocery": ["https://www.zop.in/collections/staples",
    "https://www.zop.in/collections/staples?f.Category=oils+%26+ghee",
    "https://www.zop.in/collections/staples?f.Category=masalas",
    "https://www.zop.in/collections/staples?f.Category=attacomma+rice+%26+dals",
    "https://www.zop.in/collections/staples?f.Category=honey"
    ],


    "Health & Beauty": ["https://www.zop.in/collections/diet-and-nutrition?f.Category=vitaminscomma+minerals+%26+supplements",
                        "https://www.zop.in/collections/diet-and-nutrition?f.Category=sports+supplements",
                        "https://www.zop.in/collections/diet-and-nutrition?f.Category=weight+management"],

    "Home Decor": ["https://www.zop.in/collections/home-decor,"
    "https://www.zop.in/collections/home-furnishing",
    "https://www.zop.in/collections/home-furnishing?f.Category=cusions+%26+cushion+covers",
    "https://www.zop.in/collections/home-furnishing?f.Category=curtains+%26+accessories",
    "https://www.zop.in/collections/home-furnishing?f.Category=carpets+%26+rugs"
    ],


    "Home & Kitchen": ["https://www.zop.in/collections/kitchen-dining",
                       "https://www.zop.in/collections/kitchen-dining?f.Category=tableware+%26+dinnerware",
                       "https://www.zop.in/collections/kitchen-dining?f.Category=cookware",
                       "https://www.zop.in/collections/kitchen-dining?f.Category=knivescomma+choppers+%26+cutters",
                       "https://www.zop.in/collections/kitchen-dining?f.Category=kitchen+storage+%26+containers",
                       "https://www.zop.in/collections/kitchen-dining?f.Category=kitchen+appliances"
                       ],

    "Makeup": ["https://www.zop.in/collections/makeup",
               "https://www.zop.in/collections/makeup?f.Category=makeup+brush",
               "https://www.zop.in/collections/makeup?f.Category=lipstick",
               "https://www.zop.in/collections/makeup?f.Category=makeup+kit",
               "https://www.zop.in/collections/makeup?f.Category=eyeliner",
               "https://www.zop.in/collections/makeup?f.Category=eyeshadow"
               ],
    "Mobile Accessories": ["https://www.zop.in/collections/mobile-accessories",
                           "https://www.zop.in/collections/mobile-accessories?f.Category=mobile+chargers",
                           "https://www.zop.in/collections/mobile-accessories?f.Category=mobile+cases",
                           "https://www.zop.in/collections/mobile-accessories?f.Category=mobile+pouches+%26+holders",
                           "https://www.zop.in/collections/mobile-accessories?f.Category=powerbanks",
                           
                           ],




    "Others": ["https://www.zop.in/collections/bike-accessories-and-parts"],


    "Gym Equipments": ["https://www.zop.in/collections/fitness-accessories",
                       "https://www.zop.in/collections/fitness-accessories?f.Category=sports+equipment%2F+accessories",
                       "https://www.zop.in/collections/fitness-accessories?f.Category=strength+training+equipment",
                       "https://www.zop.in/collections/fitness-accessories?f.Category=exercise+%26+yoga+mats",
                       ""
                       ],
    "Shirts": ["https://www.zop.in/collections/mens-topwear?f.Category=men%27s+casual+shirts",
               "https://www.zop.in/collections/mens-topwear?f.Category=men%27s+formal+shirts"
               ],
    "Shoes": ["https://www.zop.in/collections/mens-footwear?f.Category=men%27s+casual+shoes",
              "https://www.zop.in/collections/mens-footwear?f.Category=men%27s+formal+shoes",
              "https://www.zop.in/collections/mens-footwear?f.Category=men%27s+sneakers",
              "https://www.zop.in/collections/mens-footwear?f.Category=men%27s+sports+shoes"
              ],
    "Toys Games": ["https://www.zop.in/collections/toys-games?f.Category=soft+toys",
    "https://www.zop.in/collections/toys-games?f.Category=baby+%26+toddler+toys",
    "https://www.zop.in/collections/toys-games?f.Category=learning+%26+education+toys",
    "https://www.zop.in/collections/toys-games?f.Category=puzzles",
    "https://www.zop.in/collections/toys-games?f.Category=playing+cards"
    ],
   
}

MAX_PAGES = 2
OUTPUT_FILE = "zop_store_all.json"

LISTING_PAGE_DELAY = 2
DETAIL_PAGE_DELAY = 2
RATE_LIMIT_DELAY = 30
MAX_429_RETRIES = 3
PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 30000


# ============================================================
# PRICE, RATING, DISCOUNT HELPERS
# ============================================================

def parse_money(value):
    """Parse price into int/float; return None if missing or invalid."""
    if value is None:
        return None

    text = (
        str(value)
        .replace(",", "")
        .replace("₹", "")
        .replace("INR", "")
        .strip()
    )
    match = re.search(r"\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        amount = Decimal(match.group(0))
        if not amount.is_finite() or amount < 0:
            return None
        return int(amount) if amount == amount.to_integral_value() else float(amount)
    except (InvalidOperation, ValueError):
        return None


def parse_rating(value):
    """Parse a valid rating from 0 to 5, or return None."""
    if value is None:
        return None

    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None

    try:
        rating = float(match.group(0))
        if not 0 <= rating <= 5:
            return None
        return int(rating) if rating.is_integer() else rating
    except ValueError:
        return None


def calculate_discount(original_price, price):
    """Calculate percentage discount from original and current prices."""
    if original_price is None or price is None or original_price <= 0:
        return 0

    if price >= original_price:
        return 0

    return round(((original_price - price) / original_price) * 100)


# ============================================================
# LISTING CARD HELPERS
# ============================================================

def get_product_link(card, base_url):
    link = card.select_one("h3.product-info-title a") or card.select_one("a[href]")
    href = link.get("href") if link else None
    return urljoin(base_url, href) if href else None


def get_image_url(soup_or_card, base_url):
    img = soup_or_card.select_one("img")
    if not img:
        return None

    src = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-original")
        or img.get("data-lazy-src")
    )

    if not src:
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            src = srcset.split(",")[0].strip().split()[0]

    if not src or src.startswith("data:"):
        return None

    return urljoin(base_url, src)


# ============================================================
# DESCRIPTION EXTRACTION
# ============================================================

def clean_description(text):
    """Decode HTML and reject placeholders, policy text, and seller details."""
    if not text:
        return None

    text = str(text)
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded

    text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return None

    lowered = text.lower()
    invalid_phrases = (
        "loading reviews",
        "no reviews available",
        "processing time",
        "orders are typically processed",
        "shipping time",
        "shipping times may vary",
        "shipping costs",
        "tracking information",
        "international shipping",
        "returns and exchange policy",
        "we currently do not offer returns",
        "thank you for choosing zop",
        "manufacturer address",
        "manufactured by",
        "country of origin",
        "support email",
        "support mobile number",
        "seller address",
        "sold by -",
        "imported and marketed by",
    )

    if any(phrase in lowered for phrase in invalid_phrases):
        return None

    if len(text) > 1200 and any(
        word in lowered for word in ("shipping", "refund", "checkout", "returns")
    ):
        return None

    return text


def extract_html_renderer_description(product_soup):
    """
    ZOP description is often HTML-encoded in the html-renderer content attribute.
    """
    selectors = (
        "div.accordion-details__content.rte.collapsibleSubtext html-renderer[content]",
        "div.accordion-details__content.rte.collapsibleSubtext html-renderer",
        "html-renderer[content]",
    )

    for selector in selectors:
        for renderer in product_soup.select(selector):
            content = renderer.get("content")
            if content:
                description = clean_description(content)
                if description:
                    return description

            child_text = renderer.get_text(" ", strip=True)
            description = clean_description(child_text)
            if description:
                return description

    return None


def get_description(product_soup, product_name):
    """Extract a product-specific description, avoiding store-wide policy text."""
    description = extract_html_renderer_description(product_soup)
    if description:
        return description

    # Product structured data fallback.
    for script in product_soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue

        entries = payload if isinstance(payload, list) else [payload]
        index = 0

        while index < len(entries):
            entry = entries[index]
            index += 1

            if not isinstance(entry, dict):
                continue

            graph = entry.get("@graph")
            if isinstance(graph, list):
                entries.extend(item for item in graph if isinstance(item, dict))

            entry_type = entry.get("@type")
            is_product = (
                entry_type == "Product"
                or (isinstance(entry_type, list) and "Product" in entry_type)
            )

            if is_product:
                description = clean_description(entry.get("description"))
                if description:
                    return description

    # Product-specific selectors only; avoid generic .rte containers.
    selectors = (
        ".product__description",
        ".product__description-content",
        ".product-description",
        ".product__block--description .rte",
        "[itemprop='description']",
        "#ProductInfo [id*='description']",
        "[id*='product-description']",
    )

    for selector in selectors:
        for element in product_soup.select(selector):
            nested = element.select_one("html-renderer[content]")
            if nested and nested.get("content"):
                description = clean_description(nested.get("content"))
            else:
                description = clean_description(element.get_text(" ", strip=True))

            if description:
                return description

    # Metadata fallback.
    for selector, attribute in (
        ('meta[property="og:description"]', "content"),
        ('meta[name="description"]', "content"),
    ):
        element = product_soup.select_one(selector)
        description = clean_description(element.get(attribute) if element else None)
        if description and description.lower() != product_name.lower():
            return description

    return product_name


def get_detail_rating(product_soup):
    tag = product_soup.select_one("div.product-rating span.big-point")
    if tag:
        rating = parse_rating(tag.get_text(" ", strip=True))
        if rating is not None:
            return rating

    for script in product_soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue

        entries = payload if isinstance(payload, list) else [payload]
        index = 0

        while index < len(entries):
            entry = entries[index]
            index += 1

            if not isinstance(entry, dict):
                continue

            graph = entry.get("@graph")
            if isinstance(graph, list):
                entries.extend(item for item in graph if isinstance(item, dict))

            aggregate = entry.get("aggregateRating")
            if isinstance(aggregate, dict):
                rating = parse_rating(aggregate.get("ratingValue"))
                if rating is not None:
                    return rating

    return None


# ============================================================
# PLAYWRIGHT SCRAPER
# Runs in a separate process, following the SoulFlower spider pattern.
# ============================================================

async def scrape_zop_async():
    results = []
    seen_links = set()
    category_counts = {}
    skipped_invalid_price = 0
    skipped_no_discount = 0
    detail_failures = 0
    listing_pages_loaded = 0
    detail_pages_attempted = 0
    detail_pages_loaded = 0

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        listing_page = await context.new_page()
        listing_page.set_default_navigation_timeout(PAGE_TIMEOUT)

        try:
            for category, urls in BASE_URLS.items():
                logging.info("Scraping ZOP category: %s", category)
                category_results = []

                for base_url in urls:
                    page_num = 1

                    while page_num <= MAX_PAGES:
                        listing_url = f"{base_url}?page={page_num}"
                        logging.info("Fetching listing: %s", listing_url)

                        if page_num > 1:
                            await listing_page.wait_for_timeout(
                                LISTING_PAGE_DELAY * 1000
                            )

                        response = None
                        for attempt in range(1, MAX_429_RETRIES + 1):
                            try:
                                response = await listing_page.goto(
                                    listing_url,
                                    wait_until="domcontentloaded",
                                    timeout=PAGE_TIMEOUT,
                                )
                                status = response.status if response else None

                                if status == 429:
                                    if attempt < MAX_429_RETRIES:
                                        logging.warning(
                                            "HTTP 429 for listing. Waiting %s seconds "
                                            "before retry: %s",
                                            RATE_LIMIT_DELAY,
                                            listing_url,
                                        )
                                        await listing_page.wait_for_timeout(
                                            RATE_LIMIT_DELAY * 1000
                                        )
                                        continue

                                    logging.error(
                                        "Listing returned HTTP 429 after retries: %s",
                                        listing_url,
                                    )
                                    response = None
                                    break

                                break
                            except Exception as exc:
                                logging.warning(
                                    "Listing request attempt %s failed for %s: %s",
                                    attempt,
                                    listing_url,
                                    exc,
                                )
                                if attempt < MAX_429_RETRIES:
                                    await listing_page.wait_for_timeout(
                                        LISTING_PAGE_DELAY * 1000
                                    )
                                else:
                                    response = None

                        if response is None:
                            logging.warning("Unable to fetch listing: %s", listing_url)
                            break

                        listing_pages_loaded += 1
                        logging.info(
                            "[LISTING PAGE LOADED] status=%s | page=%s | url=%s",
                            response.status if response else "unknown",
                            page_num,
                            listing_url,
                        )

                        try:
                            await listing_page.wait_for_selector(
                                "div.product-card",
                                timeout=PRODUCT_SELECTOR_TIMEOUT,
                            )
                        except Exception:
                            logging.warning(
                                "No product cards found on listing: %s",
                                listing_url,
                            )
                            break

                        listing_html = await listing_page.content()
                        soup = BeautifulSoup(listing_html, "html.parser")
                        cards = soup.select("div.product-card")

                        logging.info(
                            "Found %s product cards on page %s",
                            len(cards),
                            page_num,
                        )

                        if not cards:
                            break

                        for card in cards:
                            try:
                                name_tag = card.select_one("h3.product-info-title a")
                                name = (
                                    name_tag.get_text(" ", strip=True)
                                    if name_tag
                                    else None
                                )
                                product_link = get_product_link(card, base_url)

                                if not name or not product_link:
                                    logging.info(
                                        "Skipping card with missing name or URL."
                                    )
                                    continue

                                dedupe_key = (
                                    product_link.split("?")[0].rstrip("/").lower()
                                )
                                if dedupe_key in seen_links:
                                    continue

                                # Parse listing prices before any PDP request.
                                price_tag = card.select_one("span.productprice")
                                original_tag = card.select_one(
                                    "span.productcompare-at-price"
                                )

                                price = parse_money(
                                    price_tag.get_text(" ", strip=True)
                                    if price_tag
                                    else None
                                )
                                original_price = parse_money(
                                    original_tag.get_text(" ", strip=True)
                                    if original_tag
                                    else None
                                )

                                # REQUIRED: skip before opening PDP if either price is missing.
                                if price is None or original_price is None:
                                    skipped_invalid_price += 1
                                    logging.info(
                                        "[SKIP BEFORE DETAIL] %s: missing/invalid "
                                        "current price or original price",
                                        name,
                                    )
                                    seen_links.add(dedupe_key)
                                    continue

                                discount = calculate_discount(original_price, price)
                                if discount <= 0:
                                    skipped_no_discount += 1
                                    logging.info(
                                        "[SKIP BEFORE DETAIL] %s: no positive discount "
                                        "(price=%s, original_price=%s)",
                                        name,
                                        price,
                                        original_price,
                                    )
                                    seen_links.add(dedupe_key)
                                    continue

                                image_link = get_image_url(card, base_url)

                                # Only discounted products reach this detail-page step.
                                await listing_page.wait_for_timeout(
                                    DETAIL_PAGE_DELAY * 1000
                                )

                                detail_page = await context.new_page()
                                detail_page.set_default_navigation_timeout(
                                    PAGE_TIMEOUT
                                )

                                detail_response = None
                                try:
                                    for attempt in range(1, MAX_429_RETRIES + 1):
                                        try:
                                            detail_pages_attempted += 1
                                            detail_response = await detail_page.goto(
                                                product_link,
                                                wait_until="domcontentloaded",
                                                timeout=PAGE_TIMEOUT,
                                            )
                                            status = (
                                                detail_response.status
                                                if detail_response
                                                else None
                                            )

                                            if status == 429:
                                                if attempt < MAX_429_RETRIES:
                                                    logging.warning(
                                                        "HTTP 429 on detail page. "
                                                        "Waiting %s seconds: %s",
                                                        RATE_LIMIT_DELAY,
                                                        product_link,
                                                    )
                                                    await detail_page.wait_for_timeout(
                                                        RATE_LIMIT_DELAY * 1000
                                                    )
                                                    continue

                                                detail_response = None
                                            break
                                        except Exception as exc:
                                            logging.warning(
                                                "Detail attempt %s failed for %s: %s",
                                                attempt,
                                                product_link,
                                                exc,
                                            )
                                            if attempt < MAX_429_RETRIES:
                                                await detail_page.wait_for_timeout(
                                                    DETAIL_PAGE_DELAY * 1000
                                                )

                                    if detail_response is None:
                                        detail_failures += 1
                                        logging.warning(
                                            "Detail page failed: %s",
                                            product_link,
                                        )
                                        continue

                                    detail_pages_loaded += 1
                                    logging.info(
                                        "[DETAIL PAGE LOADED] status=%s | url=%s",
                                        detail_response.status if detail_response else "unknown",
                                        product_link,
                                    )

                                    try:
                                        await detail_page.wait_for_timeout(1500)
                                        detail_html = await detail_page.content()
                                        detail_soup = BeautifulSoup(
                                            detail_html, "html.parser"
                                        )
                                        description = get_description(
                                            detail_soup, name
                                        )
                                        rating = get_detail_rating(detail_soup)

                                        if not image_link:
                                            image_link = get_image_url(
                                                detail_soup, product_link
                                            )
                                    except Exception as exc:
                                        detail_failures += 1
                                        logging.warning(
                                            "Could not parse detail page %s: %s",
                                            product_link,
                                            exc,
                                        )
                                        continue
                                finally:
                                    await detail_page.close()

                                item = {
                                    "created_at": datetime.now().isoformat(timespec="seconds"),
                                    "name": str(name),
                                    "price": price,
                                    "currency": "₹",
                                    "original_price": original_price,
                                    "discount": int(discount),
                                    "ratings": rating,
                                    "description": str(description or name),
                                    "image_link": image_link,
                                    "product_link": str(product_link),
                                    "organization_id": "Dealwallet",
                                    "store_id": "ZOP",
                                    "categories_id": str(category),
                                }

                                results.append(item)
                                category_results.append(item)
                                seen_links.add(dedupe_key)

                                logging.info(
                                    "[SCRAPED] %s | ₹%s | MRP ₹%s | %s%% OFF",
                                    name,
                                    price,
                                    original_price,
                                    discount,
                                )

                            except Exception:
                                logging.exception("Failed to process a ZOP product card.")

                        page_num += 1

                category_counts[category] = len(category_results)
                logging.info(
                    "Discounted products scraped for '%s': %s",
                    category,
                    len(category_results),
                )

        finally:
            await listing_page.close()
            await context.close()
            await browser.close()

    logging.info("=" * 60)
    logging.info("ZOP scraper summary")
    logging.info("Total discounted products: %s", len(results))
    logging.info("Playwright listing pages loaded: %s", listing_pages_loaded)
    logging.info("Playwright detail navigation attempts: %s", detail_pages_attempted)
    logging.info("Playwright detail pages loaded: %s", detail_pages_loaded)
    for category, count in category_counts.items():
        logging.info(" - %s: %s products", category, count)
    logging.info("Skipped before detail (invalid/missing prices): %s", skipped_invalid_price)
    logging.info("Skipped before detail (no discount): %s", skipped_no_discount)
    logging.info("Detail page failures: %s", detail_failures)
    logging.info("=" * 60)

    return results


def run_zop_scraper():
    """Synchronous entry point for ProcessPoolExecutor."""
    return asyncio.run(scrape_zop_async())


# ============================================================
# SCRAPY SPIDER — patterned after SoulFlowerSpider
# ============================================================

class ZOPSpider(scrapy.Spider):
    name = "zop_store"

    # This spider performs page navigation with Playwright rather than Scrapy
    # Requests, so Scrapy's LogStats "Crawled 0 pages" message is misleading.
    # Use explicit Playwright page counters logged by scrape_zop_async().
    custom_settings = {
        "LOG_LEVEL": "INFO",
        "EXTENSIONS": {
            "scrapy.extensions.logstats.LogStats": None,
        },
    }

    allowed_domains = [
        "zop.in",
        "www.zop.in",
    ]

    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting ZOP Playwright scraper...")
        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        # Isolate Playwright's event loop from Scrapy's reactor loop,
        # matching the structure used in SoulFlowerSpider.
        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_zop_scraper,
            )

        # Save a complete JSON copy, like the SoulFlower spider.
        output_path = os.path.abspath(OUTPUT_FILE)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(products, file, ensure_ascii=False, indent=4)

        self.logger.info("Saved JSON output to: %s", output_path)
        self.logger.info("ZOP scraper returned %s products.", len(products))

        yielded = 0
        for product in products:
            if not product.get("product_link"):
                continue
            if not product.get("image_link"):
                self.logger.warning(
                    "Skipping item without image URL: %s",
                    product.get("name"),
                )
                continue

            self.logger.info(
                "[ITEM YIELDED] %s | price=%s | discount=%s%% | url=%s",
                product.get("name"),
                product.get("price"),
                product.get("discount"),
                product.get("product_link"),
            )
            yield product
            yielded += 1

        self.logger.info("ZOP spider completed. Items yielded: %s", yielded)


# Allows `scrapy crawl zop_store` to load the spider from this file.
