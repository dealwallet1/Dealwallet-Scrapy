import asyncio
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import scrapy
from playwright.async_api import async_playwright


OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "autofurnish_products.json",
)

BASE_URL = "https://www.autofurnish.com"

COLLECTION_URLS = {
    "Automobile": [
        "https://www.autofurnish.com/collections/oem-style-factory-fitted-seat-covers",
        "https://www.autofurnish.com/collections/waterproof-car-body-cover",
        "https://www.autofurnish.com/collections/car-body-covers?utm_dt_top_icon_pos_2",
        "https://www.autofurnish.com/collections/steering-wheel-covers",
        "https://www.autofurnish.com/collections/bike-body-covers",
        "https://www.autofurnish.com/collections/ltm-car-foot-mats",
        "https://www.autofurnish.com/collections/car-foot-mats",
        "https://www.autofurnish.com/collections/bike-accessories",
        "https://www.autofurnish.com/collections/ev-car-accessories"
    ],
}

MAX_PAGES = 2
PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 20000
LISTING_DELAY_SECONDS = 2
DETAIL_DELAY_SECONDS = 1
MAX_NAVIGATION_RETRIES = 3


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_price(text):
    if not text:
        return None

    cleaned = text.replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)

    if not match:
        return None

    value = float(match.group(0))
    return int(value) if value.is_integer() else value


def extract_discount(text):
    if not text:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)

    if not match:
        return None

    value = float(match.group(1))
    return int(value) if value.is_integer() else value


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    if original_price <= 0 or original_price <= price:
        return None

    return round(
        ((original_price - price) / original_price) * 100
    )


def get_product_url(href):
    if not href:
        return None

    return urljoin(BASE_URL, href)


def get_image_url(image_url):
    if not image_url:
        return None

    image_url = image_url.strip()

    if not image_url or image_url.startswith("data:"):
        return None

    return urljoin(BASE_URL, image_url)


def get_page_url(collection_url, page_number):
    parsed = urlparse(collection_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page_number)]

    return urlunparse(
        parsed._replace(
            query=urlencode(query, doseq=True)
        )
    )


def save_json(products):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            products,
            file,
            indent=4,
            ensure_ascii=False,
        )

    print(f"\nJSON file saved to: {OUTPUT_FILE}")
    print(f"Total products saved: {len(products)}")


async def get_locator_text(locator):
    try:
        if await locator.count() > 0:
            return clean_text(
                await locator.first.inner_text()
            )
    except Exception:
        pass

    return None


async def get_locator_attribute(locator, attribute):
    try:
        if await locator.count() > 0:
            return await locator.first.get_attribute(attribute)
    except Exception:
        pass

    return None

async def scrape_product_details(browser, product_url, product_name):
    context = await browser.new_context()
    page = await context.new_page()

    description = None

    try:
        response = await page.goto(
            product_url,
            timeout=PAGE_TIMEOUT,
            wait_until="domcontentloaded",
        )

        if response and response.status >= 400:
            print(
                f"Warning: PDP returned HTTP {response.status}: "
                f"{product_url}"
            )

        try:
            await page.locator(
                "div.product-details__description"
            ).first.wait_for(
                state="attached",
                timeout=10000,
            )
        except Exception:
            pass

        # 1. Try the main product description first.
        description_locator = page.locator(
            "div.product-details__description"
        )

        if await description_locator.count() > 0:
            description = clean_text(
                await description_locator.first.inner_text()
            )

        # 2. If the main description is missing or empty,
        # extract the product features and perks.
        if not description:
            features_locator = page.locator(
                "div.features-perks__content"
            )

            if await features_locator.count() > 0:
                features = await features_locator.locator(
                    "#features li"
                ).all_inner_texts()

                perks = await features_locator.locator(
                    "#perks li"
                ).all_inner_texts()

                features = [
                    clean_text(item)
                    for item in features
                    if clean_text(item)
                ]

                perks = [
                    clean_text(item)
                    for item in perks
                    if clean_text(item)
                ]

                description_parts = []

                if features:
                    description_parts.append(
                        "Features: " + " ".join(features)
                    )

                if perks:
                    description_parts.append(
                        "Perks: " + " ".join(perks)
                    )

                if description_parts:
                    description = " ".join(
                        description_parts
                    )

        # 3. Final fallback if neither selector returns text.
        if not description:
            description = product_name
            print(
                f"Description not found. Using product name: "
                f"{product_name}"
            )
        else:
            print(
                f"Description extracted: {description[:200]}"
            )

    except Exception as error:
        print(
            f"Warning: PDP failed: {product_url} - {error}"
        )
        description = product_name

    finally:
        await page.close()
        await context.close()

    return description

