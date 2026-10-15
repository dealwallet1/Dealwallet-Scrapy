import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from crawl4ai import AsyncWebCrawler
from parsel import Selector
import scrapy


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_FILE = PROJECT_ROOT / "scrape_yardley_final.json"

BASE_URLS = {
    "Beauty": [
        "https://yardleyoflondon.com/collections/fragrances",
        "https://yardleyoflondon.com/collections/fragrances_m",
        "https://yardleyoflondon.com/collections/personal-care_m",
        "https://yardleyoflondon.com/collections/hygiene-essentials",
        "https://yardleyoflondon.com/collections/shower-gel",
        "https://yardleyoflondon.com/collections/no-gas-body-perfume",
    ]
}

MAX_PAGES = 10
PAGE_TIMEOUT = 60000
RETRY_COUNT = 3
RETRY_DELAY = 5

logger = logging.getLogger(__name__)


def clean_text(value):
    if value is None:
        return "N/A"

    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or "N/A"


def extract_text(element):
    if element is None:
        return "N/A"

    try:
        return clean_text(element.xpath("string(.)").get())
    except Exception:
        return "N/A"


def extract_attribute(element, attribute):
    if element is None:
        return None

    try:
        value = element.attrib.get(attribute)
        return value.strip() if value else None
    except Exception:
        return None


def create_selector(html):
    if not html:
        return None

    try:
        return Selector(text=html)
    except Exception as exc:
        logger.error("Failed to create selector: %s", exc)
        return None


def normalize_url(url):
    if not url:
        return None

    return urljoin(
        "https://yardleyoflondon.com",
        str(url).strip(),
    )


def clean_product_url(url):
    if not url:
        return None

    url = normalize_url(url)

    if not url:
        return None

    try:
        parsed = urlsplit(url)
        path = parsed.path

        match = re.search(
            r"/collections/[^/]+(/products/[^/]+)$",
            path,
            flags=re.IGNORECASE,
        )

        if match:
            path = match.group(1)

        if "/products/" not in path:
            return None

        path = path.rstrip("/")

        return urlunsplit((
            parsed.scheme or "https",
            parsed.netloc or "yardleyoflondon.com",
            path,
            "",
            "",
        ))

    except Exception:
        return None


# ---------------------------------------------------------
# IMAGE EXTRACTION
# ---------------------------------------------------------

def normalize_image_url(url):
    if not url:
        return None

    url = str(url).strip()

    if not url or url.startswith("data:"):
        return None

    if url.startswith("//"):
        url = "https:" + url

    url = urljoin("https://yardleyoflondon.com", url)

    try:
        parsed = urlsplit(url)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    full_url = url.lower()

    # Accept Yardley's own image URLs and Shopify CDN image URLs.
    allowed_host = (
        host == "yardleyoflondon.com"
        or host.endswith(".yardleyoflondon.com")
        or host == "cdn.shopify.com"
    )

    if not allowed_host:
        return None

    # Explicitly reject logos and common non-product assets.
    rejected_terms = (
        "logo",
        "favicon",
        "placeholder",
        "no-image",
        "no_image",
        "tracking",
        "pixel",
        "sprite",
        "icon",
    )

    if any(term in path for term in rejected_terms):
        return None

    if any(term in full_url for term in (
        "facebook.com",
        "facebook.net",
        "tracking",
    )):
        return None

    # Reject URLs that do not look like image assets.
    image_extension = re.search(
        r"\.(?:jpe?g|png|webp|avif|gif)(?:$|[?#])",
        full_url,
        re.IGNORECASE,
    )

    shopify_image_path = (
        "/cdn/shop/files/" in path
        or "/cdn/shop/products/" in path
        or "/s/files/" in path
    )

    if not image_extension and not shopify_image_path:
        return None

    return url


def valid_yardley_image(url):
    return normalize_image_url(url)


