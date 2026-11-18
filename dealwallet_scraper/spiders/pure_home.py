import asyncio
import csv
import json
import logging
import re
import sys

from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import (
    urljoin, urlparse, urlunparse, urlsplit,
    urlunsplit, parse_qsl, urlencode
)

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode
)

# Image validation
import aiohttp


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.purehomeandliving.com"

COLLECTIONS = [
    (
        "https://www.purehomeandliving.com/collections/home-decor?inStock%5B%5D=true&page=2&discountPercentage_gte%5B%5D=20&discountPercentage_lte%5B%5D=",
        "Home Decor"
    ),
    (
        "https://www.purehomeandliving.com/collections/kitchen-dining?inStock[]=true",
        "Home & Kitchen"
    ),
    (
        "https://www.purehomeandliving.com/collections/packaged-gift-boxes?inStock[]=true",
        "Flowers & Gifts"
    )
]

CURRENCY = "₹"
ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Pure Home And Living"

MAX_PRODUCTS = 100
MAX_PRODUCTS_PER_COLLECTION = 100
MAX_PAGES_PER_COLLECTION = 20

OUTPUT_JSON = "purehome_products.json"
OUTPUT_CSV = "purehome_products.csv"
SPIDER_JSON = "scrape_purehome.json"

IMAGE_TIMEOUT = 20
MAX_IMAGE_SIZE = 15 * 1024 * 1024

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

CSV_FIELDS = [
    "name", "price", "currency", "original_price", "discount",
    "ratings", "description", "image_link", "product_link",
    "organization_id", "store_id", "categories_id", "created_at"
]


# ============================================================
# COMMON FUNCTIONS
# ============================================================

def clean_text(value):

    if not value:
        return ""

    value = str(value).replace("✔", " ")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def price_to_int(value):

    if value is None:
        return None

    text = str(value).replace("₹", "").replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        return int(float(match.group(0)))
    except (TypeError, ValueError):
        return None


def normalize_url(url, base_url=BASE_URL):

    if not url:
        return None

    url = str(url).strip()

    if url.startswith("//"):
        url = "https:" + url

    url = urljoin(base_url, url)
    parsed = urlparse(url)

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        "",
        "",
        ""
    ))


# ============================================================
# PRODUCT URL VALIDATION
# ============================================================

def is_valid_product_url(url):

    if not url:
        return False

    try:

        parsed = urlparse(str(url).strip())

        host = (parsed.hostname or "").lower()

        valid_host = (
            host == "purehomeandliving.com"
            or host.endswith(".purehomeandliving.com")
        )

        valid_path = bool(
            re.fullmatch(
                r"/products/[^/]+/?",
                parsed.path or ""
            )
        )

        return (
            parsed.scheme in ("http", "https")
            and valid_host
            and valid_path
        )

    except (TypeError, ValueError):

        return False


def is_valid_product_page(result, html):

    if not html or not html.strip():
        return False, "Empty product page HTML"

    status_code = getattr(result, "status_code", None)

    if status_code is not None:

        try:

            if int(status_code) < 200 or int(status_code) >= 400:
                return False, f"HTTP status {status_code}"

        except (TypeError, ValueError):
            pass

    final_url = getattr(result, "url", None)

    if final_url and not is_valid_product_url(final_url):
        return False, f"Invalid redirect URL: {final_url}"

    soup = BeautifulSoup(html, "html.parser")

    title = soup.select_one(
        "h1.product__title, .product__title h1, "
        "h1.product-title, h1[itemprop='name']"
    )

    product_json = soup.select(
        'script[type="application/ld+json"]'
    )

    has_heading = bool(
        title and clean_text(title.get_text(" ", strip=True))
    )

    has_product_json = any(
        '"@type":"product"' in re.sub(
            r"\s+", "", tag.get_text(" ", strip=True)
        ).lower()
        or '"@type":["product"' in re.sub(
            r"\s+", "", tag.get_text(" ", strip=True)
        ).lower()
        for tag in product_json
    )

    if not has_heading and not has_product_json:
        return False, "Product heading/structured data missing"

    return True, "Valid product page"