async def scrape_autofurnish_products():
    products = []
    seen_names = set()
    seen_urls = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        page = await browser.new_page(
            viewport={
                "width": 1366,
                "height": 900,
            }
        )

        try:
            for category, collection_urls in COLLECTION_URLS.items():

                for collection_url in collection_urls:
                    accepted_count = 0
                    skipped_count = 0

                    print("\n" + "=" * 65)
                    print(
                        f"Starting AutoFurnish category: {category}"
                    )
                    print(f"Collection URL: {collection_url}")
                    print("=" * 65)

                    for page_number in range(1, MAX_PAGES + 1):
                        listing_url = get_page_url(
                            collection_url,
                            page_number,
                        )

                        print(
                            f"\nOpening listing page {page_number}: "
                            f"{listing_url}"
                        )

                        navigation_succeeded = False
                        response = None

                        for attempt in range(
                            1,
                            MAX_NAVIGATION_RETRIES + 1,
                        ):
                            try:
                                response = await page.goto(
                                    listing_url,
                                    timeout=PAGE_TIMEOUT,
                                    wait_until="domcontentloaded",
                                )

                                navigation_succeeded = True
                                break

                            except Exception as error:
                                print(
                                    f"Navigation attempt "
                                    f"{attempt}/{MAX_NAVIGATION_RETRIES} "
                                    f"failed: {error}"
                                )

                                if attempt < MAX_NAVIGATION_RETRIES:
                                    await page.wait_for_timeout(2000)

                        if not navigation_succeeded:
                            print(
                                f"SKIP: Could not open {listing_url}"
                            )
                            break

                        if response:
                            print(
                                f"HTTP status: {response.status}"
                            )

                        await page.wait_for_timeout(
                            LISTING_DELAY_SECONDS * 1000
                        )

                        try:
                            await page.locator(
                                "#product-grid li.grid__item"
                            ).first.wait_for(
                                state="attached",
                                timeout=PRODUCT_SELECTOR_TIMEOUT,
                            )
                        except Exception:
                            print(
                                "Product-card selector did not appear."
                            )

                        # Scroll to trigger lazy loading.
                        for _ in range(3):
                            await page.mouse.wheel(0, 2500)
                            await page.wait_for_timeout(700)

                        cards = page.locator(
                            "#product-grid li.grid__item"
                        )

                        total_cards = await cards.count()

                        print(
                            f"Page {page_number}: "
                            f"Found {total_cards} product cards"
                        )

                        if total_cards == 0:
                            print(
                                "No product cards found. "
                                "Stopping pagination."
                            )
                            break

                        new_products_on_page = 0

                        for index in range(total_cards):
                            card = cards.nth(index)

                            # PRODUCT NAME
                            name_locator = card.locator(
                                "h3.card__heading a"
                            )

                            name = await get_locator_text(
                                name_locator
                            )

                            if not name:
                                print(
                                    f"SKIP: Missing product name "
                                    f"at card {index + 1}"
                                )
                                skipped_count += 1
                                continue

                            # PRODUCT LINK
                            href = await get_locator_attribute(
                                name_locator,
                                "href",
                            )

                            product_link = get_product_url(href)

                            if not product_link:
                                print(
                                    f"SKIP: Invalid product link: {name}"
                                )
                                skipped_count += 1
                                continue

                            if (
                                name.lower() in seen_names
                                or product_link in seen_urls
                            ):
                                print(
                                    f"Duplicate product skipped: {name}"
                                )
                                continue

                            # CURRENT PRICE
                            price_locator = card.locator(
                                ".price__sale .price-item--sale"
                            )

                            price_text = await get_locator_text(
                                price_locator
                            )

                            # Fall back to the regular price.
                            if not price_text:
                                price_locator = card.locator(
                                    ".price__regular "
                                    ".price-item--regular"
                                )

                                price_text = await get_locator_text(
                                    price_locator
                                )

                            price = extract_price(price_text)

                            # ORIGINAL PRICE / MRP
                            original_price_locator = card.locator(
                                ".price__sale s.price-item--regular"
                            )

                            original_price_text = (
                                await get_locator_text(
                                    original_price_locator
                                )
                            )

                            original_price = extract_price(
                                original_price_text
                            )

                            # LISTING DISCOUNT
                            discount_locator = card.locator(
                                ".price__sale"
                            )

                            discount_text = await get_locator_text(
                                discount_locator
                            )

                            discount_scraped = extract_discount(
                                discount_text
                            )

                            # VALIDATE BEFORE OPENING PDP
                            if price is None or price <= 0:
                                print(
                                    f"SKIP before PDP: '{name}' "
                                    f"has no valid current price."
                                )
                                skipped_count += 1
                                continue

                            if original_price is None:
                                print(
                                    f"SKIP before PDP: '{name}' "
                                    f"has no valid original price."
                                )
                                skipped_count += 1
                                continue

                            calculated_discount = calculate_discount(
                                price,
                                original_price,
                            )

                            discount = (
                                calculated_discount
                                if calculated_discount is not None
                                else discount_scraped
                            )

                            if discount is None or discount <= 0:
                                print(
                                    f"SKIP before PDP: '{name}' "
                                    f"has no valid discount "
                                    f"(price={price}, "
                                    f"original_price={original_price}, "
                                    f"listing_discount="
                                    f"{discount_scraped})."
                                )
                                skipped_count += 1
                                continue

                            # PRODUCT IMAGE
                            image_locator = card.locator(
                                ".card__media img"
                            )

                            image_src = (
                                await get_locator_attribute(
                                    image_locator,
                                    "src",
                                )
                            )

                            if not image_src:
                                image_src = (
                                    await get_locator_attribute(
                                        image_locator,
                                        "data-src",
                                    )
                                )

                            if not image_src:
                                srcset = (
                                    await get_locator_attribute(
                                        image_locator,
                                        "srcset",
                                    )
                                )

                                if srcset:
                                    image_src = (
                                        srcset.split(",")[-1]
                                        .strip()
                                        .split(" ")[0]
                                    )

                            image_link = get_image_url(image_src)

                            # RATINGS
                            ratings = None

                            rating_locator = card.locator(
                                "[data-average-rating]"
                            )

                            rating_text = (
                                await get_locator_attribute(
                                    rating_locator,
                                    "data-average-rating",
                                )
                            )

                            try:
                                rating_value = float(rating_text)

                                if rating_value > 0:
                                    ratings = rating_value
                            except (TypeError, ValueError):
                                ratings = None

                            print(
                                f"\nACCEPT [{accepted_count + 1}]: "
                                f"{name}"
                            )
                            print(f"Price: {price}")
                            print(
                                f"Original price: {original_price}"
                            )
                            print(f"Discount: {discount}")
                            print("Opening product detail page...")

                            await page.wait_for_timeout(
                                DETAIL_DELAY_SECONDS * 1000
                            )

                            # UPDATED DESCRIPTION EXTRACTION
                            description = (
                                await scrape_product_details(
                                    browser,
                                    product_link,
                                    name,
                                )
                            )

                            product = {
                                "name": name,
                                "price": price,
                                "original_price": original_price,
                                "ratings": ratings,
                                "currency": "₹",
                                "discount": discount,
                                "description": description,
                                "image_link": image_link,
                                "product_link": product_link,
                                "organization_id": "Dealwallet",
                                "store_id": "AutoFurnish",
                                "categories_id": category,
                                "created_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }

                            products.append(product)

                            seen_names.add(name.lower())
                            seen_urls.add(product_link)

                            accepted_count += 1
                            new_products_on_page += 1

                        print(
                            f"\nCompleted listing page {page_number}"
                        )
                        print(
                            f"Accepted in this collection: "
                            f"{accepted_count}"
                        )
                        print(
                            f"Skipped in this collection: "
                            f"{skipped_count}"
                        )

                        if new_products_on_page == 0:
                            print(
                                "No new valid products on this page. "
                                "Stopping pagination."
                            )
                            break

                    print(f"\nCompleted category: {category}")
                    print(f"Accepted products: {accepted_count}")
                    print(f"Skipped products: {skipped_count}")

        finally:
            await browser.close()

    return products


def run_autofurnish_scraper():
    return asyncio.run(scrape_autofurnish_products())


class AutoFurnishSpider(scrapy.Spider):
    name = "autofurnish"
    allowed_domains = [
        "autofurnish.com",
        "www.autofurnish.com",
    ]

    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info(
            "Starting AutoFurnish Playwright scraper..."
        )
        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_autofurnish_scraper,
            )

        save_json(products)

        self.logger.info(
            "AutoFurnish scraper returned %s products.",
            len(products),
        )

        for product in products:
            if not product.get("product_link"):
                continue

            yield product

        self.logger.info("=" * 60)
        self.logger.info("AutoFurnish spider completed.")
        self.logger.info("=" * 60)