def image_from_srcset(value):
    if not value:
        return None

    candidates = []

    for item in str(value).split(","):
        item = item.strip()

        if not item:
            continue

        parts = item.split()
        url = parts[0]

        normalized = valid_yardley_image(url)

        if not normalized:
            continue

        width_match = re.search(r"(\d+)w", item)
        width = int(width_match.group(1)) if width_match else 0

        candidates.append((width, normalized))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def extract_image(search_object):
    """
    Extract a product image without falling back to the site logo.

    Product-specific gallery/card selectors are checked first.
    Lazy-loaded attributes and srcset are supported.
    """

    if search_object is None:
        return None

    image_selectors = [
        # Product detail gallery selectors.
        ".product__media img",
        ".product__media-list img",
        ".product-gallery img",
        ".product-single__media img",
        ".product__image img",
        ".product-image img",

        # Product listing card selectors.
        "a.product-card__image img.first-image",
        "a.product-card__image img",
        ".product-card img",
        ".card__media img",
    ]

    for css in image_selectors:
        try:
            images = search_object.css(css)
        except Exception:
            continue

        for image in images:
            # Check lazy-loaded and normal image attributes.
            for attribute in (
                "data-src",
                "data-original",
                "data-lazy-src",
                "data-zoom",
                "src",
            ):
                value = extract_attribute(image, attribute)
                normalized = valid_yardley_image(value)

                if normalized:
                    return normalized

            # Check responsive image attributes.
            for attribute in ("srcset", "data-srcset"):
                value = extract_attribute(image, attribute)
                normalized = image_from_srcset(value)

                if normalized:
                    return normalized

    # Do not scan every image on the page.
    # Doing so can select the header logo or another unrelated image.
    return None


def extract_json_ld_image(product_data):
    if not isinstance(product_data, dict):
        return None

    image_data = product_data.get("image")

    if isinstance(image_data, str):
        return valid_yardley_image(image_data)

    if isinstance(image_data, dict):
        image_url = (
            image_data.get("url")
            or image_data.get("contentUrl")
        )
        return valid_yardley_image(image_url)

    if isinstance(image_data, list):
        for image_item in image_data:
            if isinstance(image_item, dict):
                image_item = (
                    image_item.get("url")
                    or image_item.get("contentUrl")
                )

            normalized = valid_yardley_image(image_item)

            if normalized:
                return normalized

    return None


def extract_listing_image(selector, card=None):
    # Prefer the image belonging to the specific product card.
    if card is not None:
        image = extract_image(card)

        if image:
            return image

    # Do not scan the whole listing page, as it may return the logo.
    return None


# ---------------------------------------------------------
# PRICE EXTRACTION
# ---------------------------------------------------------

def extract_number(value):
    if value is None:
        return None

    match = re.search(
        r"\d[\d,]*(?:\.\d+)?",
        str(value),
    )

    if not match:
        return None

    try:
        number = float(match.group(0).replace(",", ""))
        return int(number) if number.is_integer() else number
    except Exception:
        return None


def extract_all_numbers(value):
    if not value:
        return []

    numbers = []

    for match in re.findall(
        r"\d[\d,]*(?:\.\d+)?",
        str(value),
    ):
        try:
            number = float(match.replace(",", ""))
            numbers.append(
                int(number) if number.is_integer() else number
            )
        except Exception:
            pass

    return numbers


def calculate_discount(original_price, sale_price):
    if original_price is None or sale_price is None:
        return "N/A"

    try:
        original = float(original_price)
        sale = float(sale_price)

        if original <= 0 or sale < 0:
            return "N/A"

        if sale >= original:
            return 0

        return round(((original - sale) / original) * 100)

    except Exception:
        return "N/A"


def extract_price_from_elements(search_object, selectors):
    if search_object is None:
        return None

    for css in selectors:
        try:
            for element in search_object.css(css):
                number = extract_number(extract_text(element))

                if number is not None:
                    return number

        except Exception:
            pass

    return None