# ============================================================
# IMAGE URL VALIDATION
# ============================================================

def is_valid_image_url(url):

    if not url:
        return False

    try:

        parsed = urlparse(str(url).strip())

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and not url.lower().startswith("data:")
            and bool(parsed.path)
        )

    except (TypeError, ValueError):

        return False


async def validate_image_url(session, image_url):

    if not is_valid_image_url(image_url):

        return False, "Invalid image URL"

    try:

        timeout = aiohttp.ClientTimeout(
            total=IMAGE_TIMEOUT
        )

        async with session.get(
            image_url,
            timeout=timeout,
            allow_redirects=True
        ) as response:

            if response.status != 200:

                return False, f"Image HTTP status {response.status}"

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if not content_type.startswith("image/"):

                return False, f"Invalid image content type: {content_type}"

            content_length = response.headers.get(
                "Content-Length"
            )

            if content_length:

                try:

                    if int(content_length) > MAX_IMAGE_SIZE:
                        return False, "Image is too large"

                except ValueError:
                    pass

            image_data = await response.content.read(
                MAX_IMAGE_SIZE + 1
            )

            if not image_data:

                return False, "Empty image response"

            if len(image_data) > MAX_IMAGE_SIZE:

                return False, "Image exceeds maximum allowed size"

            # Verify common image file signatures.
            valid_signatures = (
                image_data.startswith(b"\xff\xd8\xff"),  # JPEG
                image_data.startswith(b"\x89PNG\r\n\x1a\n"),  # PNG
                image_data.startswith(b"GIF87a"),
                image_data.startswith(b"GIF89a"),
                image_data.startswith(b"RIFF")
                and image_data[8:12] == b"WEBP",
                image_data.startswith(b"BM"),  # BMP
                image_data.startswith(b"II*\x00")
                or image_data.startswith(b"MM\x00*"),  # TIFF
            )

            if not any(valid_signatures):

                return False, "Image content signature invalid"

            return True, "Valid image"

    except asyncio.TimeoutError:

        return False, "Image request timeout"

    except Exception as exc:

        return False, f"Image validation error: {exc}"


# ============================================================
# PAGINATION
# ============================================================

def set_page_number(url, page_number):

    parts = urlsplit(url)

    query = parse_qsl(
        parts.query,
        keep_blank_values=True
    )

    updated = []
    page_written = False

    for key, value in query:

        if key == "page":

            if not page_written:

                updated.append(
                    ("page", str(page_number))
                )

                page_written = True

        else:

            updated.append((key, value))

    if not page_written:

        updated.append(
            ("page", str(page_number))
        )

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(updated, doseq=True),
        parts.fragment
    ))


# ============================================================
# DISCOUNT CALCULATION
# ============================================================

def calculate_discount(price, original_price):

    if (
        price is None
        or original_price is None
        or original_price <= 0
        or price >= original_price
    ):

        return 0

    return int(round(
        ((original_price - price) / original_price) * 100
    ))


# ============================================================
# RATING EXTRACTION
# ============================================================

def extract_rating(soup):

    for selector in (
        '[itemprop="ratingValue"]',
        '[data-average-rating]',
        '.jdgm-prev-badge__text',
        '.jdgm-prev-badge',
        '.spr-badge-caption',
        '.rating',
        '[class*="rating"]',
    ):

        element = soup.select_one(selector)

        if not element:
            continue

        raw = (
            element.get("content")
            or element.get("data-average-rating")
            or element.get_text(" ", strip=True)
        )

        match = re.search(
            r"\d+(?:\.\d+)?",
            str(raw)
        )

        if match:

            try:

                rating = float(match.group(0))

                return round(rating, 1) if rating > 0 else None

            except ValueError:
                pass

    return None


