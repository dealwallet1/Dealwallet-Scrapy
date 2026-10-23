import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin

import pandas as pd
import requests
import scrapy
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURATION — retained from the original Dabur code
# ============================================================

BASE_URL = "https://www.daburshop.com"

COLLECTIONS = {
    "Grocery": [
        "https://www.daburshop.com/collections/edible-oils-ghee",
        "https://www.daburshop.com/collections/pickle-flavour-booster",
        "https://www.daburshop.com/collections/air-fresheners",
        "https://www.daburshop.com/collections/surface-cleaner",
        "https://www.daburshop.com/collections/toilet-cleaner",
    ],
    "Food": [
        "https://www.daburshop.com/collections/health-supplements",
        "https://www.daburshop.com/collections/ayurvedic-juice",
        "https://www.daburshop.com/collections/instant-energy-drink",
    ],
    "Health & Beauty": [
        "https://www.daburshop.com/collections/hair-care",
        "https://www.daburshop.com/collections/hair-oil",
        "https://www.daburshop.com/collections/bleach-facial-kit",
        "https://www.daburshop.com/collections/face-cleanser-toner",
        "https://www.daburshop.com/collections/skin-cream-lotion",
    ],
    "Ayurveda": [
        "https://www.daburshop.com/collections/bestsellers",
        "https://www.daburshop.com/collections/liver-gut-health",
        "https://www.daburshop.com/collections/cough-cold",
        "https://www.daburshop.com/collections/general-wellness",
        "https://www.daburshop.com/collections/bone-joint-health",
        "https://www.daburshop.com/collections/nutraceutical",
    ],
}

ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Dabur"
MAX_PRODUCTS = 100
JSON_FILE = "dabur_products.json"
CSV_FILE = "dabur_products.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

LOGGER = logging.getLogger(__name__)

# ============================================================
# ORIGINAL HELPER FUNCTIONS — selectors and business rules kept
# ============================================================