def extract_original_price(selector, card=None):
    if card is not None:
        price = extract_price_from_elements(
            card,
            ["s.product-card__regular-price"],
        )

        if price is not None:
            return price

    if selector is not None:
        detail_selectors = [
            "s.product-card__regular-price",
            "span.price--compare-at",
            "span.original-price",
            "s.price-item--regular",
            "s.price-item",
            "del.price-item",
            "del",
            "[class*='compare-at']",
            "[class*='compare']",
            "[class*='original-price']",
            "[class*='original']",
        ]

        price = extract_price_from_elements(
            selector,
            detail_selectors,
        )

        if price is not None:
            return price

    return None


def extract_sale_price(selector, card=None):
    if card is not None:
        for css in (
            "div.product-card__price",
            "div.product-card__pricesale",
        ):
            try:
                for element in card.css(css):
                    numbers = extract_all_numbers(
                        extract_text(element)
                    )

                    if numbers:
                        return numbers[0]

            except Exception:
                continue

        for css in (
            "span.current-price",
            "span.price",
            ".price-item--sale",
            ".price__sale .price-item--sale",
            ".price__sale .price-item",
            "[class*='sale-price']",
            "[class*='selling-price']",
        ):
            try:
                for element in card.css(css):
                    numbers = extract_all_numbers(
                        extract_text(element)
                    )

                    if numbers:
                        return numbers[0]

            except Exception:
                continue

    if selector is not None:
        for css in (
            "span.current-price",
            "span.price",
            ".price-item--sale",
            ".price__sale .price-item--sale",
            ".price__sale .price-item",
            "[class*='sale-price']",
            "[class*='selling-price']",
        ):
            try:
                for element in selector.css(css):
                    numbers = extract_all_numbers(
                        extract_text(element)
                    )

                    if numbers:
                        return numbers[0]

            except Exception:
                continue

    # Last-resort price container.
    for obj in (card, selector):
        if obj is None:
            continue

        for css in (
            "div.product-card__price",
            ".price",
            "[class*='price']",
        ):
            try:
                for element in obj.css(css):
                    numbers = extract_all_numbers(
                        extract_text(element)
                    )

                    if numbers:
                        return numbers[0]

            except Exception:
                continue

    return None


# ---------------------------------------------------------
# PRODUCT LISTING AND PRODUCT DETAILS
# ---------------------------------------------------------

def extract_product_urls(selector):
    if selector is None:
        return []

    result = []
    seen = set()

    for link in selector.css("a[href*='/products/']"):
        href = extract_attribute(link, "href")
        cleaned = clean_product_url(href)

        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)

    return result


def find_product_card(product_link, selector):
    if not product_link or selector is None:
        return None

    for link in selector.css("a[href*='/products/']"):
        href = extract_attribute(link, "href")

        if clean_product_url(href) != product_link:
            continue

        ancestors = link.xpath(
            "ancestor::*[self::li or self::article or "
            "contains(@class, 'product-card') or "
            "contains(@class, 'card')][1]"
        )

        if ancestors:
            return ancestors[0]

        parent = link.xpath("ancestor::li[1]")

        if parent:
            return parent[0]

    return None


def extract_product_name(selector, card=None):
    selectors = [
        "a.d-block.product-card__name",
        "a.product-title",
        "h2 a",
        ".product-card__name",
        ".card__heading",
        "h1.product__title",
        "h1",
        "h2",
        "h3",
    ]

    objects = [obj for obj in (card, selector) if obj is not None]

    for obj in objects:
        for css in selectors:
            try:
                for element in obj.css(css):
                    name = extract_text(element)

                    if name != "N/A" and len(name) > 1:
                        return name

            except Exception:
                pass

    return "N/A"


def extract_rating(selector, card=None):
    objects = [obj for obj in (selector, card) if obj is not None]

    rating_selectors = [
        ".sum-mz-r",
        "p.rating-text.caption span",
        ".rating-value",
        ".rating",
        "[class*='rating-value']",
    ]

    for obj in objects:
        for css in rating_selectors:
            try:
                for element in obj.css(css):
                    text = extract_text(element)

                    if text == "N/A" or re.search(r"reviews?", text, re.I):
                        continue

                    match = re.search(r"([0-5](?:\.\d+)?)", text)

                    if match:
                        value = extract_number(match.group(1))

                        if value is not None and 0 <= float(value) <= 5:
                            return value

            except Exception:
                pass

    return "N/A"