# ============================================================
# SOLD-OUT VALIDATION
# ============================================================

def is_product_available(soup, listing_card=None):

    unavailable_pattern = re.compile(
        r"\b(sold\s*out|out\s*of\s*stock|unavailable|currently\s*unavailable)\b",
        re.IGNORECASE
    )

    # Listing card validation.
    if listing_card is not None:

        for selector in (
            ".sold-out",
            ".soldout",
            ".out-of-stock",
            ".out_of_stock",
            ".product-sold-out",
            ".boost-pfs-filter-product-item-label",
            "[class*=sold-out]",
            "[class*=soldout]",
            "[class*=out-of-stock]",
        ):

            for element in listing_card.select(selector):

                label = clean_text(
                    element.get_text(" ", strip=True)
                )

                classes = " ".join(
                    element.get("class", [])
                )

                if (
                    unavailable_pattern.search(label)
                    or re.search(
                        r"sold.?out|out.?of.?stock",
                        classes,
                        re.I
                    )
                ):

                    return False

    # Shopify variant availability.
    for script in soup.select(
        'script[type="application/json"]'
    ):

        raw = script.string or script.get_text(strip=True)

        if not raw:
            continue

        try:

            data = json.loads(raw)

        except (json.JSONDecodeError, TypeError):

            continue

        if isinstance(data, dict):

            variants = data.get("variants")

            if isinstance(variants, list) and variants:

                flags = [
                    variant.get("available")
                    for variant in variants
                    if isinstance(variant, dict)
                ]

                if flags and all(flag is False for flag in flags):

                    return False

    # Availability metadata.
    for element in soup.select(
        '[itemprop="availability"], '
        'meta[property="product:availability"]'
    ):

        value = (
            element.get("content")
            or element.get("href")
            or element.get_text(" ", strip=True)
        )

        if re.search(
            r"outofstock|out.of.stock|sold.?out|discontinued",
            str(value),
            re.I
        ):

            return False

    # Product information.
    for selector in (
        ".t4s-product__info-container",
        ".product__info-container",
        ".product__info",
        ".product-single__meta",
        ".product-form",
        "product-form",
        "[data-product-form]",
    ):

        for element in soup.select(selector):

            if unavailable_pattern.search(
                clean_text(
                    element.get_text(" ", strip=True)
                )
            ):

                return False

    # Add to cart buttons.
    for selector in (
        'form[action*="/cart/add"] button[type="submit"]',
        'form[action*="/cart/add"] [name="add"]',
        'product-form button[type="submit"]',
        ".t4s-product-form__submit",
        "button[name=add]",
    ):

        for button in soup.select(selector):

            label = clean_text(" ".join((
                button.get_text(" ", strip=True),
                button.get("aria-label", ""),
                button.get("title", "")
            )))

            if unavailable_pattern.search(label):

                return False

            if button.has_attr("disabled"):

                button_text = label.lower()

                if (
                    not button_text
                    or unavailable_pattern.search(button_text)
                ):

                    return False

    return True


# ============================================================
# LISTING PAGE PRODUCT EXTRACTION
# ============================================================

