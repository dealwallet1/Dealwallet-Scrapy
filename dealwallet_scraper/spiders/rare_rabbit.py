import asyncio
import json
import logging
import multiprocessing
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit, urljoin

from bs4 import BeautifulSoup
from scrapy import Spider


BASE_URLS = {
    # "Shirts": [
    #     "https://thehouseofrare.com/collections/rare-rabbit-shirt",
    #     "https://thehouseofrare.com/collections/formal-shirt",
    #     "https://thehouseofrare.com/collections/rare-rabbit-shirt?page=2",
    # ],
    "Fashion & Lifestyle": [
        "https://thehouseofrare.com/collections/rareism-dress",
        "https://thehouseofrare.com/collections/rareism-top",
        "https://thehouseofrare.com/collections/rareism-shirts",
        "https://thehouseofrare.com/collections/rareism-t-shirt",
        "https://thehouseofrare.com/collections/rareism-polo",
        "https://thehouseofrare.com/collections/rareism-co-ords",
        "https://thehouseofrare.com/collections/rareism-trouser",
        "https://thehouseofrare.com/collections/rareism-denim",
        "https://thehouseofrare.com/collections/rareism-skirt",
        "https://thehouseofrare.com/collections/rareism-short",
        "https://thehouseofrare.com/collections/rareism-blazer",
        "https://thehouseofrare.com/collections/rareism-track-pant",
        "https://thehouseofrare.com/collections/rareism-sweater",
        "https://thehouseofrare.com/collections/rareism-shrug-overlay",
        "https://thehouseofrare.com/collections/rareism-sweatshirt-1",
        "https://thehouseofrare.com/collections/rareism-jackets",
        "https://thehouseofrare.com/collections/rareism-coat",
    ],
    # "Shoes": [
    #     "https://thehouseofrare.com/collections/rare-rr-men-shoes"
    # ],
    # "Baby & Kids": [
    #     "https://thehouseofrare.com/collections/rare-ones-kids-all-products"
    # ],
}

MAX_PAGES = 2
TEST_MODE = True
TEST_LIMIT = 1000

# Faster collection/detail crawling. Keep concurrency moderate to avoid rate limits.
REQUEST_DELAY = 0.1
RETRY_DELAY = 5
RETRY_COUNT = 0
DETAIL_CONCURRENCY = 3
PAGE_TIMEOUT = 25000

STORE_ID = "Rare Rabbit"
ORGANIZATION_ID = "Dealwallet"
CURRENCY = "₹"

COLLECTION_CARD_SELECTOR = "div.collection-product-main.collection-product-main-js"

OUTPUT_FIELDS = (
    "name",
    "price",
    "currency",
    "original_price",
    "discount",
    "ratings",
    "description",
    "image_link",
    "product_link",
    "organization_id",
    "store_id",
    "categories_id",
    "created_at",
)

WORKER_LOGGER = logging.getLogger("RARE-RABBIT-WORKER")


def clean_text(value):
    if not value:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    if hasattr(value, "get_text"):
        text = value.get_text(" ", strip=True)
    else:
        text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_price(value):
    if value is None:
        return None
    value = str(value).strip().replace(",", "")
    value = re.sub(r"[^\d.]", "", value)
    if not value:
        return None
    try:
        return int(round(float(value)))
    except (ValueError, TypeError):
        return None


def normalize_shopify_price(value):
    price = parse_price(value)
    if price is None:
        return None
    # Shopify product.js prices are usually in paise when represented as large integers.
    if price >= 100000:
        return int(round(price / 100))
    return price


def make_page_url(base_url, page_number):
    parts = urlsplit(base_url)
    query = parse_qs(parts.query)
    query["page"] = [str(page_number)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, doseq=True), parts.fragment))


def absolute_url(url):
    if not url:
        return ""
    return urljoin("https://thehouseofrare.com", str(url).strip())


def extract_image(card):
    image = (
        card.select_one(".image-link img")
      
    )
    if image:
        return image.get("data-src") or image.get("src") or image.get("data-original") or ""
    quick_add = card.select_one(".quick-add-button")
    if quick_add:
        return quick_add.get("data-image", "")
    return card.get("data-images", "")


def extract_discount(card, price, original_price):
    """Return a positive discount percentage, or None when not discounted."""
    discount_element = card.select_one(".perc_price")
    if discount_element:
        match = re.search(r"(\d+(?:\.\d+)?)", clean_text(discount_element))
        if match:
            badge_discount = int(float(match.group(1)))
            if badge_discount > 0:
                return badge_discount

    # Calculate from the displayed prices if the badge is absent or says 0%.
    if price is not None and original_price is not None and original_price > price > 0:
        calculated = int(round(((original_price - price) / original_price) * 100))
        if calculated > 0:
            return calculated
    return None


