import asyncio
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin
import json
import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIGURATION
# ============================================================


BASE_URL = "https://www.myntra.com"

BASE_URLS = {"Fashion & Lifestyle": ["https://www.myntra.com/men-topwear", "https://www.myntra.com/women-topwear"],
    "Bags": ["https://www.myntra.com/bags"],
    "Computers & Accessories": ["https://www.myntra.com/computer-accessories"],
    "Electronics": ["https://www.myntra.com/electronics"],
    "Furnitures": ["https://www.myntra.com/furnitures"],
    "Gym Equipments": ["https://www.myntra.com/gym-sports-accessories"],
    "Home Appliances": ["https://www.myntra.com/home-appliences"],
    "Home Decor": ["https://www.myntra.com/homedecor"],
    "Makeup": ["https://www.myntra.com/makeup"],
    "Mobile Accessories": ["https://www.myntra.com/mobile-accessories"],
    "Shirts": ["https://www.myntra.com/shirts"],
    "Sports": ["https://www.myntra.com/sports-equipments"],
    "Beauty": ["https://www.myntra.com/personal-care"],
    "Travel": ["https://www.myntra.com/travelling-trolley"],
    "Toys & Games": ["https://www.myntra.com/toys"],

}


MAX_PAGES = 10
PAGE_START = 1

LISTING_PAGE_WAIT = 3
DETAIL_PAGE_WAIT = 2
PAGE_TIMEOUT = 60000
MAX_CRAWL_RETRIES = 2
RETRY_DELAY = 10

CATEGORIES = list(BASE_URLS.keys())


def normalize_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text)).strip()


def extract_int(text):
    if not text:
        return None

    match = re.search(r"[\d,]+", str(text))

    if not match:
        return None

    try:
        return int(match.group(0).replace(",", ""))
    except (ValueError, TypeError):
        return None


def extract_float(text):
    if not text:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(text)
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except (ValueError, TypeError):
        return None


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def extract_image_url(product_element):
    """
    Extract product image URL from Myntra listing.
    """

    image = product_element.select_one(
        "picture img"
    )

    if not image:
        image = product_element.select_one(
            "img"
        )

    if not image:
        return None

    # --------------------------------------------------------
    # Direct image attributes
    # --------------------------------------------------------

    attributes = [
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
    ]

    for attribute in attributes:

        value = image.get(attribute)

        if not value:
            continue

        value = value.strip()

        if not value:
            continue

        if value.startswith("//"):
            return "https:" + value

        if value.startswith("http://"):
            return value

        if value.startswith("https://"):
            return value

        if value.startswith("/"):
            return urljoin(
                BASE_URL,
                value
            )

    # --------------------------------------------------------
    # SRCSET
    # --------------------------------------------------------

    srcset = (
        image.get("srcset")
        or image.get("data-srcset")
    )

    if srcset:

        candidates = []

        for item in srcset.split(","):

            item = item.strip()

            if not item:
                continue

            parts = item.split()

            if not parts:
                continue

            candidates.append(
                parts[0]
            )

        if candidates:

            image_url = candidates[-1]

            if image_url.startswith("//"):
                return "https:" + image_url

            if image_url.startswith("http://"):
                return image_url

            if image_url.startswith("https://"):
                return image_url

            return urljoin(
                BASE_URL,
                image_url
            )

    return None


# ============================================================
# PRODUCT URL
# ============================================================

def extract_product_url(product_element):

    link = product_element.select_one(
        "a"
    )

    if not link:
        return None

    href = link.get("href")

    if not href:
        return None

    return urljoin(
        BASE_URL,
        href
    )


# ============================================================
# RATING
# ============================================================

