import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
import scrapy

BASE_URL = "https://www.boveee.com"

COLLECTIONS = [
    {
        "url": "https://www.boveee.com/collections/silk",
        "category": "Fashion & Lifestyle",
        "limit": 100,
    },
]

CATEGORY_LIMITS = {
    "Fashion & Lifestyle": 100,
}

TOTAL_PRODUCTS = 100
OUTPUT_JSON = "boveee_products.json"
ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Boveee"
CURRENCY = "₹"
PAGE_TIMEOUT = 180000
PRODUCT_DELAY = 1.0

PRODUCT_CARD_SELECTORS = [
    "div.product-block",
]

PRODUCT_LINK_SELECTOR = "a.product-link[href]"

NAME_SELECTORS = [
    "div.product-block__title",
]

PRICE_SELECTORS = [
    "span.product-price__amount--on-sale span.money",
    "span.product-price__amount span.money",
]

ORIGINAL_PRICE_SELECTORS = [
    "span.product-price__compare span.money",
]

IMAGE_SELECTORS = [
    "img.rimage__image",
]

def clean_text(value):
    if value is None:
        return None

    value = BeautifulSoup(
        str(value),
        "html.parser",
    ).get_text(
        " ",
        strip=True,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return value if value else None

def clean_description(value):
    if not value:
        return None

    value = str(value)
    value = re.sub(r'[/\\"]', '', value)
    value = re.sub(r"\s+", " ", value).strip()

    return value or None

def to_int(value):
    if value is None:
        return None

    try:
        if isinstance(value, int):
            return value

        if isinstance(value, float):
            return int(value)

        text = str(value)

        text = re.sub(
            r"[₹$€£,\s]",
            "",
            text,
        )

        match = re.search(
            r"\d+(?:\.\d+)?",
            text,
        )

        if not match:
            return None

        return int(float(match.group(0)))

    except Exception:
        return None

def clean_rating(value):
    if value is None:
        return None

    try:
        rating = float(value)

        if rating == 0.0:
            return None

        return rating

    except Exception:
        return None

def normalize_product_url(href):
    if not href:
        return None

    href = href.strip()

    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = urljoin(BASE_URL, href)

    if not href.startswith("http"):
        return None

    href = href.split("?")[0]

    if "/products/" not in href:
        return None

    return href.rstrip("/")

def get_handle(product_url):
    try:
        path = urlparse(product_url).path.strip("/")
        parts = path.split("/")

        if "products" not in parts:
            return None

        index = parts.index("products")

        if index + 1 >= len(parts):
            return None

        return parts[index + 1]

    except Exception:
        return None

def mandatory_fields_present(product):
    if not product:
        return False

    required_fields = [
        "name",
        "price",
        "original_price",
        "discount",
        "image_link",
        "product_link",
    ]

    for field in required_fields:
        value = product.get(field)

        if value is None:
            return False

        if isinstance(value, str) and not value.strip():
            return False

    return True

def extract_product_links(html, max_links=None):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    links = []
    seen = set()

    for card_selector in PRODUCT_CARD_SELECTORS:
        cards = soup.select(card_selector)

        if not cards:
            continue

        for card in cards:
            link_node = card.select_one(
                PRODUCT_LINK_SELECTOR
            )

            if not link_node:
                continue

            product_url = normalize_product_url(
                link_node.get("href")
            )

            if not product_url:
                continue

            if product_url in seen:
                continue

            seen.add(product_url)
            links.append(product_url)

            if (
                max_links is not None
                and len(links) >= max_links
            ):
                return links

        if links:
            return links

    return links

def extract_listing_name(soup, product_url):
    for card_selector in PRODUCT_CARD_SELECTORS:
        for card in soup.select(card_selector):
            link_node = card.select_one(
                PRODUCT_LINK_SELECTOR
            )

            if not link_node:
                continue

            href = normalize_product_url(
                link_node.get("href")
            )

            if href != product_url:
                continue

            for selector in NAME_SELECTORS:
                element = card.select_one(selector)

                if element:
                    name = clean_text(
                        element.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if name:
                        return name

    return None

def extract_card_image(card):
    for selector in IMAGE_SELECTORS:
        image = card.select_one(selector)

        if not image:
            continue

        src = (
            image.get("data-src")
            or image.get("data-srcset")
            or image.get("src")
        )

        if not src:
            continue

        src = src.strip()

        if "," in src and " " in src:
            src = src.split(",")[0].strip().split(" ")[0]

        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE_URL, src)

        return src

    return None

def extract_collection_product_data(html, max_links=None):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    products = []
    seen = set()

    cards = []

    for selector in PRODUCT_CARD_SELECTORS:
        cards = soup.select(selector)

        if cards:
            break

    for card in cards:
        link_node = card.select_one(
            PRODUCT_LINK_SELECTOR
        )

        if not link_node:
            continue

        product_url = normalize_product_url(
            link_node.get("href")
        )

        if not product_url or product_url in seen:
            continue

        name = None

        for selector in NAME_SELECTORS:
            element = card.select_one(selector)

            if element:
                name = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if name:
                    break

        image_link = extract_card_image(card)

        seen.add(product_url)

        products.append(
            {
                "product_link": product_url,
                "listing_name": name,
                "image_link": image_link,
            }
        )

        if (
            max_links is not None
            and len(products) >= max_links
        ):
            break

    return products

async def get_shopify_product_json(
    crawler,
    product_url,
):
    handle = get_handle(product_url)

    if not handle:
        return None

    json_url = (
        f"{BASE_URL}/products/{handle}.js"
    )

    try:
        result = await crawler.arun(
            url=json_url,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=60000,
                wait_until="commit",
                word_count_threshold=0,
                exclude_external_links=True,
                remove_overlay_elements=True,
            ),
        )

        if not result.success:
            return None

        raw = result.html or ""

        if not raw:
            return None

        raw = raw.strip()

        try:
            return json.loads(raw)
        except Exception:
            soup = BeautifulSoup(
                raw,
                "html.parser",
            )

            text = soup.get_text(
                " ",
                strip=True,
            )

            return json.loads(text)

    except Exception as error:
        print(
            f"[PRODUCT JSON ERROR] "
            f"{product_url} -> {error}"
        )

        return None