def extract_collection_product(card, category):
    quick_add = card.select_one(".quick-add-button")
    title_element = card.select_one(".product-title-wishlist .main-title")
    subtitle_element = card.select_one(".product-title-wishlist .sub-title")
    product_link_element = card.select_one(".product-link")

    product_url = card.get("data-product-url") or (
        product_link_element.get("href") if product_link_element else ""
    )
    if not product_url:
        return None
    product_url = absolute_url(product_url)

    full_title = clean_text(quick_add.get("data-title")) if quick_add else ""
    if not full_title:
        size_element = card.select_one(".product-card__sizes li[product-title]")
        if size_element:
            full_title = clean_text(size_element.get("product-title"))
    if not full_title:
        full_title = clean_text(title_element)
        subtitle = clean_text(subtitle_element)
        if subtitle:
            full_title = f"{full_title} {subtitle}".strip()
    if not full_title:
        return None

    price = parse_price(quick_add.get("data-price")) if quick_add else None
    original_price = parse_price(quick_add.get("data-compare-at")) if quick_add else None

    if price is None:
        price_element = card.select_one(".regular-price .money")
        price = parse_price(clean_text(price_element))
    if original_price is None:
        original_element = card.select_one(".compare-price .money")
        original_price = parse_price(clean_text(original_element))

    discount = extract_discount(card, price, original_price)
    image_link = extract_image(card)

    return {
        "name": full_title,
        "price": price,
        "currency": CURRENCY,
        "original_price": original_price,
        "discount": discount,
        "ratings": None,
        "description": "",
        "image_link": absolute_url(image_link),
        "product_link": product_url,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": category,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_product_cards(html):
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(COLLECTION_CARD_SELECTOR)
    if not cards:
        WORKER_LOGGER.warning("Primary product-card selector returned zero results; trying fallback selectors.")
        cards = soup.select("div[data-product-url][data-main-product]")
    if not cards:
        cards = soup.select("div.collection-product-main")
    if not cards:
        cards = soup.select("[data-product-url]")
    return cards


def extract_ratings(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
            objects = data if isinstance(data, list) else [data]
            index = 0
            while index < len(objects):
                obj = objects[index]
                index += 1
                if not isinstance(obj, dict):
                    continue
                graph = obj.get("@graph")
                if isinstance(graph, list):
                    objects.extend(item for item in graph if isinstance(item, dict))
                aggregate = obj.get("aggregateRating")
                if isinstance(aggregate, dict) and aggregate.get("ratingValue") is not None:
                    try:
                        return float(aggregate["ratingValue"])
                    except (ValueError, TypeError):
                        pass
        except (json.JSONDecodeError, TypeError):
            continue

    rating_element = soup.select_one(".jdgm-prev-badge")
    if rating_element:
        rating_value = rating_element.get("data-average-rating") or rating_element.get("data-rating")
        if rating_value:
            try:
                return float(rating_value)
            except (ValueError, TypeError):
                pass
    return None


def extract_description_from_detail(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    description_selectors = (
        "div.content-wrapper.desktop-tab-content-clamped",
        ".product-description",
        ".product__description",
        "#description",
        "[itemprop='description']",
    )
    for css in description_selectors:
        element = soup.select_one(css)
        if element:
            paragraph = element.find("p")
            description = clean_text(paragraph if paragraph else element)
            if description:
                return description

    # Look for the product JSON object without assuming a fixed whitespace pattern.
    for pattern in (
        r"const\s+__wzrk_product_json\s*=\s*(\{.*?\})\s*;",
        r"__wzrk_product_json\s*=\s*(\{.*?\})\s*;",
    ):
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                description = clean_text(json.loads(match.group(1)).get("description", ""))
                if description:
                    return description
            except (json.JSONDecodeError, TypeError):
                pass

    for selector, attribute in (
        ('meta[name="twitter:description"]', "content"),
        ('meta[property="og:description"]', "content"),
    ):
        element = soup.select_one(selector)
        if element:
            description = clean_text(element.get(attribute, ""))
            if description:
                return description
    return ""


def extract_shopify_json(data):
    if not isinstance(data, dict):
        return {}
    description = clean_text(data.get("description", ""))
    images = data.get("images") or []
    image_link = ""
    if images:
        first_image = images[0]
        image_link = first_image.get("src", "") if isinstance(first_image, dict) else str(first_image)

    variant_price = None
    variant_original_price = None
    for variant in data.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        current_price = normalize_shopify_price(variant.get("price"))
        current_original = normalize_shopify_price(variant.get("compare_at_price"))
        if current_price is not None:
            variant_price = current_price
            variant_original_price = current_original
            break
    return {
        "description": description,
        "image_link": image_link,
        "price": variant_price,
        "original_price": variant_original_price,
    }


def extract_product_json(html):
    if not html:
        return {}
    try:
        return extract_shopify_json(json.loads(html))
    except (json.JSONDecodeError, TypeError):
        return {}


async def crawl_page(crawler, url):
    from crawl4ai import CacheMode, CrawlerRunConfig

    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=PAGE_TIMEOUT,
        delay_before_return_html=0,
        remove_overlay_elements=True,
        verbose=False,
    )

    for attempt in range(RETRY_COUNT + 1):
        try:
            result = await crawler.arun(url=url, config=config)
            status_code = getattr(result, "status_code", None)
            if status_code == 429:
                WORKER_LOGGER.warning("429 rate limit for %s (attempt %s/%s)", url, attempt + 1, RETRY_COUNT + 1)
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None

            if not getattr(result, "success", False):
                WORKER_LOGGER.warning("Crawl failed for %s: %s", url, getattr(result, "error_message", "Unknown error"))
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                return None

            html = getattr(result, "html", None) or getattr(result, "cleaned_html", None) or ""
            if not html:
                WORKER_LOGGER.warning("No HTML returned for %s", url)
                return None
            return html
        except Exception:
            WORKER_LOGGER.exception("Error crawling %s", url)
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    return None


async def fetch_collection_products(crawler, category, collection_url):
    products = []
    seen_products = set()
    base_parts = urlsplit(collection_url)
    original_query = parse_qs(base_parts.query)
    requested_start_page = int(original_query.get("page", [1])[0])

    for offset in range(MAX_PAGES):
        page_number = requested_start_page + offset
        page_url = make_page_url(collection_url, page_number)
        WORKER_LOGGER.info("[%s] Fetching collection page %s/%s: %s", category, offset + 1, MAX_PAGES, page_url)

        html = await crawl_page(crawler, page_url)
        if not html:
            WORKER_LOGGER.warning("Unable to fetch collection page: %s", page_url)
            break

        cards = extract_product_cards(html)
        if not cards:
            WORKER_LOGGER.warning("No product cards found on %s", page_url)
            Path(f"rare_rabbit_debug_page_{page_number}.html").write_text(html, encoding="utf-8")
            break

        page_products = 0
        skipped_no_discount = 0
        for card in cards:
            product = extract_collection_product(card, category)
            if not product:
                continue

            # IMPORTANT: filter here, before enrich_product() can request a detail page.
            if not product.get("discount") or product["discount"] <= 0:
                skipped_no_discount += 1
                continue

            unique_key = product["product_link"]
            if unique_key in seen_products:
                continue
            seen_products.add(unique_key)
            products.append(product)
            page_products += 1

        WORKER_LOGGER.info(
            "[%s] Page %s: %s discounted products kept, %s products skipped without discount",
            category, page_number, page_products, skipped_no_discount,
        )
        if not cards:
            break
        await asyncio.sleep(REQUEST_DELAY)
    return products


async def enrich_product(crawler, product, semaphore):
    product_url = product.get("product_link")
    if not product_url:
        return product

    async with semaphore:
        WORKER_LOGGER.info("Opening detail page: %s", product.get("name"))
        detail_html = await crawl_page(crawler, product_url)
        if detail_html:
            product["ratings"] = extract_ratings(detail_html)
            product["description"] = extract_description_from_detail(detail_html)
        else:
            WORKER_LOGGER.warning("Detail page could not be loaded: %s", product_url)

        # Only use Shopify JSON as a fallback when the detail page has no description.
        if not product.get("description"):
            json_url = product_url.rstrip("/") + ".js"
            json_html = await crawl_page(crawler, json_url)
            if json_html:
                details = extract_product_json(json_html)
                product["description"] = details.get("description") or ""
                if not product.get("image_link"):
                    product["image_link"] = absolute_url(details.get("image_link"))
                if product.get("price") is None:
                    product["price"] = details.get("price")
                if product.get("original_price") is None:
                    product["original_price"] = details.get("original_price")

        if not product.get("description"):
            WORKER_LOGGER.warning("Description missing: %s", product_url)

        price = product.get("price")
        original_price = product.get("original_price")
        if (not product.get("discount") and price is not None and original_price and original_price > price):
            product["discount"] = int(round(((original_price - price) / original_price) * 100))

        product["image_link"] = absolute_url(product.get("image_link"))
        WORKER_LOGGER.info("Finished detail page: %s", product.get("name"))
        return product


async def scrape_all_products():
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    browser_config = BrowserConfig(browser_type="chromium", headless=True, verbose=False)
    all_products = []
    seen_products = set()
    products_attempted = 0
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

    WORKER_LOGGER.info("Starting Rare Rabbit browser worker")
    WORKER_LOGGER.info("Detail-page concurrency: %s", DETAIL_CONCURRENCY)
    if TEST_MODE:
        WORKER_LOGGER.info("TEST MODE ENABLED | Maximum discounted products: %s", TEST_LIMIT)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        stop_scraping = False
        for category, collection_urls in BASE_URLS.items():
            if stop_scraping:
                break

            category_products = []
            for collection_url in collection_urls:
                collection_products = await fetch_collection_products(crawler, category, collection_url)
                category_products.extend(collection_products)

            # Remove duplicates across collection URLs before detail-page requests.
            unique_category_products = []
            for product in category_products:
                link = product.get("product_link")
                if link and link not in seen_products:
                    seen_products.add(link)
                    unique_category_products.append(product)

            WORKER_LOGGER.info("[%s] Discounted unique products found: %s", category, len(unique_category_products))

            if TEST_MODE:
                remaining = TEST_LIMIT - products_attempted
                if remaining <= 0:
                    stop_scraping = True
                    break
                unique_category_products = unique_category_products[:remaining]

            tasks = []
            for index, product in enumerate(unique_category_products, start=1):
                products_attempted += 1
                WORKER_LOGGER.info(
                    "[%s] Queued discounted product %s/%s (overall %s): %s",
                    category, index, len(unique_category_products), products_attempted, product.get("name"),
                )
                tasks.append(enrich_product(crawler, product, semaphore))

            # Consume results as each product finishes so progress is visible immediately.
            tasks = [asyncio.create_task(task) for task in tasks]
            category_exported = 0
            completed = 0
            for completed_task in asyncio.as_completed(tasks):
                completed += 1
                try:
                    result = await completed_task
                except Exception:
                    WORKER_LOGGER.exception(
                        "Product enrichment failed (%s/%s)",
                        completed, len(tasks),
                    )
                    continue

                all_products.append({field: result.get(field) for field in OUTPUT_FIELDS})
                category_exported += 1
                WORKER_LOGGER.info(
                    "[%s] Detail progress: %s/%s completed",
                    category, completed, len(tasks),
                )

            WORKER_LOGGER.info("[%s] Products exported: %s", category, category_exported)
            if TEST_MODE and products_attempted >= TEST_LIMIT:
                stop_scraping = True

    WORKER_LOGGER.info("=" * 60)
    WORKER_LOGGER.info("RARE RABBIT SCRAPING SUMMARY")
    WORKER_LOGGER.info("Discounted products attempted: %s", products_attempted)
    WORKER_LOGGER.info("Unique products exported: %s", len(all_products))
    WORKER_LOGGER.info("Test mode: %s", TEST_MODE)
    return all_products


def run_browser_worker():
    if multiprocessing.current_process().name == "MainProcess":
        raise RuntimeError("Browser worker must run in a separate process")

    if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [RARE-RABBIT-WORKER] %(levelname)s %(message)s",
    )
    try:
        return asyncio.run(scrape_all_products())
    except Exception:
        WORKER_LOGGER.exception("Rare Rabbit browser worker failed")
        return []


class RareRabbitSpider(Spider):
    name = "rare_rabbit"

    custom_settings = {
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0,
        "DOWNLOAD_TIMEOUT": 60,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    async def start(self):
        self.logger.info("Starting Rare Rabbit Scrapy spider")
        self.logger.info("Using a separate Crawl4AI browser process")
        loop = asyncio.get_running_loop()
        multiprocessing.freeze_support()

        with ProcessPoolExecutor(
            max_workers=1,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            products = await loop.run_in_executor(executor, run_browser_worker)

        self.logger.info("Browser worker returned %s products", len(products))
        sent_count = 0
        for product in products:
            if not product:
                continue
            yield product
            sent_count += 1

        self.logger.info("Rare Rabbit spider completed. Products sent to pipeline: %s", sent_count)