def clean_text(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def price_to_int(value):
    if value is None:
        return None
    value = str(value).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return None
    return int(float(match.group()))


def normalize_url(url):
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return urljoin(BASE_URL, url)


def calculate_discount(price, original_price):
    if (
        price is None
        or original_price is None
        or original_price <= 0
        or price >= original_price
    ):
        return 0
    return round(((original_price - price) / original_price) * 100)


def extract_rating(card):
    # Original rating selector, unchanged.
    badge = card.select_one(".bestseller-rating-badge")
    if not badge:
        return None
    rating = badge.get("data-average-rating") or badge.get("data-rating")
    try:
        rating = float(rating)
        return round(rating, 1) if rating > 0 else None
    except (TypeError, ValueError):
        return None


def extract_product_image_fallback(product_url):
    """Use Shopify product JSON only if the original collection image selector fails."""
    match = re.search(r"/products/([^/?#]+)", product_url or "")
    if not match:
        return None

    json_url = f"{BASE_URL}/products/{match.group(1)}.js"
    try:
        response = requests.get(json_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        product_data = response.json()

        image = product_data.get("featured_image")
        if not image:
            images = product_data.get("images") or []
            image = images[0] if images else None

        if not image:
            return None

        image = str(image).strip()
        if image.startswith("//"):
            return "https:" + image
        return urljoin(BASE_URL, image)

    except (requests.RequestException, ValueError, TypeError) as error:
        LOGGER.warning("[IMAGE FALLBACK ERROR] %s: %s", product_url, error)
        return None


def extract_description(product_url, fallback_name):
    # Original description selectors, fallback and request behavior preserved.
    try:
        response = requests.get(
            product_url,
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        description_element = (
            soup.select_one(".product__description")
            or soup.select_one(".product-description")
            or soup.select_one("[itemprop='description']")
            or soup.select_one(".rte")
        )
        if description_element:
            description = clean_text(
                description_element.get_text(" ", strip=True)
            )
            if description:
                return description

        # Original fallback to Shopify product JSON, retained for description only.
        match = re.search(r"/products/([^/?#]+)", product_url)
        if match:
            handle = match.group(1)
            json_url = f"{BASE_URL}/products/{handle}.js"
            json_response = requests.get(
                json_url,
                headers=HEADERS,
                timeout=20,
            )
            if json_response.ok:
                product_data = json_response.json()
                description_html = product_data.get("description", "")
                description_soup = BeautifulSoup(
                    description_html,
                    "html.parser",
                )
                description = clean_text(
                    description_soup.get_text(" ", strip=True)
                )
                if description:
                    return description
    except (requests.RequestException, ValueError) as error:
        LOGGER.warning("[DESCRIPTION ERROR] %s: %s", product_url, error)
    return fallback_name


def extract_product_name(product_url, fallback_name):
    """Read product name using the original product-page selectors."""
    try:
        response = requests.get(
            product_url,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Original title selectors and order, unchanged.
        title_element = (
            soup.select_one(".product__title h1.product_title")
            or soup.select_one(".product__title h1")
            or soup.select_one(".product__title a.product__title h2")
            or soup.select_one("#prod_title")
        )
        if title_element:
            product_name = clean_text(title_element.get_text(" ", strip=True))
            if product_name:
                return product_name

    except requests.RequestException as error:
        LOGGER.warning("[PRODUCT NAME ERROR] %s: %s", product_url, error)

    return fallback_name


def extract_product_prices(product_url):
    """Read price, MRP, and discount using the original price selectors only."""
    try:
        response = requests.get(
            product_url,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Original price-container selectors, unchanged.
        price_container = (
            soup.select_one("#price-template--22792675328131__main")
            or soup.select_one("[id^='price-'][role='status']")
        )
        if not price_container:
            LOGGER.warning(
                "[PRICE ERROR] Product price container not found: %s",
                product_url,
            )
            return None, None, 0

        # Original selling-price selector, unchanged.
        sale_element = price_container.select_one(
            ".price__sale .price-item--sale.price-item--last"
        )
        price = (
            price_to_int(sale_element.get_text(" ", strip=True))
            if sale_element
            else None
        )

        # Original original-price selectors, unchanged.
        original_element = price_container.select_one(
            ".price__sale s.price-item--regular"
        )
        if not original_element:
            original_element = price_container.select_one("s.price-item--regular")
        original_price = (
            price_to_int(original_element.get_text(" ", strip=True))
            if original_element
            else None
        )

        # Original discount selector, unchanged.
        discount_element = price_container.select_one(".price__badge-sale")
        discount_match = (
            re.search(
                r"(\d+(?:\.\d+)?)\s*%",
                discount_element.get_text(" ", strip=True),
            )
            if discount_element
            else None
        )

        if price is None or price <= 0:
            return None, None, 0

        if original_price is None or original_price <= price:
            original_price = None
            discount = 0
        elif discount_match:
            # Use the site's discount badge when one is available.
            discount = int(float(discount_match.group(1)))
        else:
            # If price and original price exist but the page has no discount badge,
            # calculate the discount manually without changing any selectors.
            discount = calculate_discount(price, original_price)
            LOGGER.info(
                "[DISCOUNT FALLBACK] Calculated manually for %s | "
                "Price=%s | Original=%s | Discount=%s%%",
                product_url,
                price,
                original_price,
                discount,
            )

        return price, original_price, discount

    except requests.RequestException as error:
        LOGGER.warning("[PRICE PAGE ERROR] %s: %s", product_url, error)
        return None, None, 0


# ============================================================
# ORIGINAL PRODUCT-CARD EXTRACTION — selectors unchanged
# ============================================================


def scrape_product_card(card, category):
    title_element = card.select_one(
        "h3.card__heading a.full-unstyled-link"
    )
    if not title_element:
        title_element = card.select_one(
            ".card__heading a[href*='/products/']"
        )
    if not title_element:
        return None

    name = clean_text(title_element.get_text(" ", strip=True))
    product_link = normalize_url(title_element.get("href"))
    if not name or not product_link:
        return None

    # Original product-name extraction retained.
    name = extract_product_name(product_link, name)

    # Original image selector and attribute priority retained.
    image_element = card.select_one(".card__media img")
    image_link = None
    if image_element:
        image_link = image_element.get("src") or image_element.get("data-src")
        if not image_link:
            srcset = image_element.get("srcset", "")
            if srcset:
                image_link = srcset.split(",")[0].strip().split()[0]
        image_link = normalize_url(image_link)

    # Keep the original image selector first. If it has no usable URL, use Shopify
    # product JSON as a fallback so valid products do not end with image_link=None.
    if not image_link:
        image_link = extract_product_image_fallback(product_link)
        if image_link:
            LOGGER.info("[IMAGE FALLBACK] Shopify product JSON used for %s", product_link)

    # Original product-page price extraction retained; no Shopify price fallback.
    price, original_price, discount = extract_product_prices(product_link)

    # Original validation rules retained.
    if price is None or price <= 0:
        LOGGER.info("[SKIPPED - INVALID PRODUCT-PAGE PRICE] %s", name)
        return None

    if original_price is None or original_price <= price:
        LOGGER.info("[SKIPPED - ORIGINAL PRICE NOT FOUND OR NO DISCOUNT] %s", name)
        return None

    if discount <= 0:
        LOGGER.info("[SKIPPED - ZERO DISCOUNT] %s", name)
        return None

    ratings = extract_rating(card)
    description = extract_description(product_link, name)
    created_at = datetime.now(timezone.utc).isoformat()

    # Original output structure and values retained.
    return {
        "name": name,
        "price": int(price),
        "currency": "₹",
        "original_price": int(original_price),
        "discount": int(discount),
        "ratings": ratings,
        "description": description,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": category,
        "created_at": created_at,
    }


def scrape_collection(collection_url, category):
    """Use the original requests-based collection flow and original selectors."""
    products = []
    page_number = 1
    seen_links = set()

    while len(products) < MAX_PRODUCTS:
        if page_number == 1:
            page_url = collection_url
        else:
            separator = "&" if "?" in collection_url else "?"
            page_url = f"{collection_url}{separator}page={page_number}"

        LOGGER.info("[CATEGORY] %s", category)
        LOGGER.info("[PAGE %s] %s", page_number, page_url)

        try:
            response = requests.get(
                page_url,
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            LOGGER.warning("[PAGE ERROR] %s: %s", page_url, error)
            break

        soup = BeautifulSoup(response.text, "html.parser")
        # Original collection card selectors, unchanged.
        cards = soup.select('li[id^="collection_list-"]')
        if not cards:
            cards = soup.select("li.grid__item .product-card-wrapper")

        if not cards:
            LOGGER.info("[STOP] No product cards found on page %s", page_number)
            break

        LOGGER.info("[CARDS FOUND] %s", len(cards))
        new_products = 0

        for card in cards:
            if len(products) >= MAX_PRODUCTS:
                break

            item = scrape_product_card(card, category)
            if not item:
                continue

            product_url = item["product_link"]
            if product_url in seen_links:
                continue

            seen_links.add(product_url)
            products.append(item)
            new_products += 1

            LOGGER.info(
                "[%s/%s] %s | ₹%s | MRP ₹%s | Discount=%s%% | Category=%s",
                len(products),
                MAX_PRODUCTS,
                item["name"],
                item["price"],
                item["original_price"],
                item["discount"],
                item["categories_id"],
            )
            LOGGER.info("[URL TOTAL] %s: %s/%s", collection_url, len(products), MAX_PRODUCTS)

        # Preserve the original stop condition.
        if new_products == 0:
            LOGGER.info("[STOP] No new products on page %s", page_number)
            break

        page_number += 1

    LOGGER.info("[COLLECTION COMPLETE] %s | products=%s", collection_url, len(products))
    return products


def scrape_dabur_products():
    """Run the original category/collection loop and global 100-product limit."""
    all_products = []
    seen_links = set()

    for category, collection_urls in COLLECTIONS.items():
        if len(all_products) >= MAX_PRODUCTS:
            break

        LOGGER.info("\n%s", "=" * 55)
        LOGGER.info("STARTING CATEGORY: %s", category)
        LOGGER.info("%s", "=" * 55)

        for collection_url in collection_urls:
            if len(all_products) >= MAX_PRODUCTS:
                break

            products = scrape_collection(collection_url, category)
            for product in products:
                link = product["product_link"]
                if link in seen_links:
                    continue

                seen_links.add(link)
                all_products.append(product)
                LOGGER.info("[GLOBAL TOTAL] %s/%s", len(all_products), MAX_PRODUCTS)

                if len(all_products) >= MAX_PRODUCTS:
                    break

    return all_products[:MAX_PRODUCTS]


def run_dabur_scraper():
    """Worker entry point; runs the original requests-based scraping logic."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    LOGGER.info("Dabur worker started (original extraction logic).")
    products = scrape_dabur_products()
    LOGGER.info("Dabur worker finished | Products=%s", len(products))
    return products


def save_outputs(products):
    with open(JSON_FILE, "w", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=4)
    pd.DataFrame(products).to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
    LOGGER.info("JSON generated: %s", JSON_FILE)
    LOGGER.info("CSV generated: %s", CSV_FILE)
    LOGGER.info("Products written: %s", len(products))


# ============================================================
# SCRAPY SPIDER — ProcessPoolExecutor wrapper, like Boveee
# ============================================================


class DaburSpider(scrapy.Spider):
    name = "dabur"
    allowed_domains = ["daburshop.com", "www.daburshop.com"]

    async def start(self):
        self.logger.info("=" * 70)
        self.logger.info("Starting Dabur scraper through ProcessPoolExecutor...")
        self.logger.info("=" * 70)

        loop = asyncio.get_running_loop()
        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                products = await loop.run_in_executor(executor, run_dabur_scraper)
        except Exception:
            self.logger.exception("Dabur scraper worker failed")
            return

        # Do not add stricter required-field validation here; the original code
        # only skips products based on its price/original-price/discount rules.
        for product in products:
            yield product

        save_outputs(products)
        self.logger.info("=" * 70)
        self.logger.info("Dabur spider completed.")
        self.logger.info("Valid products: %s", len(products))
        self.logger.info("Output JSON: %s", JSON_FILE)
        self.logger.info("Output CSV: %s", CSV_FILE)
        self.logger.info("=" * 70)


if __name__ == "__main__":
    data = run_dabur_scraper()
    save_outputs(data)
    print("=" * 70)
    print("DABUR SCRAPING COMPLETED")
    print("Valid products:", len(data))
    print("JSON:", JSON_FILE)
    print("CSV:", CSV_FILE)
    print("=" * 70)