# ---------------------------------------------------------
# JSON-LD AND DESCRIPTION
# ---------------------------------------------------------

def get_json_ld_objects(selector):
    objects = []

    if selector is None:
        return objects

    try:
        scripts = selector.css(
            "script[type='application/ld+json']::text"
        ).getall()

        for script_text in scripts:
            try:
                data = json.loads(script_text)
            except Exception:
                continue

            if isinstance(data, list):
                objects.extend(data)

            elif isinstance(data, dict):
                graph = data.get("@graph")

                if isinstance(graph, list):
                    objects.extend(graph)
                else:
                    objects.append(data)

    except Exception:
        pass

    return objects


def find_product_json_ld(selector):
    for obj in get_json_ld_objects(selector):
        if not isinstance(obj, dict):
            continue

        object_type = obj.get("@type")

        if object_type == "Product" or (
            isinstance(object_type, list)
            and "Product" in object_type
        ):
            return obj

    return None


def extract_description(selector):
    if selector is None:
        return "N/A"

    selectors = [
        "div.py-3 p",
        ".product__description p",
        ".product-description p",
        ".product__description",
        "[class*='product-description']",
    ]

    for css in selectors:
        try:
            values = []

            for element in selector.css(css):
                text = extract_text(element)

                if text != "N/A" and text not in values:
                    values.append(text)

            if values:
                return clean_text(" ".join(values))

        except Exception:
            pass

    product_data = find_product_json_ld(selector)

    if product_data:
        description = clean_text(product_data.get("description"))

        if description != "N/A":
            return description

    try:
        description = clean_text(
            selector.css(
                "meta[name='description']::attr(content)"
            ).get()
        )

        if description != "N/A":
            return description

    except Exception:
        pass

    return "N/A"


# ---------------------------------------------------------
# SCRAPE PRODUCT DETAIL
# ---------------------------------------------------------