def extract_listing_products(html, collection_url):

    soup = BeautifulSoup(
        html or "",
        "html.parser"
    )

    cards = soup.select(
        ".boost-pfs-filter-product-item"
    )

    products = []
    seen = set()

    for card in cards:

        if not is_product_available(
            BeautifulSoup(str(card), "html.parser"),
            card
        ):

            print(
                "[SKIPPED LISTING] Sold out / unavailable product"
            )

            continue

        title_el = (
            card.select_one(
                "a.boost-pfs-filter-product-item-title[href*='/products/']"
            )
            or card.select_one(
                "a.boost-pfs-filter-product-item-image-link[href*='/products/']"
            )
            or card.select_one(
                "a[href*='/products/']"
            )
        )

        if not title_el:
            continue

        product_link = normalize_url(
            title_el.get("href"),
            collection_url
        )

        # Product URL validation.
        if not is_valid_product_url(product_link):

            print(
                f"[SKIPPED LISTING] Invalid product URL: {product_link}"
            )

            continue

        if product_link in seen:
            continue

        seen.add(product_link)

        name = clean_text(
            title_el.get_text(" ", strip=True)
        )

        if not name:

            name = clean_text(
                card.get("title", "")
            )

        if not name:

            print("[SKIPPED LISTING] Product name missing")
            continue

        image_el = (
            card.select_one(
                "img.boost-pfs-filter-product-item-main-image"
            )
            or card.select_one(
                ".boost-pfs-filter-product-item-image img"
            )
            or card.select_one("img")
        )

        image_url = ""

        if image_el:

            image_url = (
                image_el.get("src")
                or image_el.get("data-src")
                or image_el.get("data-original")
                or image_el.get("data-lazy-src")
                or ""
            )

            if (
                (not image_url or image_url.startswith("data:"))
                and image_el.get("data-srcset")
            ):

                image_url = (
                    image_el.get("data-srcset", "")
                    .split(",")[0]
                    .strip()
                    .split(" ")[0]
                )

            elif (
                (not image_url or image_url.startswith("data:"))
                and image_el.get("srcset")
            ):

                image_url = (
                    image_el.get("srcset", "")
                    .split(",")[0]
                    .strip()
                    .split(" ")[0]
                )

            image_url = normalize_url(
                image_url,
                collection_url
            ) or ""

        # Listing price extraction.
        sale_el = card.select_one(
            ".boost-pfs-filter-product-item-sale-price"
        )

        price_root = card.select_one(
            ".boost-pfs-filter-product-item-price"
        )

        original_el = (
            price_root.select_one("s")
            if price_root
            else card.select_one("s")
        )

        discount_el = (
            price_root.select_one("span[style*='color']")
            if price_root
            else None
        )

        if not discount_el and price_root:

            discount_el = price_root.select_one("span")

        listing_price = (
            price_to_int(
                sale_el.get_text(" ", strip=True)
            )
            if sale_el
            else None
        )

        listing_original_price = (
            price_to_int(
                original_el.get_text(" ", strip=True)
            )
            if original_el
            else None
        )

        discount_match = (
            re.search(
                r"(\d+(?:\.\d+)?)\s*%",
                discount_el.get_text(" ", strip=True)
            )
            if discount_el
            else None
        )

        listing_discount = (
            int(float(discount_match.group(1)))
            if discount_match
            else calculate_discount(
                listing_price,
                listing_original_price
            )
        )

        if listing_discount <= 0:

            print(
                f"[SKIPPED LISTING] No discount: {name}"
            )

            continue

        if (
            listing_price is not None
            and listing_original_price is not None
            and listing_price >= listing_original_price
        ):

            print(
                f"[SKIPPED LISTING] Invalid price comparison: {name}"
            )

            continue

        products.append({
            "name": name,
            "product_link": product_link,
            "image_link": image_url,
            "ratings": extract_rating(card),
            "listing_price": listing_price,
            "listing_original_price": listing_original_price,
            "listing_discount": listing_discount,
        })

    return products


# ============================================================
# PRODUCT PAGE PRICE EXTRACTION
# ============================================================

