import asyncio
import json
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin
from datetime import datetime, timezone
import scrapy
from playwright.async_api import async_playwright





# --------------------------------------------------

# CONFIGURATION

# --------------------------------------------------



OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fabindia_products.json",
)

BASE_URLS = {
    "Fashion & Lifestyle": [
        "https://www.fabindia.com/clothing/women-kurtas?sortCode=discountPercent-desc",
        "https://www.fabindia.com/clothing/women-western-wear/?sortCode=discountPercent-desc",
        "https://www.fabindia.com/clothing/men-ethnic-wear?sortCode=discountPercent-desc",
        "https://www.fabindia.com/clothing/men-western-wear/?sortCode=discountPercent-desc",
    ],
  
    "Flowers & Gifts": [
        "https://www.fabindia.com/collection/gifting-ideas?query=:Recommended:allCategories:gifting-ideas:productgender:personal%20care:productgender:home%20%26%20living&promoname=cog-sec4-15oct24-3.jpg&promoID=NewGiftingPage_Component_Section4-sbc-3&sortCode=discountPercent-desc",
        "https://www.fabindia.com/home/photo-frames?promoname=Photo%20Frames&promoID=NewGiftingPage_Component_Section4-sbc-6&sortCode=discountPercent-desc",
    ],
    "Furnitures": [
        "https://www.fabindia.com/furniture/living?sortCode=discountPercent-desc",
        "https://www.fabindia.com/furniture/bedroom?sortCode=discountPercent-desc",
        "https://www.fabindia.com/furniture/dining?sortCode=discountPercent-desc",
        "https://www.fabindia.com/collection/furniture-outdoor?sortCode=discountPercent-desc",
    ],
    "Home & Kitchen": [
        "https://www.fabindia.com/home/dinner-serveware?sortCode=discountPercent-desc",
        "https://www.fabindia.com/home/drinkware?sortCode=discountPercent-desc",
    ],
    "Home Appliances": [],
    "Home Decor": [
        "https://www.fabindia.com/home/candles-candle-stands?sortCode=discountPercent-desc",
        "https://www.fabindia.com/home/home-decor?sortCode=discountPercent-desc",
    ],
    "Shirts": [
        "https://www.fabindia.com/clothing/men-shirts?sortCode=discountPercent-desc",
    ],
    "Toys Games": [
        "https://www.fabindia.com/home/games-toys?sortCode=discountPercent-desc",
    ],
}

# Flatten category URLs into (URL, category) pairs. Empty categories are skipped.
START_URLS = [
    (url, category)
    for category, urls in BASE_URLS.items()
    for url in urls
    if url
]

BASE_URL = "https://www.fabindia.com"
MAX_PRODUCTS = 50  # Maximum accepted products per category URL
PAGE_TIMEOUT = 60000
PRODUCT_SELECTOR_TIMEOUT = 20000
LISTING_DELAY_SECONDS = 2
DETAIL_DELAY_SECONDS = 1
MAX_NAVIGATION_RETRIES = 3





# --------------------------------------------------

# HELPER FUNCTIONS

# --------------------------------------------------



def clean_text(text):

    """Normalize whitespace in text."""

    return re.sub(r"\s+", " ", text).strip() if text else ""





def extract_price(text):

    """

    Convert displayed price like '₹1,299' to int/float.

    Returns None when missing or invalid.

    """

    if not text:

        return None



    cleaned = text.replace(",", "").strip()

    match = re.search(r"\d+(?:\.\d+)?", cleaned)



    if not match:

        return None



    value = float(match.group(0))

    return int(value) if value.is_integer() else value





def extract_discount(text):

    """

    Convert '6% off' to numeric 6.

    Returns None when no percentage is present.

    """

    if not text:

        return None



    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)

    if not match:

        return None



    value = float(match.group(1))

    return int(value) if value.is_integer() else value





def calculate_discount(price, original_price):

    """Calculate percentage discount, or return None if invalid."""

    if price is None or original_price is None:

        return None



    if original_price <= 0 or original_price <= price:

        return None



    return round(((original_price - price) / original_price) * 100)





def get_product_url(href):

    """Convert relative product link to absolute URL."""

    if not href:

        return None

    return urljoin(BASE_URL, href)





def get_image_url(image_url):

    """Return full image URL, preserving query parameters."""

    if not image_url:

        return None



    image_url = image_url.strip()

    if not image_url or image_url.startswith("data:"):

        return None



    return urljoin(BASE_URL, image_url)





def save_json(products):

    """Save JSON beside this Python script."""

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:

        json.dump(products, file, indent=4, ensure_ascii=False)



    print(f"\nJSON file saved to: {OUTPUT_FILE}")

    print(f"Total products saved: {len(products)}")





# --------------------------------------------------

# PRODUCT DETAIL PAGE

# This function is called only after listing validation.

# --------------------------------------------------