async def scrape_product_detail(
    crawler,
    product_url,
    listing_selector=None,
    listing_card=None,
):
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            logger.info(
                "Opening Yardley product detail: %s",
                product_url,
            )

            result = await crawler.arun(url=product_url)

            if not result:
                raise RuntimeError("Empty Crawl4AI result")

            html = getattr(result, "html", None)

            if not html:
                raise RuntimeError("Empty detail HTML")

            selector = create_selector(html)

            if selector is None:
                raise RuntimeError("Could not create detail selector")

            product_json = find_product_json_ld(selector)

            name = extract_product_name(selector)

            if name == "N/A" and product_json:
                name = clean_text(product_json.get("name"))

            if name == "N/A":
                name = extract_product_name(
                    listing_selector,
                    listing_card,
                )

            # Extract sale price and original price.
            sale_price = extract_sale_price(
                selector,
                listing_card,
            )

            original_price = extract_original_price(
                selector,
                listing_card,
            )

            # JSON-LD fallback for sale price.
            if sale_price is None and product_json:
                offers = product_json.get("offers")

                if isinstance(offers, list):
                    offers = offers[0] if offers else None

                if isinstance(offers, dict):
                    sale_price = extract_number(
                        offers.get("price")
                    )

            if sale_price is None:
                sale_price = extract_sale_price(selector)

            if sale_price is None:
                sale_price = extract_sale_price(
                    listing_selector,
                    listing_card,
                )

            # Ensure the sale price is not greater than the original price.
            if (
                sale_price is not None
                and original_price is not None
                and float(sale_price) > float(original_price)
            ):
                sale_price, original_price = (
                    original_price,
                    sale_price,
                )

            # IMAGE FIX:
            # 1. Product gallery image.
            # 2. Product JSON-LD image.
            # 3. Matching product listing-card image.
            # Never use a generic page image or logo as a fallback.
            image_link = extract_image(selector)

            if image_link is None and product_json:
                image_link = extract_json_ld_image(product_json)

            if image_link is None:
                image_link = extract_listing_image(
                    listing_selector,
                    listing_card,
                )

            # Final safety check before saving.
            image_link = valid_yardley_image(image_link)

            if not image_link:
                logger.warning(
                    "Skipping product: no valid product image found | %s",
                    product_url,
                )
                return None

            rating = extract_rating(selector)

            if rating == "N/A" and product_json:
                aggregate = product_json.get("aggregateRating")

                if isinstance(aggregate, dict):
                    rating = extract_number(
                        aggregate.get("ratingValue")
                    )

            description = extract_description(selector)

            discount = calculate_discount(
                original_price,
                sale_price,
            )

            if name == "N/A":
                logger.warning(
                    "Skipping Yardley product: name missing | %s",
                    product_url,
                )
                return None

            if sale_price is None:
                logger.warning(
                    "Skipping Yardley product: price missing | %s",
                    name,
                )
                return None

            timestamp = datetime.now(
                timezone(timedelta(hours=5, minutes=30))
            ).strftime("%Y-%m-%dT%H:%M:%S")

            return {
                "name": name,
                "price": sale_price,
                "currency": "₹",
                "original_price": (
                    original_price
                    if original_price is not None
                    else "N/A"
                ),
                "discount": discount,
                "ratings": rating,
                "description": description,
                "image_link": image_link,
                "product_link": clean_product_url(product_url),
                "organization_id": "Dealwallet",
                "store_id": "Yardley",
                "categories_id": "Beauty",
                "created_at": timestamp,
            }

        except Exception as exc:
            logger.warning(
                "Yardley detail attempt %s/%s failed for %s: %s",
                attempt,
                RETRY_COUNT,
                product_url,
                exc,
            )

            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)

    logger.error(
        "Failed to scrape Yardley product after %s attempts: %s",
        RETRY_COUNT,
        product_url,
    )

    return None


# ---------------------------------------------------------
# FETCH LISTING PAGE
# ---------------------------------------------------------

async def fetch_listing_page(crawler, page_url):
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            logger.info("Fetching Yardley listing: %s", page_url)

            result = await crawler.arun(url=page_url)

            if not result:
                raise RuntimeError("Empty Crawl4AI result")

            html = getattr(result, "html", None)

            if html:
                return html

            raise RuntimeError("Empty listing HTML")

        except Exception as exc:
            logger.warning(
                "Yardley listing attempt %s/%s failed: %s",
                attempt,
                RETRY_COUNT,
                exc,
            )

            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)

    return None


# ---------------------------------------------------------
# SCRAPE CATEGORY AND PAGINATION
# ---------------------------------------------------------

async def scrape_yardley_category(crawler, category, base_url):
    products = []
    seen_product_urls = set()

    for page_number in range(1, MAX_PAGES + 1):
        page_url = (
            base_url
            if page_number == 1
            else f"{base_url}{'&' if '?' in base_url else '?'}page={page_number}"
        )

        logger.info(
            "Scraping Yardley page %s/%s: %s",
            page_number,
            MAX_PAGES,
            page_url,
        )

        html = await fetch_listing_page(crawler, page_url)

        if not html:
            continue

        selector = create_selector(html)

        if selector is None:
            continue

        product_urls = extract_product_urls(selector)

        logger.info(
            "Yardley product URLs found on page %s: %s",
            page_number,
            len(product_urls),
        )

        if not product_urls:
            break

        new_product_urls = [
            url
            for url in product_urls
            if url not in seen_product_urls
        ]

        for url in new_product_urls:
            seen_product_urls.add(url)

        logger.info(
            "New Yardley product URLs on page %s: %s",
            page_number,
            len(new_product_urls),
        )

        if not new_product_urls:
            logger.info(
                "No new Yardley products found on page %s. Pagination complete.",
                page_number,
            )
            break

        before = len(products)

        for index, product_url in enumerate(
            new_product_urls,
            start=1,
        ):
            listing_card = find_product_card(
                product_url,
                selector,
            )

            product = await scrape_product_detail(
                crawler,
                product_url,
                selector,
                listing_card,
            )

            if product:
                products.append(product)

                logger.info(
                    "Yardley product collected %s/%s: %s | Price: %s | Rating: %s",
                    index,
                    len(new_product_urls),
                    product.get("name"),
                    product.get("price"),
                    product.get("ratings"),
                )

        logger.info(
            "Yardley page %s completed. New products collected: %s | Total: %s",
            page_number,
            len(products) - before,
            len(products),
        )

    logger.info(
        "Yardley category completed: %s | Total products: %s",
        base_url,
        len(products),
    )

    return products