def extract_product_prices(soup, listing_product):

    # Exact selectors supplied by the user.
    product_price_root = soup.select_one(
        ".t4s-product-price[data-pr-price][data-product-price]"
    ) or soup.select_one(
        ".t4s-product-price"
    )

    if product_price_root:

        sale_el = product_price_root.select_one("ins")

        original_el = product_price_root.select_one("del")

        discount_el = product_price_root.select_one(
            "span.custom-sale"
        )

        price = (
            price_to_int(
                sale_el.get_text(" ", strip=True)
            )
            if sale_el
            else None
        )

        original_price = (
            price_to_int(
                original_el.get_text(" ", strip=True)
            )
            if original_el
            else None
        )

        discount_match = (
            re.search(
                r"(\d+(?:\.\d+)?)\s*%",
                discount_el.get_text(" ", strip=True)
            )
            if discount_el
            else None
        )

        page_discount = (
            int(float(discount_match.group(1)))
            if discount_match
            else None
        )

        if price is not None and original_price is not None:

            discount = (
                page_discount
                if page_discount is not None
                else calculate_discount(
                    price,
                    original_price
                )
            )

            return price, original_price, int(discount or 0)

    # Existing fallback selectors.
    price_root = (
        soup.select_one(".product__info-container .price")
        or soup.select_one(".product__info .price")
        or soup.select_one(".product-single__price")
        or soup.select_one(".product-price")
        or soup.select_one("[itemprop='offers']")
        or soup
    )

    sale_el = (
        price_root.select_one(".price__sale .price-item--sale")
        or price_root.select_one(".price-item--sale")
        or price_root.select_one(".product__price--sale")
        or price_root.select_one(
            ".boost-pfs-filter-product-item-sale-price"
        )
        or price_root.select_one("ins")
    )

    original_el = (
        price_root.select_one(
            ".price__sale .price-item--regular"
        )
        or price_root.select_one(".price-item--regular s")
        or price_root.select_one(".price-item--regular")
        or price_root.select_one(".product__price--compare")
        or price_root.select_one("del")
        or price_root.select_one("s")
    )

    price = (
        price_to_int(
            sale_el.get_text(" ", strip=True)
        )
        if sale_el
        else None
    )

    original_price = (
        price_to_int(
            original_el.get_text(" ", strip=True)
        )
        if original_el
        else None
    )

    if price is None:

        for selector in (
            'meta[property="product:price:amount"]',
            'meta[itemprop="price"]',
            '[itemprop="price"]'
        ):

            element = soup.select_one(selector)

            if element:

                price = price_to_int(
                    element.get("content")
                    or element.get_text(" ", strip=True)
                )

                if price is not None:
                    break

    if original_price is None:

        for selector in (
            'meta[property="product:original_price:amount"]',
            ".compare-at-price",
            ".price--compare",
            "del",
            "s"
        ):

            element = soup.select_one(selector)

            if element:

                original_price = price_to_int(
                    element.get("content")
                    or element.get_text(" ", strip=True)
                )

                if original_price is not None:
                    break

    # Listing values are fallback only.
    if price is None:

        price = listing_product.get(
            "listing_price"
        )

    if original_price is None:

        original_price = listing_product.get(
            "listing_original_price"
        )

    discount_el = soup.select_one(
        ".t4s-product-price span.custom-sale"
    )

    discount_match = (
        re.search(
            r"(\d+(?:\.\d+)?)\s*%",
            discount_el.get_text(" ", strip=True)
        )
        if discount_el
        else None
    )

    if discount_match:

        discount = int(
            float(discount_match.group(1))
        )

    else:

        discount = calculate_discount(
            price,
            original_price
        )

    return price, original_price, int(discount or 0)


# ============================================================
# PRODUCT NAME
# ============================================================

def extract_product_name(soup, fallback_name):

    for selector in (
        'h1.product__title',
        '.product__title h1',
        'h1.product-title',
        'h1[itemprop="name"]',
        'h1'
    ):

        element = soup.select_one(selector)

        if element:

            name = clean_text(
                element.get_text(" ", strip=True)
            )

            if name:
                return name

    og_title = soup.select_one(
        'meta[property="og:title"]'
    )

    return (
        clean_text(og_title.get("content", ""))
        if og_title
        else fallback_name
    )


# ============================================================
# DESCRIPTION EXTRACTION
# ============================================================