def extract_price_data(product_json):
    if not product_json:
        return None, None, None

    variants = product_json.get(
        "variants",
        [],
    )

    if not variants:
        return None, None, None

    selected_variant = None

    for variant in variants:
        if variant.get("available"):
            selected_variant = variant
            break

    if selected_variant is None:
        selected_variant = variants[0]

    price_raw = selected_variant.get("price")
    original_raw = selected_variant.get(
        "compare_at_price"
    )

    price = None
    original_price = None

    if price_raw is not None:
        try:
            number = float(price_raw)

            if number >= 100:
                price = int(number / 100)
            else:
                price = int(number)

        except Exception:
            price = to_int(price_raw)

    if original_raw is not None:
        try:
            number = float(original_raw)

            if number >= 100:
                original_price = int(number / 100)
            else:
                original_price = int(number)

        except Exception:
            original_price = to_int(original_raw)

    discount = None

    if (
        price is not None
        and original_price is not None
        and original_price > price
    ):
        discount = int(
            round(
                (
                    (original_price - price)
                    / original_price
                )
                * 100
            )
        )

    return (
        price,
        original_price,
        discount,
    )

def extract_name(
    soup,
    product_json,
    listing_name=None,
):
    if listing_name:
        listing_name = clean_text(listing_name)

        if listing_name:
            return listing_name

    selectors = [
        "h1.product__title",
        "h1.product-title",
        "h1.product__title-desk",
        "div.product__title h1",
        "div.product__title h2",
        "h1",
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if element:
            name = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if name:
                return name

    if product_json:
        title = product_json.get("title")

        if title:
            return clean_text(title)

    return None

def extract_description(
    soup,
    product_json,
):
    selectors = [
        "div.product__description",
        ".product__description",
        "[class*='product__description']",
        ".rte",
        ".product-single__description",
        ".product-description",
        "[data-product-description]",
        ".product__content",
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if element:
            text = clean_text(element)

            if text:
                return clean_description(text)

    if product_json:
        body_html = product_json.get(
            "description"
        )

        if body_html:
            text = clean_text(body_html)

            if text:
                return clean_description(text)

    return None

def extract_image(
    soup,
    product_json,
    collection_image=None,
):
    selectors = [
        "meta[property='og:image']",
        "div.product__media img",
        ".product__media img",
        ".product-single__media img",
        ".product__media-list img",
        "main img",
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if not element:
            continue

        src = (
            element.get("content")
            or element.get("src")
            or element.get("data-src")
            or element.get("data-original")
        )

        if not src:
            continue

        src = src.strip()

        if "," in src and " " in src:
            src = src.split(",")[0].strip().split(" ")[0]

        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE_URL, src)

        return src

    if product_json:
        image = product_json.get(
            "featured_image"
        )

        if image:
            image = str(image).strip()

            if image.startswith("//"):
                image = "https:" + image
            elif image.startswith("/"):
                image = urljoin(
                    BASE_URL,
                    image,
                )

            return image

        images = product_json.get(
            "images",
            [],
        )

        if images:
            image = str(images[0]).strip()

            if image.startswith("//"):
                image = "https:" + image
            elif image.startswith("/"):
                image = urljoin(
                    BASE_URL,
                    image,
                )

            return image

    return collection_image

def extract_rating(soup, product_json=None):
    selectors = [
        "[data-rating]",
        "[data-average-rating]",
        ".rating",
        ".jdgm-prev-badge",
        "[class*='rating']",
    ]

    for selector in selectors:
        element = soup.select_one(selector)

        if not element:
            continue

        for attribute in [
            "data-rating",
            "data-average-rating",
            "data-score",
        ]:
            value = element.get(attribute)

            if value is not None:
                rating = clean_rating(value)

                if rating is not None:
                    return rating

        aria = element.get("aria-label")

        if aria:
            match = re.search(
                r"(\d+(?:\.\d+)?)",
                aria,
            )

            if match:
                rating = clean_rating(
                    match.group(1)
                )

                if rating is not None:
                    return rating

        text = element.get_text(
            " ",
            strip=True,
        )

        match = re.search(
            r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
            text,
        )

        if match:
            rating = clean_rating(
                match.group(1)
            )

            if rating is not None:
                return rating

    if product_json:
        for key in [
            "rating",
            "ratings",
            "average_rating",
        ]:
            value = product_json.get(key)

            if value is not None:
                rating = clean_rating(value)

                if rating is not None:
                    return rating

    return None

async def scrape_product(
    crawler,
    product_data,
    category,
    index,
):
    product_url = product_data.get(
        "product_link"
    )

    print()
    print(
        f"[PRODUCT {index}] "
        f"{product_url}"
    )

    try:
        result = await crawler.arun(
            url=product_url,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=PAGE_TIMEOUT,
                wait_until="domcontentloaded",
                word_count_threshold=0,
                exclude_external_links=True,
                remove_overlay_elements=True,
            ),
        )

        if not result.success:
            print("[PRODUCT FAILED]")
            return None

        html = result.html or ""

        if not html:
            return None

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        product_json = await get_shopify_product_json(
            crawler,
            product_url,
        )

        price, original_price, discount = (
            extract_price_data(product_json)
        )

        if price is None:
            print("[SKIP] Price missing")
            return None

        if original_price is None:
            print("[SKIP] Original price missing")
            return None

        if discount is None:
            print("[SKIP] Discount missing")
            return None

        name = extract_name(
            soup,
            product_json,
            product_data.get("listing_name"),
        )

        if not name:
            print("[SKIP] Name missing")
            return None

        description = extract_description(
            soup,
            product_json,
        )

        description = clean_description(
            description
        )

        image_link = extract_image(
            soup,
            product_json,
            product_data.get("image_link"),
        )

        if not image_link:
            print("[SKIP] Image missing")
            return None

        if not product_url:
            print("[SKIP] Product link missing")
            return None

        ratings = extract_rating(
            soup,
            product_json,
        )

        if ratings == 0.0:
            ratings = None

        product = {
            "name": name,
            "price": int(price),
            "currency": CURRENCY,
            "original_price": int(original_price),
            "discount": int(discount),
            "ratings": ratings,
            "description": description,
            "image_link": image_link,
            "product_link": product_url,
            "organization_id": ORGANIZATION_ID,
            "store_id": STORE_ID,
            "categories_id": category,
            "created_at": datetime.now().isoformat(),
        }

        if not mandatory_fields_present(product):
            print("[SKIP] Mandatory field missing")
            return None

        print(f"[VALID] {name}")
        print(f"        Price={price}")
        print(f"        Original={original_price}")
        print(f"        Discount={discount}%")
        print(f"        Rating={ratings}")
        print(f"        Category={category}")

        return product

    except Exception as error:
        print(
            f"[PRODUCT ERROR] "
            f"{product_url} -> {error}"
        )

        return None

async def scrape_boveee_async():
    print("=" * 70)
    print("BOVEEE CRAWL4AI SCRAPER")
    print("=" * 70)
    print("Collection: Sindoor")
    print("Maximum TOTAL products: 100")
    print("Only discounted products")
    print("=" * 70)

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        extra_args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,BackForwardCache",
        ],
    )

    all_products = []
    seen_product_urls = set()
    category_counts = {
        "Fashion & Lifestyle": 0,
    }
    collection_counts = {
        collection["url"]: 0
        for collection in COLLECTIONS
    }

    async with AsyncWebCrawler(
        config=browser_config
    ) as crawler:

        for collection_index, collection in enumerate(
            COLLECTIONS,
            start=1,
        ):
            collection_url = collection["url"]
            category = collection["category"]

            category_limit = collection.get(
                "limit",
                CATEGORY_LIMITS.get(
                    category,
                    0,
                ),
            )

            remaining = (
                category_limit
                - collection_counts.get(
                    collection_url,
                    0,
                )
            )

            if remaining <= 0:
                continue

            print()
            print("=" * 70)
            print(
                f"[URL {collection_index}/"
                f"{len(COLLECTIONS)}]"
            )
            print(
                f"[COLLECTION URL] "
                f"{collection_url}"
            )
            print(
                f"[CATEGORY] {category}"
            )
            print(
                f"[REMAINING] {remaining}"
            )
            print("=" * 70)

            page_number = 1

            while (
                len(all_products) < TOTAL_PRODUCTS
                and collection_counts[collection_url]
                < category_limit
            ):
                current_url = collection_url

                if page_number > 1:
                    separator = (
                        "&"
                        if "?" in collection_url
                        else "?"
                    )
                    current_url = (
                        f"{collection_url}"
                        f"{separator}"
                        f"page={page_number}"
                    )

                print(
                    f"[COLLECTION PAGE] "
                    f"{page_number}"
                )
                print(
                    f"[URL] {current_url}"
                )

                try:
                    result = await crawler.arun(
                        url=current_url,
                        config=CrawlerRunConfig(
                            cache_mode=CacheMode.BYPASS,
                            page_timeout=300000,
                            wait_until="domcontentloaded",
                            delay_before_return_html=8.0,
                            remove_overlay_elements=True,
                            scan_full_page=False,
                            scroll_delay=0.5,
                        ),
                    )
                except Exception as error:
                    print(
                        f"[COLLECTION ERROR] "
                        f"{error}"
                    )
                    break

                if not result.success:
                    print(
                        "[COLLECTION FAILED]"
                    )
                    print(
                        getattr(
                            result,
                            "error_message",
                            None,
                        )
                    )
                    break

                html = result.html or ""

                if not html:
                    print(
                        "[COLLECTION EMPTY]"
                    )
                    break

                page_products = (
                    extract_collection_product_data(
                        html,
                        remaining,
                    )
                )

                print(
                    f"[COLLECTION] Product links found: "
                    f"{len(page_products)}"
                )

                if not page_products:
                    break

                page_added = 0

                for product_index, product_data in enumerate(
                    page_products,
                    start=1,
                ):
                    if (
                        len(all_products)
                        >= TOTAL_PRODUCTS
                    ):
                        break

                    if (
                        collection_counts[collection_url]
                        >= category_limit
                    ):
                        break

                    product_url = product_data.get(
                        "product_link"
                    )

                    if (
                        not product_url
                        or product_url
                        in seen_product_urls
                    ):
                        print(
                            f"[SKIP DUPLICATE] "
                            f"{product_url}"
                        )
                        continue

                    product = await scrape_product(
                        crawler,
                        product_data,
                        category,
                        product_index,
                    )

                    if product is None:
                        continue

                    if (
                        product_url
                        in seen_product_urls
                    ):
                        continue

                    seen_product_urls.add(
                        product_url
                    )

                    all_products.append(
                        product
                    )

                    collection_counts[
                        collection_url
                    ] += 1

                    category_counts[
                        category
                    ] += 1

                    page_added += 1

                    print(
                        f"[URL TOTAL] "
                        f"{collection_url}: "
                        f"{collection_counts[collection_url]}/"
                        f"{category_limit}"
                    )

                    print(
                        f"[GLOBAL TOTAL] "
                        f"{len(all_products)}/"
                        f"{TOTAL_PRODUCTS}"
                    )

                    await asyncio.sleep(
                        PRODUCT_DELAY
                    )

                if page_added == 0:
                    page_number += 1
                else:
                    page_number += 1

                if len(page_products) == 0:
                    break

            print()
            print(
                f"[URL COMPLETE] "
                f"{collection_url}: "
                f"{collection_counts[collection_url]}/"
                f"{category_limit}"
            )

    return all_products[:TOTAL_PRODUCTS]