# ---------------------------------------------------------
# MAIN CRAWL4AI WORKER
# ---------------------------------------------------------

async def scrape_yardley_products():
    all_products = []
    crawler = AsyncWebCrawler(verbose=True)

    crawler.browser_config = {
        "headless": True,
        "javascript": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    crawler.crawler_run_config = {
        "wait_until": "networkidle",
        "timeout": PAGE_TIMEOUT,
        "delay_before_return_html": 2,
        "wait_for": {
            "selector": "a[href*='/products/']",
            "timeout": 30000,
        },
    }

    try:
        try:
            await crawler.start()
        except AttributeError:
            pass

        for category, urls in BASE_URLS.items():
            for base_url in urls:
                try:
                    category_products = await scrape_yardley_category(
                        crawler,
                        category,
                        base_url,
                    )
                    all_products.extend(category_products)

                except Exception as exc:
                    logger.exception(
                        "Yardley category failed: %s",
                        exc,
                    )

        unique_products = []
        seen_keys = set()

        for product in all_products:
            key = product.get("product_link") or product.get("name")

            if key and key not in seen_keys:
                seen_keys.add(key)
                unique_products.append(product)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as json_file:
            json.dump(
                unique_products,
                json_file,
                ensure_ascii=False,
                indent=4,
            )

        logger.info(
            "Yardley scraping completed. Total unique products: %s",
            len(unique_products),
        )
        logger.info("JSON file: %s", OUTPUT_FILE)

        return unique_products

    finally:
        try:
            await crawler.close()
        except Exception:
            pass


def run_yardley_scraper():
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )
        except AttributeError:
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [YARDLEY-WORKER] %(levelname)s %(message)s",
    )

    logger.info("Starting Yardley browser worker...")

    products = asyncio.run(scrape_yardley_products())

    logger.info(
        "Yardley browser worker finished. Total products: %s",
        len(products),
    )

    return products


# ---------------------------------------------------------
# SCRAPY SPIDER
# ---------------------------------------------------------

class YardleySpider(scrapy.Spider):
    name = "yardley"
    allowed_domains = ["yardleyoflondon.com"]

    custom_settings = {
        "DOWNLOAD_TIMEOUT": 120,
    }

    async def start(self):
        self.logger.info("Starting Yardley Scrapy spider...")
        self.logger.info("Using separate Crawl4AI browser worker...")

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                products = await loop.run_in_executor(
                    executor,
                    run_yardley_scraper,
                )

        except Exception as exc:
            self.logger.exception(
                "Yardley browser worker failed: %s",
                exc,
            )
            return

        if not isinstance(products, list):
            self.logger.error(
                "Invalid Yardley worker result. Expected a list."
            )
            return

        sent_count = 0

        for product in products:
            if not product.get("name"):
                continue

            if not product.get("product_link"):
                continue

            if not product.get("image_link"):
                continue

            if product.get("price") is None:
                continue

            sent_count += 1
            yield product

        self.logger.info(
            "Yardley spider completed. Products sent to Scrapy pipeline: %s",
            sent_count,
        )


if __name__ == "__main__":
    if "--yardley-worker" in sys.argv:
        run_yardley_scraper()
    else:
        print("Yardley spider file.")
        print("Use: python -m scrapy crawl yardley")