def extract_description(soup):

    for tab_wrapper in soup.select(
        ".t4s-product-tabs-wrapper .t4s-tab-wrapper"
    ):

        title = tab_wrapper.select_one(
            ".t4s-tab__text"
        )

        if (
            title
            and "DESCRIPTION" in clean_text(
                title.get_text(" ", strip=True)
            ).upper()
        ):

            element = (
                tab_wrapper.select_one(
                    ".t4s-rte.t4s-tab-content[data-t4s-tab-content]"
                )
                or tab_wrapper.select_one(
                    "[data-t4s-tab-content]"
                )
            )

            if element:

                for unwanted in element.select(
                    "script, style, noscript, iframe"
                ):

                    unwanted.decompose()

                description = clean_text(
                    element.get_text(" ", strip=True)
                )

                if description:
                    return description

    for selector in (
        ".product__description",
        ".product-description",
        ".product-single__description",
        "#ProductDescription",
        '[itemprop="description"]',
        ".product__info-container .rte",
        ".product__description-wrapper",
        ".product-description-content",
        ".rte",
    ):

        element = soup.select_one(selector)

        if element:

            for unwanted in element.select(
                "script, style, noscript, iframe"
            ):

                unwanted.decompose()

            description = clean_text(
                element.get_text(" ", strip=True)
            )

            if description:
                return description

    meta = soup.select_one(
        'meta[name="description"]'
    )

    return (
        clean_text(meta.get("content", ""))
        if meta
        else ""
    )


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def extract_product_image(soup, product_link, listing_image):

    for selector, attr in (
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        (".product__media img", "src"),
        (".product__media-list img", "src"),
        (".product-single__media img", "src"),
        (".product-gallery img", "src"),
        ("img[itemprop='image']", "src"),
    ):

        element = soup.select_one(selector)

        if element:

            value = (
                element.get(attr)
                or element.get("data-src")
                or element.get("data-original")
            )

            value = normalize_url(
                value,
                product_link
            )

            if value and is_valid_image_url(value):

                return value

    return listing_image or ""


# ============================================================
# COMPLETE PRODUCT DETAILS AND VALIDATION
# ============================================================

async def extract_product_details(
    html,
    listing_product,
    category,
    image_session
):

    soup = BeautifulSoup(
        html or "",
        "html.parser"
    )

    name = extract_product_name(
        soup,
        listing_product.get("name", "")
    )

    # 1. Product name validation.
    if not name:

        print("[SKIPPED] Product name missing")
        return None

    # 2. Sold-out validation.
    if not is_product_available(soup):

        print(
            f"[SKIPPED SOLD OUT] {name}"
        )

        return None

    # 3. Product URL validation.
    product_link = listing_product.get(
        "product_link"
    )

    if not is_valid_product_url(product_link):

        print(
            f"[SKIPPED INVALID URL] {name} | {product_link}"
        )

        return None

    # 4. Product price extraction.
    price, original_price, discount = extract_product_prices(
        soup,
        listing_product
    )

    # 5. Sale price validation.
    if price is None or price <= 0:

        print(
            f"[SKIPPED INVALID SALE PRICE] {name} | {price}"
        )

        return None

    # 6. Original price validation.
    if original_price is None or original_price <= 0:

        print(
            f"[SKIPPED INVALID ORIGINAL PRICE] "
            f"{name} | {original_price}"
        )

        return None

    if original_price <= price:

        print(
            f"[SKIPPED] Original price is not greater "
            f"than sale price: {name}"
        )

        return None

    # 7. Discount validation.
    if discount <= 0 or discount > 100:

        print(
            f"[SKIPPED INVALID DISCOUNT] {name} | {discount}%"
        )

        return None

    # 8. Description validation.
    description = extract_description(soup)

    if not description:

        print(
            f"[SKIPPED MISSING DESCRIPTION] {name}"
        )

        return None

    # 9. Image extraction.
    image_link = extract_product_image(
        soup,
        product_link,
        listing_product.get("image_link", "")
    )

    if not image_link:

        print(
            f"[SKIPPED MISSING IMAGE] {name}"
        )

        return None

    # 10. Actual image response validation.
    image_valid, image_message = await validate_image_url(
        image_session,
        image_link
    )

    if not image_valid:

        print(
            f"[SKIPPED INVALID IMAGE] "
            f"{name} | {image_message} | {image_link}"
        )

        return None

    # 11. Rating extraction.
    rating = extract_rating(soup)

    if rating is None:

        rating = listing_product.get(
            "ratings"
        )

    print(
        f"[VALIDATED] {name} | "
        f"Sale ₹{price} | "
        f"MRP ₹{original_price} | "
        f"{discount}% OFF | "
        f"Image OK"
    )

    return {
        "name": name,
        "price": int(price),
        "currency": CURRENCY,
        "original_price": int(original_price),
        "discount": int(discount),
        "ratings": rating,
        "description": description,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": category,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# SAVE JSON
# ============================================================

def save_json(products):

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            products,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"JSON generated: {OUTPUT_JSON}"
    )