def run_boveee_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    logging.info(
        "Boveee Crawl4AI worker started."
    )

    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )
        except AttributeError:
            pass

    products = asyncio.run(
        scrape_boveee_async()
    )

    logging.info(
        "Boveee worker finished | Products=%s",
        len(products),
    )

    return products

def save_json(products):
    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            products,
            file,
            ensure_ascii=False,
            indent=4,
        )

    logging.info(
        "JSON generated: %s",
        OUTPUT_JSON,
    )

    logging.info(
        "Products written: %s",
        len(products),
    )

class BoveeeSpider(scrapy.Spider):
    name = "boveee"

    allowed_domains = [
        "boveee.com",
        "www.boveee.com",
    ]

    async def start(self):
        self.logger.info(
            "=" * 70
        )
        self.logger.info(
            "Starting Boveee Crawl4AI scraper..."
        )
        self.logger.info(
            "=" * 70
        )

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:
                products = await loop.run_in_executor(
                    executor,
                    run_boveee_scraper,
                )

        except Exception as error:
            self.logger.exception(
                "Boveee Crawl4AI worker failed: %s",
                error,
            )
            return

        valid_products = []

        for product in products:
            if not mandatory_fields_present(
                product
            ):
                continue

            valid_products.append(
                product
            )

            yield product

        save_json(
            valid_products
        )

        self.logger.info(
            "=" * 70
        )
        self.logger.info(
            "Boveee spider completed."
        )
        self.logger.info(
            "Valid products: %s",
            len(valid_products),
        )
        self.logger.info(
            "Output: %s",
            OUTPUT_JSON,
        )
        self.logger.info(
            "=" * 70
        )

if __name__ == "__main__":
    data = run_boveee_scraper()
    valid_products = [
        product
        for product in data
        if mandatory_fields_present(product)
    ]
    save_json(valid_products)
    print("=" * 70)
    print("BOVEEE SCRAPING COMPLETED")
    print("Valid products:", len(valid_products))
    print("Output:", OUTPUT_JSON)
    print("=" * 70)