async def scrape_pdp(browser, product_url, product_name):

    """Extract product description from the product detail page."""

    context = await browser.new_context()

    page = await context.new_page()

    description = None



    try:

        await page.goto(

            product_url,

            timeout=45000,

            wait_until="domcontentloaded",

        )

        await page.wait_for_timeout(2000)



        # Try opening the Description section if present.

        try:

            description_button = page.locator(

                "button:has-text('Description')"

            )

            if await description_button.count() > 0:

                await description_button.first.click(timeout=3000)

        except Exception:

            pass



        description_locator = page.locator(

            "app-fab-product-description div.container"

        )



        if await description_locator.count() > 0:

            description = clean_text(

                await description_locator.first.inner_text()

            )



        if not description:

            description = product_name



    except Exception as error:

        print(f"Warning: Failed PDP: {product_url} - {error}")

        description = product_name



    finally:

        await page.close()

        await context.close()



    return description





# --------------------------------------------------

# FABINDIA LISTING SCRAPER

# --------------------------------------------------



async def scrape_fabindia_products():

    products = []

    seen_names = set()

    seen_urls = set()



    async with async_playwright() as playwright:

        browser = await playwright.chromium.launch(

            headless=True,

            args=["--no-sandbox", "--disable-setuid-sandbox"],

        )



        page = await browser.new_page()



        try:

            for category_url, category in START_URLS:

                print(f"\nStarting Fabindia category: {category}")

                print(f"URL: {category_url}")



                response = None
                navigation_succeeded = False

                for attempt in range(1, MAX_NAVIGATION_RETRIES + 1):
                    try:
                        response = await page.goto(
                            category_url,
                            timeout=PAGE_TIMEOUT,
                            wait_until="domcontentloaded",
                        )
                        navigation_succeeded = True
                        break
                    except Exception as error:
                        print(
                            f"Navigation attempt {attempt}/{MAX_NAVIGATION_RETRIES} "
                            f"failed for {category_url}: {error}"
                        )
                        if attempt < MAX_NAVIGATION_RETRIES:
                            await page.wait_for_timeout(2000)

                if not navigation_succeeded:
                    print(f"SKIP: Could not open listing URL: {category_url}")
                    continue

                if response is not None:
                    print(f"HTTP status: {response.status}")

                await page.wait_for_timeout(2500)

                print(f"Final page URL: {page.url}")
                print(f"Page title: {await page.title()}")

                # Wait for product cards; filtered/sale listings can render slowly.
                try:
                    await page.locator("app-fab-product-grid-item").first.wait_for(
                        state="attached",
                        timeout=PRODUCT_SELECTOR_TIMEOUT,
                    )
                except Exception:
                    print("Product-card selector did not appear before scrolling.")

                # Scroll to load more listing cards.
                for _ in range(3):
                    await page.mouse.wheel(0, 3000)
                    await page.wait_for_timeout(1000)

                cards = page.locator("app-fab-product-grid-item")
                total_cards = await cards.count()
                print(f"Found {total_cards} potential product cards")

                if total_cards == 0:
                    try:
                        page_text = clean_text(await page.locator("body").inner_text())
                        print(f"Page text preview: {page_text[:350]}")
                    except Exception:
                        pass



                accepted_count = 0

                skipped_count = 0



                for index in range(total_cards):

                    # Count only accepted products toward MAX_PRODUCTS.

                    if accepted_count >= MAX_PRODUCTS:

                        break



                    card = cards.nth(index)



                    # ----------------------------------

                    # PRODUCT NAME

                    # ----------------------------------

                    name_locator = card.locator(

                        "p.product-description.my-0"

                    )

                    if await name_locator.count() == 0:

                        continue



                    name = clean_text(

                        await name_locator.first.inner_text()

                    )

                    if not name or name.lower() in seen_names:

                        continue



                    # ----------------------------------

                    # PRODUCT LINK

                    # ----------------------------------

                    link_locator = card.locator("a.text-decoration-none")

                    if await link_locator.count() == 0:

                        print(f"SKIP: No product link for '{name}'")

                        skipped_count += 1

                        continue



                    href = await link_locator.first.get_attribute("href")

                    product_link = get_product_url(href)



                    if not product_link:

                        print(f"SKIP: Invalid product link for '{name}'")

                        skipped_count += 1

                        continue



                    if product_link in seen_urls:

                        continue



                    # ----------------------------------

                    # CURRENT PRICE

                    # Selector: span.offer-amount

                    # ----------------------------------

                    price = None

                    price_locator = card.locator("span.offer-amount")



                    if await price_locator.count() > 0:

                        price = extract_price(

                            await price_locator.first.inner_text()

                        )



                    # ----------------------------------

                    # ORIGINAL PRICE / MRP

                    # Selector: del span.amount

                    # ----------------------------------

                    original_price = None

                    original_price_locator = card.locator(

                        "del span.amount"

                    )



                    if await original_price_locator.count() > 0:

                        original_price = extract_price(

                            await original_price_locator.first.inner_text()

                        )



                    # ----------------------------------

                    # LISTING DISCOUNT

                    # Selector: span.discount-amount span.amount

                    # ----------------------------------

                    discount_scraped = None

                    discount_locator = card.locator(

                        "span.discount-amount span.amount"

                    )



                    if await discount_locator.count() > 0:

                        discount_text = clean_text(

                            await discount_locator.first.inner_text()

                        )

                        discount_scraped = extract_discount(discount_text)



                    # ----------------------------------

                    # VALIDATE BEFORE OPENING PDP

                    # ----------------------------------

                    if price is None:

                        print(

                            f"SKIP before PDP: '{name}' "

                            "has no valid current price."

                        )

                        skipped_count += 1

                        continue



                    if original_price is None:

                        print(

                            f"SKIP before PDP: '{name}' "

                            "has no valid original price."

                        )

                        skipped_count += 1

                        continue



                    # The original price must be higher than current price.

                    calculated_discount = calculate_discount(

                        price,

                        original_price,

                    )



                    # Prefer calculated discount; use listing percentage

                    # only if calculation is unavailable.

                    discount = (

                        calculated_discount

                        if calculated_discount is not None

                        else discount_scraped

                    )



                    if discount is None or discount <= 0:

                        print(

                            f"SKIP before PDP: '{name}' "

                            f"has no valid discount "

                            f"(price={price}, original_price={original_price}, "

                            f"listing_discount={discount_scraped})."

                        )

                        skipped_count += 1

                        continue



                    # ----------------------------------

                    # IMAGE URL

                    # ----------------------------------

                    image_link = None

                    image_locator = card.locator("img")



                    if await image_locator.count() > 0:

                        image_src = await image_locator.first.get_attribute(

                            "src"

                        )



                        if not image_src:

                            image_src = await image_locator.first.get_attribute(

                                "data-src"

                            )



                        image_link = get_image_url(image_src)



                    print(

                        f"\nACCEPT [{accepted_count + 1}/{MAX_PRODUCTS}]: "

                        f"{name}"

                    )

                    print(

                        f"Price: {price!r} "

                        f"({type(price).__name__})"

                    )

                    print(

                        f"Original price: {original_price!r} "

                        f"({type(original_price).__name__})"

                    )

                    print(

                        f"Discount: {discount!r} "

                        f"({type(discount).__name__})"

                    )

                    print("Opening product detail page now...")



                    # PDP is opened only after all price/discount checks pass.

                    await page.wait_for_timeout(DETAIL_DELAY_SECONDS * 1000)
                    description = await scrape_pdp(
                        browser,
                        product_link,
                        name,
                    )



                    product = {

                        "name": name,

                        "price": price,

                        "original_price": original_price,

                        "ratings": None,

                        "currency": "₹",

                        "discount": discount,

                        "description": description,

                        "image_link": image_link,

                        "product_link": product_link,

                        "organization_id": "Dealwallet",

                        "store_id": "Fabindia Home",

                        "categories_id": category,

                        "created_at": datetime.now(timezone.utc).isoformat(),

                    }



                    # Confirm numeric/null values before JSON serialization.

                    print("DEBUG FIELD TYPES:")

                    for field in (

                        "price",

                        "original_price",

                        "discount",

                        "ratings",

                    ):

                        value = product[field]

                        print(

                            f"  {field}: {value!r} "

                            f"({type(value).__name__})"

                        )



                    products.append(product)

                    seen_names.add(name.lower())

                    seen_urls.add(product_link)

                    accepted_count += 1



                print(f"\nCompleted category: {category}")

                print(f"Accepted products: {accepted_count}")

                print(f"Skipped products: {skipped_count}")



        finally:

            await browser.close()



    return products





# --------------------------------------------------
# RUN PLAYWRIGHT SCRAPER IN A SEPARATE PROCESS
# --------------------------------------------------


def run_fabindia_scraper():
    """Run the async Playwright scraper in a child process."""
    return asyncio.run(scrape_fabindia_products())


# --------------------------------------------------
# SCRAPY SPIDER
# --------------------------------------------------


class FabindiaSpider(scrapy.Spider):
    name = "fabindia"
    allowed_domains = ["fabindia.com", "www.fabindia.com"]

    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting Fabindia Playwright scraper...")
        self.logger.info("=" * 60)

        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_fabindia_scraper,
            )

        # Save a raw copy, similar to the Soulflower spider.
        save_json(products)

        self.logger.info("Fabindia scraper returned %s products.", len(products))

        for product in products:
            # Keep valid product links. Image/description may be absent on some
            # listings; description is allowed to fall back to the product name.
            if not product.get("product_link"):
                continue
            yield product

        self.logger.info("=" * 60)
        self.logger.info("Fabindia spider completed.")
        self.logger.info("=" * 60)