# ============================================================
# SAVE CSV
# ============================================================

def save_csv(products):

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(products)

    print(
        f"CSV generated: {OUTPUT_CSV}"
    )


# ============================================================
# PAGINATION AND COLLECTION SCRAPING
# ============================================================

async def scrape_collection_pages(
    crawler,
    collection_url,
    category,
    global_seen,
    remaining_total,
    image_session
):

    products = []

    initial_parts = urlsplit(
        collection_url
    )

    query_values = dict(
        parse_qsl(
            initial_parts.query,
            keep_blank_values=True
        )
    )

    try:

        initial_page = int(
            query_values.get("page", "1")
        )

    except ValueError:

        initial_page = 1

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=120000,
        delay_before_return_html=2,
        word_count_threshold=0,
    )

    for page_offset in range(
        MAX_PAGES_PER_COLLECTION
    ):

        if len(products) >= remaining_total:
            break

        page_number = initial_page + page_offset

        page_url = set_page_number(
            collection_url,
            page_number
        )

        print(
            f"\n[COLLECTION PAGE] {page_number} | {page_url}"
        )

        try:

            result = await crawler.arun(
                url=page_url,
                config=run_config
            )

        except Exception as exc:

            print(
                f"[PAGE ERROR] {page_url}: {exc}"
            )

            break

        if not result.success:

            print(
                f"[PAGE ERROR] {page_url}: "
                f"{result.error_message}"
            )

            break

        html = result.html or ""

        print(
            f"[PAGE HTML LENGTH] {len(html)}"
        )

        candidates = extract_listing_products(
            html,
            page_url
        )

        print(
            f"[LISTING PRODUCTS FOUND] {len(candidates)}"
        )

        if not candidates:

            print(
                "[NO PRODUCTS ON PAGE] Stopping pagination"
            )

            break

        new_candidates = [
            item
            for item in candidates
            if item["product_link"] not in global_seen
        ]

        if not new_candidates:

            print(
                "[NO NEW PRODUCTS ON PAGE] Stopping pagination"
            )

            break

        for item in new_candidates:

            if len(products) >= remaining_total:
                break

            link = item["product_link"]

            # Prevent duplicates.
            global_seen.add(link)

            print(
                f"\n[PRODUCT {len(products) + 1}/"
                f"{remaining_total}] {item['name']}"
            )

            # Validate URL before requesting product page.
            if not is_valid_product_url(link):

                print(
                    f"[SKIPPED INVALID PRODUCT URL] {link}"
                )

                continue

            try:

                product_result = await crawler.arun(
                    url=link,
                    config=run_config
                )

            except Exception as exc:

                print(
                    f"[PRODUCT ERROR] {link}: {exc}"
                )

                continue

            if not product_result.success:

                print(
                    f"[PRODUCT ERROR] {link}: "
                    f"{product_result.error_message}"
                )

                continue

            product_html = product_result.html or ""

            # Product page validation.
            url_ok, url_message = is_valid_product_page(
                product_result,
                product_html
            )

            if not url_ok:

                print(
                    f"[SKIPPED INVALID PRODUCT PAGE] "
                    f"{link} | {url_message}"
                )

                continue

            product = await extract_product_details(
                product_html,
                item,
                category,
                image_session
            )

            if product is None:
                continue

            products.append(product)

            print(
                f"[SAVED] {product['name']} | "
                f"₹{product['price']} | "
                f"MRP ₹{product['original_price']} | "
                f"{product['discount']}% OFF"
            )

        print(
            f"[COLLECTION VALID TOTAL] "
            f"{len(products)}/{remaining_total}"
        )

    return products