def extract_rating(product_element):

    rating = None

    rating_span = product_element.select_one(
        "div.product-ratingsContainer span"
    )

    if rating_span:

        rating = extract_float(
            rating_span.get_text(strip=True)
        )

    return rating


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(product_element):

    # Myntra product title
    title_element = product_element.select_one(
        ".product-product"
    )

    if title_element:

        title = normalize_text(
            title_element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    # Fallback
    title_element = product_element.select_one(
        ".product-productMetaInfo"
    )

    if title_element:

        title = normalize_text(
            title_element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    return ""


# ============================================================
# LISTING PRODUCT PARSER
# ============================================================

def parse_listing_product(
    product_element,
    category
):

    # --------------------------------------------------------
    # NAME
    # --------------------------------------------------------

    name = extract_product_name(
        product_element
    )

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_element = product_element.select_one(
        ".product-discountedPrice"
    )

    if not price_element:

        price_element = product_element.select_one(
            ".product-price"
        )

    price = extract_int(
        price_element.get_text(
            " ",
            strip=True
        )
        if price_element
        else None
    )

    # --------------------------------------------------------
    # ORIGINAL PRICE
    # --------------------------------------------------------

    original_price_element = (
        product_element.select_one(
            ".product-strike"
        )
    )

    original_price = extract_int(
        original_price_element.get_text(
            " ",
            strip=True
        )
        if original_price_element
        else None
    )

    # --------------------------------------------------------
    # DISCOUNT
    # --------------------------------------------------------

    discount_element = (
        product_element.select_one(
            ".product-discountPercentage"
        )
    )

    discount = extract_int(
        discount_element.get_text(
            " ",
            strip=True
        )
        if discount_element
        else None
    )

    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    image_link = extract_image_url(
        product_element
    )

    # IMPORTANT:
    # Skip products without an image.
    if not image_link:
        return None

    # --------------------------------------------------------
    # PRODUCT URL
    # --------------------------------------------------------

    product_link = extract_product_url(
        product_element
    )

    if not product_link:
        return None

    # --------------------------------------------------------
    # RATING
    # --------------------------------------------------------

    ratings = extract_rating(
        product_element
    )

    # --------------------------------------------------------
    # PRODUCT
    # --------------------------------------------------------

    return {
        "name": name,
        "price": price,
        "currency": "₹",
        "original_price": original_price,
        "discount": discount,
        "ratings": ratings,
        "description": None,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": "Dealwallet",
        "store_id": "Myntra",
        "categories_id": category,
        "created_at": datetime.now(
    timezone(timedelta(hours=5, minutes=30))
).strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ============================================================
# DETAIL PAGE DESCRIPTION
# ============================================================

def parse_detail_page(html):

    if not html:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    selectors = [
        "p.pdp-product-description-content",
        ".pdp-product-description-content",
        ".pdp-product-description",
        "[class*='description-content']",
    ]

    for selector in selectors:

        element = soup.select_one(
            selector
        )

        if not element:
            continue

        description = normalize_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if description:
            return description

    return None


# ============================================================
# CRAWL4AI ASYNC SCRAPER
# ============================================================

async def open_myntra_page(crawler, url, page_type="page"):
    """Open a Myntra page with retries and detailed Crawl4AI diagnostics."""
    for attempt in range(1, MAX_CRAWL_RETRIES + 1):
        try:
            logging.info("Opening Myntra %s (attempt %s/%s): %s", page_type, attempt, MAX_CRAWL_RETRIES, url)
            result = await crawler.arun(url=url)
            logging.info("Crawl4AI result | type=%s | success=%s | url=%s", page_type, getattr(result, "success", None), getattr(result, "url", None))
            if result.success:
                html = result.html or ""
                logging.info("Myntra %s HTML length: %s", page_type, len(html))
                return result
            logging.error("Myntra %s crawl unsuccessful | error=%s", page_type, getattr(result, "error_message", None))
        except Exception as exc:
            logging.exception("Myntra %s crawl exception on attempt %s/%s: %s", page_type, attempt, MAX_CRAWL_RETRIES, exc)
        if attempt < MAX_CRAWL_RETRIES:
            logging.info("Waiting %s seconds before retrying Myntra %s.", RETRY_DELAY, page_type)
            await asyncio.sleep(RETRY_DELAY)
    logging.error("Giving up Myntra %s after %s attempts: %s", page_type, MAX_CRAWL_RETRIES, url)
    return None


# ============================================================
# CRAWL4AI ASYNC SCRAPER
# ============================================================

async def scrape_myntra_async():
    all_products = []
    category_counts = {category: 0 for category in CATEGORIES}
    seen_products = set()
    seen_descriptions = set()

    logging.info("Myntra Crawl4AI: starting browser crawler.")

    async with AsyncWebCrawler(verbose=True) as crawler:
        # Same browser setup as the working WiseLife Crawl4AI scraper.
        crawler.browser_config = {
            "headless": True,
            "javascript": True,
        }
        # Keep DOMContentLoaded for Myntra; the longer timeout and delay
        # allow its product grid to render without waiting forever for networkidle.
        crawler.crawler_run_config = {
            "wait_until": "domcontentloaded",
            "timeout": PAGE_TIMEOUT,
            "delay_before_return_html": LISTING_PAGE_WAIT,
        }

        logging.info("Myntra browser config: %s", crawler.browser_config)
        logging.info("Myntra run config: %s", crawler.crawler_run_config)

        for category in CATEGORIES:
            base_url = BASE_URLS[category][0]
            category_start_count = len(all_products)
            logging.info("=" * 60)
            logging.info("Myntra category: %s", category)
            logging.info("Base URL: %s", base_url)
            logging.info("=" * 60)

            for page_number in range(PAGE_START, PAGE_START + MAX_PAGES):
                if page_number == 1:
                    listing_url = base_url
                else:
                    separator = "&" if "?" in base_url else "?"
                    listing_url = f"{base_url}{separator}p={page_number}"

                logging.info("Scraping Myntra listing page %s/%s: %s", page_number, PAGE_START + MAX_PAGES - 1, listing_url)
                result = await open_myntra_page(crawler, listing_url, "listing")
                if result is None:
                    continue

                html = result.html or ""
                if not html:
                    logging.warning("Myntra listing returned empty HTML: %s", listing_url)
                    continue

                soup = BeautifulSoup(html, "html.parser")
                li_count = len(soup.select("li.product-base"))
                fallback_count = len(soup.select(".product-base"))
                logging.info("Myntra listing diagnostics | HTML=%s | title=%s | li.product-base=%s | .product-base=%s", len(html), soup.title.get_text(strip=True) if soup.title else "NO TITLE", li_count, fallback_count)

                product_elements = soup.select("li.product-base") or soup.select(".product-base")
                logging.info("Myntra products found on page %s: %s", page_number, len(product_elements))
                if not product_elements:
                    logging.warning("NO MYNTRA PRODUCTS FOUND | category=%s | page=%s | url=%s", category, page_number, listing_url)
                    continue

                page_new_products = 0
                for product_element in product_elements:
                    product = parse_listing_product(product_element, category)
                    if not product:
                        continue
                    product_key = (product.get("product_link"), product.get("name"), product.get("price"))
                    if product_key in seen_products:
                        continue
                    seen_products.add(product_key)
                    all_products.append(product)
                    category_counts[category] += 1
                    page_new_products += 1

                logging.info("Myntra page %s complete | new products=%s | total products=%s", page_number, page_new_products, len(all_products))

            logging.info("Myntra category complete | %s | products=%s", category, len(all_products) - category_start_count)

    logging.info("=" * 60)
    logging.info("Myntra listing phase complete. Products collected: %s", len(all_products))
    logging.info("=" * 60)
    for category, count in category_counts.items():
        logging.info("Myntra category count | %s: %s products", category, count)

    if not all_products:
        logging.error("Myntra Crawl4AI collected ZERO products during listing phase.")
        return all_products

    async with AsyncWebCrawler(verbose=True) as crawler:
        crawler.browser_config = {
            "headless": True,
            "javascript": True,
        }
        crawler.crawler_run_config = {
            "wait_until": "domcontentloaded",
            "timeout": PAGE_TIMEOUT,
            "delay_before_return_html": DETAIL_PAGE_WAIT,
        }

        logging.info("Myntra detail browser config: %s", crawler.browser_config)
        logging.info("Myntra detail run config: %s", crawler.crawler_run_config)

        for index, product in enumerate(all_products, start=1):
            product_url = product.get("product_link")
            if not product_url:
                continue
            logging.info("Myntra detail page %s/%s: %s", index, len(all_products), product_url)
            result = await open_myntra_page(crawler, product_url, "detail")
            if result is None:
                product["description"] = None
                continue

            description = parse_detail_page(result.html)
            if description:
                description_key = normalize_text(description).lower()
                if description_key in seen_descriptions:
                    product["description"] = None
                else:
                    seen_descriptions.add(description_key)
                    product["description"] = description
            else:
                product["description"] = None

    logging.info("Myntra Crawl4AI completed. Total products: %s", len(all_products))
    return all_products


# ============================================================
# PROCESS RUNNER
# ============================================================

def run_myntra_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    logging.info("Myntra Crawl4AI process started.")
    logging.info("Python: %s", sys.version)
    logging.info("Platform: %s", sys.platform)

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    try:
        products = asyncio.run(scrape_myntra_async())
        logging.info("Myntra Crawl4AI process finished. Products returned: %s", len(products))
        return products
    except Exception:
        logging.exception("Myntra Crawl4AI process failed.")
        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class MyntraSpider(scrapy.Spider):

    name = "myntra_products"

    allowed_domains = [
        "myntra.com",
        "www.myntra.com",
    ]

    async def start(self):

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Starting Myntra Crawl4AI scraper..."
        )

        self.logger.info(
            "=" * 60
        )

        # ----------------------------------------------------
        # Run Crawl4AI in a separate process.
        #
        # This keeps the Windows asyncio handling compatible
        # with Scrapy + Scrapyd.
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:

                products = await loop.run_in_executor(
                    executor,
                    run_myntra_scraper
                )
        except Exception as exc:
            self.logger.exception(
                "Myntra Crawl4AI worker failed: %s",
                exc,
            )
            return

        # ----------------------------------------------------
        # Results
        # ----------------------------------------------------

        with open("scrape_myntra_final.json", "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)

        self.logger.info(
            "Crawl4AI returned %s products.",
            len(products)
        )

        # ----------------------------------------------------
        # Yield products
        # ----------------------------------------------------

        for product in products:

            # Final validation:
            # Never send a product without an image.
            if not product.get(
                "image_link"
            ):
                continue

            yield product

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Myntra spider completed."
        )

        self.logger.info(
            "=" * 60
        )
