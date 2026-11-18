import asyncio
import logging
import sys
from concurrent.futures import ProcessPoolExecutor

import scrapy
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import json
import re
import time
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urlunparse

# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.giva.co"

COLLECTIONS = [
    {
        "url": "https://www.giva.co/collections/gifts-for-her",
        "category": "Beauty",
        "max_products": 100
    },
    {
        "url": "https://www.giva.co/collections/kids-jewellery",
        "category": "Baby & Kids",
        "max_products": 50
    }
]

JSON_FILE = "giva_products.json"
CSV_FILE = "giva_products.csv"

ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Giva"

MAX_PAGES = 30
REQUEST_TIMEOUT = 45
REQUEST_DELAY = 1.5
IMAGE_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/json,*/*"
}

session = requests.Session()
session.headers.update(HEADERS)

# ============================================================
# PLAYWRIGHT RATING BROWSER
# ============================================================

playwright_instance = None
rating_browser = None
rating_page = None


def start_rating_browser():
    global playwright_instance, rating_browser, rating_page
    try:
        playwright_instance = sync_playwright().start()
        rating_browser = playwright_instance.chromium.launch(headless=True)
        rating_page = rating_browser.new_page(
            user_agent=HEADERS["User-Agent"],
            locale="en-IN"
        )
        print("[PLAYWRIGHT] Rating browser started.")
        return True
    except Exception as error:
        print(f"[PLAYWRIGHT ERROR] Could not start rating browser: {error}")
        stop_rating_browser()
        return False


def stop_rating_browser():
    global playwright_instance, rating_browser, rating_page
    try:
        if rating_page:
            rating_page.close()
    except Exception:
        pass
    try:
        if rating_browser:
            rating_browser.close()
    except Exception:
        pass
    try:
        if playwright_instance:
            playwright_instance.stop()
    except Exception:
        pass
    rating_page = None
    rating_browser = None
    playwright_instance = None


def extract_dynamic_rating(product_url):
    if rating_page is None or not product_url:
        return None
    try:
        rating_page.goto(
            product_url,
            wait_until="domcontentloaded",
            timeout=60000
        )
        rating_page.wait_for_timeout(3000)
        rating_selector = (
            'button[aria-label="See ratings and reviews"] '
            'span.label-large'
        )
        try:
            rating_page.wait_for_selector(
                rating_selector,
                state="visible",
                timeout=12000
            )
        except PlaywrightTimeoutError:
            rating_selector = (
                'button[aria-label*="ratings and reviews"] '
                'span.label-large'
            )
            rating_page.wait_for_selector(
                rating_selector,
                state="visible",
                timeout=5000
            )
        rating_text = rating_page.locator(
            rating_selector
        ).first.inner_text(timeout=5000).strip()
        rating = extract_float(rating_text)
        if rating is not None:
            print(f"[RATING - PLAYWRIGHT] {rating_text}")
            print(f"[EXACT RATING] {rating}")
            return rating
    except PlaywrightTimeoutError:
        print("[RATING - PLAYWRIGHT] Rating element not found.")
    except Exception as error:
        print(f"[RATING - PLAYWRIGHT ERROR] {error}")
    return None


# ============================================================
# MANDATORY FIELDS
# ============================================================

MANDATORY_FIELDS = [
    "name",
    "price",
    "currency",
    "original_price",
    "description",
    "image_link",
    "product_link",
    "organization_id",
    "store_id",
    "categories_id"
]

# ============================================================
# COMMON FUNCTIONS
# ============================================================

def get_soup(url):
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except requests.RequestException as error:
        print(f"[REQUEST ERROR] {url}: {error}")
        return None


def clean_text(value):
    if value is None:
        return ""
    value = str(value)
    value = BeautifulSoup(
        value,
        "html.parser"
    ).get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def extract_integer(value):
    if value is None:
        return None
    value = clean_text(value)
    match = re.search(r"[\d,]+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", "")))
    except (ValueError, TypeError):
        return None


# ============================================================
# EXACT RATING EXTRACTION
# ============================================================

def extract_float(value):
    if value is None:
        return None
    value = clean_text(value)
    match = re.search(r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)", value)
    if not match:
        return None
    try:
        rating = float(match.group(1))
        if 0 < rating <= 5:
            return rating
        return None
    except (ValueError, TypeError):
        return None


def extract_rating_details(soup, product_url=None):
    # Method 1: Dynamically rendered rating.
    dynamic_rating = extract_dynamic_rating(product_url)
    if dynamic_rating is not None:
        return dynamic_rating

    # Method 2: Exact rating from GIVA rating button.
    rating_button = soup.select_one(
        'button[aria-label="See ratings and reviews"]'
    )
    if not rating_button:
        rating_button = soup.select_one(
            'button[aria-label*="ratings and reviews"]'
        )
    if rating_button:
        rating_element = rating_button.select_one("span.label-large")
        if rating_element:
            rating_text = rating_element.get_text(" ", strip=True)
            rating = extract_float(rating_text)
            if rating is not None:
                print(f"[RATING - BUTTON] {rating_text}")
                print(f"[EXACT RATING] {rating}")
                return rating

    # Method 3: Fallback selectors.
    rating_selectors = [
        "[data-average-rating]",
        "[data-rating]",
        ".loox-rating",
        ".rating-value",
        ".product-rating",
        "[aria-label*='out of 5']"
    ]
    for selector in rating_selectors:
        elements = soup.select(selector)
        for element in elements:
            rating_value = (
                element.get("data-average-rating")
                or element.get("data-rating")
                or element.get("aria-label")
                or element.get_text(" ", strip=True)
            )
            rating = extract_float(rating_value)
            if rating is not None:
                print(f"[RATING - FALLBACK] {rating_value}")
                print(f"[EXACT RATING] {rating}")
                return rating

    print("[FINAL RATING] None")
    return None


# ============================================================
# DISCOUNT
# ============================================================

def extract_discount(price, original_price):
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
# URL FUNCTIONS
# ============================================================

def absolute_url(url):
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE_URL, url)


def get_text_by_selectors(soup, selectors):
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            value = clean_text(element.get_text(" ", strip=True))
            if value:
                return value
    return None


def validate_product_url(url):
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.hostname not in ("giva.co", "www.giva.co"):
            return False
        if not parsed.path.startswith("/products/"):
            return False
        return True
    except Exception:
        return False


# ============================================================
# PRODUCT URL CORRECTION
# ============================================================

def normalize_product_name(name):
    if not name:
        return ""
    name = clean_text(name).lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def get_product_api_data(product_url):
    if not validate_product_url(product_url):
        return None
    parsed = urlparse(product_url)
    api_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip("/") + ".js",
        "",
        "",
        ""
    ))
    try:
        response = session.get(api_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("title"):
            return data
    except (requests.RequestException, ValueError) as error:
        print(f"[PRODUCT API ERROR] {error}")
    return None


def search_giva_product(product_name):
    if not product_name:
        return None
    search_url = f"{BASE_URL}/search/suggest.json"
    params = {
        "q": product_name,
        "resources[type]": "product",
        "resources[limit]": 10
    }
    try:
        response = session.get(
            search_url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        products = (
            data.get("resources", {})
            .get("results", {})
            .get("products", [])
        )
        expected_name = normalize_product_name(product_name)
        for item in products:
            result_name = item.get("title", "")
            result_normalized = normalize_product_name(result_name)

            # Only accept an exact normalized title match.
            if result_normalized != expected_name:
                continue

            handle = item.get("handle")
            if handle:
                corrected_url = f"{BASE_URL}/products/{handle}"
            else:
                corrected_url = item.get("url")
                if corrected_url:
                    corrected_url = absolute_url(corrected_url)

            if not validate_product_url(corrected_url):
                continue

            print(f"[URL SEARCH MATCH] {result_name}")
            print(f"[CORRECTED URL] {corrected_url}")
            return corrected_url
    except (requests.RequestException, ValueError) as error:
        print(f"[GIVA SEARCH ERROR] {error}")
    return None


def resolve_product_url(product_name, candidate_url):
    print("[URL VALIDATION] Checking product URL...")

    # First check the collection-page URL.
    product_data = get_product_api_data(candidate_url)
    if product_data:
        api_title = product_data.get("title", "")
        expected_name = normalize_product_name(product_name)
        actual_name = normalize_product_name(api_title)
        if expected_name == actual_name:
            handle = product_data.get("handle")
            if handle:
                correct_url = f"{BASE_URL}/products/{handle}"
            else:
                correct_url = candidate_url
            print("[URL VALIDATED] Product title matches.")
            print(f"[PRODUCT NAME] {api_title}")
            print(f"[PRODUCT URL] {correct_url}")
            return correct_url

        print("[URL MISMATCH] Collection name and API title differ.")
        print(f"[EXPECTED] {product_name}")
        print(f"[FOUND] {api_title}")
    else:
        print("[URL ERROR] Shopify product API unavailable.")

    # Search GIVA for the correct product.
    print("[URL CORRECTION] Searching GIVA for matching product...")
    corrected_url = search_giva_product(product_name)
    if not corrected_url:
        print("[URL REJECTED] No exact matching product URL found.")
        return None

    # Verify the corrected URL through Shopify API.
    corrected_data = get_product_api_data(corrected_url)
    if not corrected_data:
        print("[URL REJECTED] Corrected product API unavailable.")
        return None

    corrected_title = corrected_data.get("title", "")
    if (
        normalize_product_name(corrected_title)
        != normalize_product_name(product_name)
    ):
        print("[URL REJECTED] Corrected product title mismatch.")
        return None

    print("[URL CORRECTED AND VALIDATED]")
    print(f"[PRODUCT NAME] {corrected_title}")
    print(f"[PRODUCT URL] {corrected_url}")
    return corrected_url


# ============================================================
# IMAGE VALIDATION
# ============================================================

def validate_image_url(image_url):
    if not image_url:
        return False
    try:
        parsed = urlparse(image_url)
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc:
            return False
        response = session.get(
            image_url,
            timeout=IMAGE_TIMEOUT,
            stream=True
        )
        try:
            if response.status_code != 200:
                return False
            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()
            if not content_type.startswith("image/"):
                return False
            return True
        finally:
            response.close()
    except requests.RequestException:
        return False


def extract_image(image_element):
    if not image_element:
        return None
    image_url = (
        image_element.get("data-src")
        or image_element.get("data-original")
        or image_element.get("src")
    )
    srcset = image_element.get("srcset")
    if srcset:
        candidates = []
        for item in srcset.split(","):
            parts = item.strip().split()
            if not parts:
                continue
            candidate_url = parts[0]
            width = 0
            if len(parts) > 1:
                match = re.search(r"(\d+)w", parts[1])
                if match:
                    width = int(match.group(1))
            candidates.append((width, candidate_url))
        if candidates:
            candidates.sort(
                key=lambda item: item[0],
                reverse=True
            )
            image_url = candidates[0][1]
    return absolute_url(image_url) if image_url else None


# ============================================================
# SOLD OUT VALIDATION
# ============================================================

def is_sold_out(soup):
    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
            items = data if isinstance(data, list) else [data]
            index = 0
            while index < len(items):
                item = items[index]
                index += 1
                if not isinstance(item, dict):
                    continue
                graph = item.get("@graph", [])
                if isinstance(graph, list):
                    items.extend(graph)
                offers = item.get("offers", {})
                if isinstance(offers, dict):
                    offers = [offers]
                if isinstance(offers, list):
                    for offer in offers:
                        if not isinstance(offer, dict):
                            continue
                        availability = str(
                            offer.get("availability", "")
                        ).lower()
                        if (
                            "outofstock" in availability
                            or "soldout" in availability
                        ):
                            return True
        except (
            json.JSONDecodeError,
            TypeError,
            AttributeError
        ):
            continue

    sold_out_selectors = [
        'button[name="add"][disabled]',
        'button[data-sold-out]',
        '.product-form__submit[disabled]',
        '.product-form__submit[aria-disabled="true"]',
        '.sold-out',
        '.product__inventory--sold-out',
        '[data-availability="sold_out"]'
    ]
    for selector in sold_out_selectors:
        element = soup.select_one(selector)
        if element:
            return True

    buttons = soup.select("button, .product-form__submit")
    for button in buttons:
        button_text = clean_text(
            button.get_text(" ", strip=True)
        ).lower()
        if button_text in (
            "sold out",
            "out of stock",
            "currently unavailable"
        ):
            return True
    return False


# ============================================================
# OUT OF DELIVERY VALIDATION
# ============================================================

def is_out_of_delivery(soup):
    unavailable_messages = [
        "delivery is not available",
        "delivery unavailable",
        "not deliverable to your location",
        "currently not deliverable",
        "we do not deliver to this location",
        "cannot be delivered to your location",
        "delivery is unavailable for this product"
    ]
    delivery_selectors = [
        ".delivery-error",
        ".delivery-unavailable",
        "[data-delivery-status='unavailable']"
    ]
    for selector in delivery_selectors:
        for element in soup.select(selector):
            text = clean_text(
                element.get_text(" ", strip=True)
            ).lower()
            if any(
                message in text
                for message in unavailable_messages
            ):
                return True

    page_text = clean_text(
        soup.get_text(" ", strip=True)
    ).lower()
    for message in unavailable_messages:
        if message in page_text:
            return True
    return False


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

def extract_description(soup):
    description_selectors = [
        "div.non-gold-desc-content#non-gold-desc-content-full",
        ".non-gold-desc #non-gold-desc-content-full",
        ".product__description.rte",
        ".product__description",
        ".product-description",
        ".product-single__description",
        "[data-product-description]",
        "#description"
    ]
    for selector in description_selectors:
        for element in soup.select(selector):
            description_soup = BeautifulSoup(
                str(element),
                "html.parser"
            )
            for unwanted in description_soup.select(
                "script, style, .non-gold-desc-btn"
            ):
                unwanted.decompose()
            description = description_soup.get_text(
                separator=" ",
                strip=True
            )
            description = re.sub(
                r"\s+",
                " ",
                description
            ).strip()
            if description and description.lower() not in (
                "product description",
                "show more",
                "show less"
            ):
                return description

    # JSON-LD fallback.
    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
            items = data if isinstance(data, list) else [data]
            index = 0
            while index < len(items):
                item = items[index]
                index += 1
                if not isinstance(item, dict):
                    continue
                graph = item.get("@graph", [])
                if isinstance(graph, list):
                    items.extend(graph)
                item_type = item.get("@type", [])
                if isinstance(item_type, str):
                    item_type = [item_type]
                if "Product" in item_type:
                    description = clean_text(
                        item.get("description", "")
                    )
                    if description:
                        return description
        except (
            json.JSONDecodeError,
            TypeError,
            AttributeError
        ):
            continue
    return None


# ============================================================
# COLLECTION PAGE PRODUCT EXTRACTION
# ============================================================

def extract_collection_products(soup):
    products = []
    product_cards = soup.select("li.grid__item")
    print(
        f"[COLLECTION] Product cards found: "
        f"{len(product_cards)}"
    )
    for card in product_cards:
        # Product name.
        name_element = card.select_one("input#hidden_title")
        name = None
        if name_element:
            name = clean_text(name_element.get("value", ""))
        if not name:
            name = get_text_by_selectors(
                card,
                [
                    "h3.card__heading a",
                    ".card__heading a",
                    "h3.card__heading",
                    ".card__heading"
                ]
            )
        if not name:
            continue

        # Product URL.
        link_element = card.select_one("h3.card__heading a")
        if not link_element:
            link_element = card.select_one("a[href*='/products/']")
        product_url = (
            absolute_url(link_element.get("href"))
            if link_element else None
        )
        if not validate_product_url(product_url):
            print(f"[SKIP] Invalid product URL: {product_url}")
            continue

        # Current price.
        price_element = card.select_one("input#hidden_price")
        price = None
        if price_element:
            price = extract_integer(price_element.get("value"))
        if price is None:
            price_text = get_text_by_selectors(
                card,
                [
                    ".price-item--sale",
                    ".price-item--regular",
                    ".price__sale .price-item",
                    ".price__regular .price-item"
                ]
            )
            price = extract_integer(price_text)

        # Original price.
        original_price_text = get_text_by_selectors(
            card,
            [
                "s#gold_card_regular_price .line-through",
                "s#gold_card_regular_price",
                "s.price-item--regular",
                ".price__compare .price-item",
                ".price-item--compare"
            ]
        )
        original_price = extract_integer(original_price_text)
        if original_price is None:
            original_element = card.select_one(
                "input#hidden_original_price"
            )
            if original_element:
                original_price = extract_integer(
                    original_element.get("value")
                )
        if original_price is None and price is not None:
            original_price = price

        # Product image.
        image_element = card.select_one(".card__media img")
        if not image_element:
            image_element = card.select_one("img")
        image_url = extract_image(image_element)

        # Collection-page rating.
        rating_element = card.select_one(".loox-rating")
        rating = None
        if rating_element:
            rating_value = (
                rating_element.get("data-rating")
                or rating_element.get_text(" ", strip=True)
            )
            rating = extract_float(rating_value)

        products.append({
            "name": name,
            "price": price,
            "currency": "₹",
            "original_price": original_price,
            "discount": extract_discount(
                price,
                original_price
            ),
            "ratings": rating,
            "description": None,
            "image_link": image_url,
            "product_link": product_url,
            "organization_id": ORGANIZATION_ID,
            "store_id": STORE_ID,
            "categories_id": None,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat()
        })
    return products


# ============================================================
# PRODUCT PAGE DETAILS
# ============================================================

def extract_product_details(product_url):
    if not validate_product_url(product_url):
        print("[SKIP] Invalid product URL.")
        return None

    soup = get_soup(product_url)
    if not soup:
        return None

    # Sold-out validation.
    if is_sold_out(soup):
        print("[SKIP] Product is SOLD OUT.")
        return None

    # Delivery validation.
    if is_out_of_delivery(soup):
        print("[SKIP] Product is OUT OF DELIVERY.")
        return None

    # Description.
    description = extract_description(soup)

    # Product name.
    name = get_text_by_selectors(
        soup,
        [
            "h1.product_title",
            ".product__title h1",
            "h1.product__title",
            "h1"
        ]
    )

    # Current selling price.
    price_text = get_text_by_selectors(
        soup,
        [
            ".price__sale .price-item--sale.price-item--last",
            ".price-item--sale.price-item--last",
            ".price__sale .price-item--last",
            ".price-item--regular"
        ]
    )
    price = extract_integer(price_text)

    # Original price.
    original_price_text = get_text_by_selectors(
        soup,
        [
            "s#gold_card_regular_price .line-through",
            "s#gold_card_regular_price",
            "s.price-item--regular",
            ".price__compare .price-item",
            ".price-item--compare"
        ]
    )
    original_price = extract_integer(original_price_text)

    # Discount.
    discount_text = get_text_by_selectors(
        soup,
        [
            "#discount_percent",
            ".price__badge-sale",
            ".off"
        ]
    )
    discount_match = re.search(
        r"\d+",
        discount_text or ""
    )
    discount = (
        int(discount_match.group(0))
        if discount_match else None
    )
    if (
        discount is None
        and price is not None
        and original_price is not None
    ):
        discount = extract_discount(
            price,
            original_price
        )

    # Product image.
    image_element = soup.select_one(".product__media img")
    if not image_element:
        image_element = soup.select_one(".product-gallery img")
    if not image_element:
        image_element = soup.select_one("img.product__image")
    if not image_element:
        image_element = soup.select_one(".product__title img")
    image_link = extract_image(image_element)

    # Exact product-page rating.
    ratings = extract_rating_details(
        soup,
        product_url
    )

    return {
        "description": description,
        "name": name,
        "image_link": image_link,
        "ratings": ratings,
        "price": price,
        "original_price": original_price,
        "discount": discount
    }


# ============================================================
# MANDATORY FIELD VALIDATION
# ============================================================

def validate_mandatory_fields(product):
    missing_fields = []
    for field in MANDATORY_FIELDS:
        value = product.get(field)
        if value is None or value == "":
            missing_fields.append(field)

    if missing_fields:
        print(
            f"[SKIP] Missing mandatory fields: "
            f"{', '.join(missing_fields)}"
        )
        return False

    # Product name validation.
    if len(product["name"].strip()) < 2:
        print("[SKIP] Invalid product name.")
        return False

    # Selling price validation.
    if (
        not isinstance(product["price"], (int, float))
        or product["price"] <= 0
    ):
        print("[SKIP] Invalid selling price.")
        return False

    # Original price validation.
    if (
        not isinstance(product["original_price"], (int, float))
        or product["original_price"] <= 0
    ):
        print("[SKIP] Invalid original price.")
        return False

    # Discount validation.
    if product["price"] >= product["original_price"]:
        print(
            "[SKIP] No discount / selling price equals "
            "original price."
        )
        return False

    # Description validation.
    if len(product["description"].strip()) < 5:
        print("[SKIP] Invalid product description.")
        return False

    # Product URL validation.
    if not validate_product_url(product["product_link"]):
        print("[SKIP] Invalid product URL.")
        return False

    # Image validation.
    if not validate_image_url(product["image_link"]):
        print("[SKIP] Invalid or inaccessible product image.")
        return False

    return True


# ============================================================
# MAIN SCRAPER
# ============================================================

def scrape_giva():
    all_products = []
    print("=" * 60)
    print("GIVA PRODUCT SCRAPER STARTED")
    print("=" * 60)

    for collection in COLLECTIONS:
        collection_url = collection["url"]
        category_name = collection["category"]
        max_products = collection["max_products"]

        print("\n" + "=" * 60)
        print(f"CATEGORY: {category_name}")
        print(f"URL: {collection_url}")
        print(f"PRODUCT LIMIT: {max_products}")
        print("=" * 60)

        category_products = []
        seen_urls = set()

        for page_number in range(1, MAX_PAGES + 1):
            if len(category_products) >= max_products:
                break

            page_url = f"{collection_url}?page={page_number}"
            print(f"\n[PAGE] {page_number}")
            print(f"[URL] {page_url}")

            soup = get_soup(page_url)
            if not soup:
                print("[STOP] Unable to load collection page.")
                break

            page_products = extract_collection_products(soup)
            if not page_products:
                print("[STOP] No products found on this page.")
                break

            new_products = 0
            for product in page_products:
                if len(category_products) >= max_products:
                    break

                # ----------------------------------------
                # PRODUCT URL CORRECTION
                # ----------------------------------------

                candidate_url = product.get("product_link")
                if not candidate_url:
                    print("[SKIP] Product URL missing.")
                    continue

                product_url = resolve_product_url(
                    product["name"],
                    candidate_url
                )
                if not product_url:
                    print("[SKIP] Correct product URL not found.")
                    continue

                if product_url in seen_urls:
                    print("[SKIP] Duplicate product URL.")
                    continue

                seen_urls.add(product_url)
                product["product_link"] = product_url
                new_products += 1

                print(
                    f"\n[PRODUCT "
                    f"{len(category_products) + 1}/"
                    f"{max_products}] {product['name']}"
                )

                # ----------------------------------------
                # PRODUCT PAGE EXTRACTION
                # ----------------------------------------

                details = extract_product_details(product_url)
                if not details:
                    print("[SKIP] Product details unavailable.")
                    continue

                # Product-page title.
                if details.get("name"):
                    page_title = details["name"]

                    # Do not silently replace the verified
                    # collection/API product identity.
                    if (
                        normalize_product_name(page_title)
                        != normalize_product_name(product["name"])
                    ):
                        print(
                            "[SKIP] Product page title does not "
                            "match verified product."
                        )
                        print(f"[EXPECTED] {product['name']}")
                        print(f"[FOUND] {page_title}")
                        continue

                    product["name"] = page_title

                # Product-page prices.
                product["price"] = details.get("price")
                product["original_price"] = (
                    details.get("original_price")
                )
                product["discount"] = details.get("discount")

                if product["discount"] is None:
                    product["discount"] = extract_discount(
                        product["price"],
                        product["original_price"]
                    )

                # Product-page description.
                product["description"] = (
                    details.get("description")
                )

                # Product-page image.
                if details.get("image_link"):
                    product["image_link"] = (
                        details["image_link"]
                    )

                # Product-page exact rating.
                if details.get("ratings") is not None:
                    product["ratings"] = (
                        details["ratings"]
                    )

                # Category assignment.
                product["categories_id"] = category_name

                # Fixed fields.
                product["organization_id"] = ORGANIZATION_ID
                product["store_id"] = STORE_ID

                # Final mandatory validation.
                if not validate_mandatory_fields(product):
                    print(f"[REJECTED] {product['name']}")
                    continue

                # Timestamp.
                product["created_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

                # Accept validated product.
                category_products.append(product)

                print("[VALIDATED] Product accepted.")
                print(f"[NAME] {product['name']}")
                print(f"[PRICE] ₹{product['price']}")
                print(
                    f"[ORIGINAL PRICE] "
                    f"₹{product['original_price']}"
                )
                print(f"[DISCOUNT] {product['discount']}%")
                print(f"[RATING] {product['ratings']}")
                print(f"[CATEGORY] {category_name}")
                print(
                    f"[DESCRIPTION] "
                    f"{product['description'][:150]}"
                )
                print(f"[IMAGE] {product['image_link']}")
                print(f"[PRODUCT URL] {product['product_link']}")

                time.sleep(REQUEST_DELAY)

            print(f"\n[PAGE PRODUCTS] {len(page_products)}")
            print(
                f"[CATEGORY TOTAL] "
                f"{len(category_products)}/{max_products}"
            )

            if new_products == 0:
                print("[STOP] No new products found.")
                break

            time.sleep(1)

        print(
            f"\n[COMPLETED CATEGORY] "
            f"{category_name}: "
            f"{len(category_products)} products"
        )
        all_products.extend(category_products)

    # ========================================================
    # SAVE JSON AND CSV
    # ========================================================

    if not all_products:
        print("[ERROR] No valid products were scraped.")
        return []

    output_fields = [
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
        "created_at"
    ]

    final_products = [
        {
            field: product.get(field)
            for field in output_fields
        }
        for product in all_products
    ]

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8"
    ) as json_file:
        json.dump(
            final_products,
            json_file,
            indent=4,
            ensure_ascii=False
        )

    dataframe = pd.DataFrame(
        final_products,
        columns=output_fields
    )
    dataframe.to_csv(
        CSV_FILE,
        index=False,
        encoding="utf-8-sig"
    )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print("\n" + "=" * 60)
    print("SCRAPING COMPLETED")
    print("=" * 60)
    print(f"TOTAL VALID PRODUCTS: {len(final_products)}")
    print(f"JSON FILE: {JSON_FILE}")
    print(f"CSV FILE: {CSV_FILE}")

    for collection in COLLECTIONS:
        category = collection["category"]
        count = sum(
            1
            for product in final_products
            if product["categories_id"] == category
        )
        print(f"{category}: {count} products")

    return final_products


# ============================================================
# SCRAPY BROWSER PROCESS RUNNER
# ============================================================

def run_giva_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True
    )

    print("=" * 60)
    print("GIVA BROWSER PROCESS STARTED")
    print("=" * 60)

    try:
        start_rating_browser()
        products = scrape_giva()

        if products is None:
            products = []

        logging.info(
            "GIVA browser process finished. Products: %s",
            len(products)
        )
        return products

    except Exception:
        logging.exception("GIVA browser process failed.")
        raise

    finally:
        stop_rating_browser()


# ============================================================
# SCRAPY SPIDER
# ============================================================

class GivaSpider(scrapy.Spider):
    name = "giva"
    
    allowed_domains = ["giva.co", "www.giva.co"]

    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting GIVA Playwright scraper...")
        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_giva_scraper
            )

        if not products:
            self.logger.error("No valid GIVA products were returned.")
            return

        self.logger.info(
            "GIVA scraper returned %s products.",
            len(products)
        )

        for product in products:
            if not product.get("product_link"):
                continue

            if not product.get("image_link"):
                continue

            yield product

        self.logger.info("=" * 60)
        self.logger.info("GIVA spider completed.")
        self.logger.info("=" * 60)