# ============================================================
# MAIN FUNCTION
# ============================================================

async def main():

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        headers=HEADERS
    )

    global_seen = set()
    all_products = []

    image_timeout = aiohttp.ClientTimeout(
        total=IMAGE_TIMEOUT
    )

    connector = aiohttp.TCPConnector(
        limit=20,
        ssl=False
    )

    async with aiohttp.ClientSession(
        timeout=image_timeout,
        connector=connector,
        headers=HEADERS
    ) as image_session:

        async with AsyncWebCrawler(
            config=browser_config
        ) as crawler:

            for collection_url, category in COLLECTIONS:

                if len(all_products) >= MAX_PRODUCTS:
                    break

                print(
                    "\n=================================================="
                )

                print(
                    f"SCRAPING CATEGORY: {category}"
                )

                print(
                    f"COLLECTION URL: {collection_url}"
                )

                print(
                    f"GLOBAL TOTAL: "
                    f"{len(all_products)}/{MAX_PRODUCTS}"
                )

                print(
                    "=================================================="
                )

                remaining = min(
                    MAX_PRODUCTS_PER_COLLECTION,
                    MAX_PRODUCTS - len(all_products)
                )

                collection_products = await scrape_collection_pages(
                    crawler,
                    collection_url,
                    category,
                    global_seen,
                    remaining,
                    image_session
                )

                all_products.extend(
                    collection_products
                )

                print(
                    f"[CATEGORY RUN TOTAL] "
                    f"{len(all_products)}/{MAX_PRODUCTS}"
                )

    save_json(all_products)
    save_csv(all_products)

    print(
        "\n========================================"
    )

    print(
        "PURE HOME AND LIVING SCRAPING COMPLETED"
    )

    print(
        "========================================"
    )

    print(
        f"Total valid products: {len(all_products)}"
    )

    print(
        f"JSON: {OUTPUT_JSON}"
    )

    print(
        f"CSV : {OUTPUT_CSV}"
    )

    return all_products


# ============================================================
# SCRAPY PROCESS
# ============================================================

def run_purehome_scraper():

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True
    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)

    logging.info(
        "Pure Home and Living browser process started."
    )

    logging.info("=" * 60)

    try:

        products = asyncio.run(
            main()
        )

        logging.info(
            "Pure Home and Living browser process finished. "
            "Products returned: %s",
            len(products)
        )

        return products

    except Exception:

        logging.exception(
            "Pure Home and Living browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class PureHomeSpider(scrapy.Spider):

    name = "purehome"

    allowed_domains = [
        "purehomeandliving.com",
        "www.purehomeandliving.com"
    ]

    async def start(self):

        self.logger.info(
            "Starting Pure Home and Living scraper through Scrapy..."
        )

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_purehome_scraper
            )

        with open(
            SPIDER_JSON,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=2
            )

        self.logger.info(
            "Pure Home and Living scraper returned %s products",
            len(products)
        )

        for product in products:

            if not isinstance(product, dict):
                continue

            if (
                not product.get("image_link")
                or not product.get("product_link")
            ):
                continue

            yield product

        self.logger.info(
            "Pure Home and Living spider completed"
        )


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    data = run_purehome_scraper()

    with open(
        SPIDER_JSON,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Saved in {SPIDER_JSON}"
    )

    print(
        "Total products:",
        len(data)
    )
