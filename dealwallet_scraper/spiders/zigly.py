import asyncio
import json
import logging
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URLS = {
    "Pet Essentials": [
        "https://zigly.com/collections/dog-food",    
    ],
}


MAX_PAGES = 3
MAX_PRODUCTS_PER_CATEGORY = 1000

JSON_FILE = "scrape_zigly.json"

CRAWLER_TIMEOUT = 60000
CRAWLER_DELAY = 2000
WAIT_TIMEOUT = 30000


# ============================================================
# PRICE HELPERS
# ============================================================

def clean_to_int(text):
    if not text:
        return 0

    txt = str(text).replace("₹", "").replace(",", "").strip()

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        txt,
    )

    return int(float(match.group(1))) if match else 0


def discount_to_int(value):
    if not value:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        str(value),
    )

    return int(float(match.group(1))) if match else None


def parse_rating_to_float(rating_str):
    if not rating_str:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(rating_str),
    )

    return float(match.group()) if match else None


# ============================================================
# VARIANT PRICE EXTRACTION
# ============================================================

def get_discounted_variant(card):
    """
    Get price, original price and discount from the SAME
    Zigly variant.

    Zigly has multiple variants inside one product card.

    Never mix:
        price from one variant
        with discount from another variant.
    """

    variants = card.select(
        "div.card-variant-wrapper[data-price][data-comprice]"
    )

    if not variants:
        return None

    # --------------------------------------------------------
    # First priority:
    # Explicit discounted variant
    # --------------------------------------------------------

    for variant in variants:

        discount = variant.get("data-discount")

        if discount and discount.strip():
            return variant

    # --------------------------------------------------------
    # Fallback:
    # Calculate discount from price < compare price
    # --------------------------------------------------------

    for variant in variants:

        price = clean_to_int(
            variant.get("data-price")
        )

        compare_price = clean_to_int(
            variant.get("data-comprice")
        )

        if price > 0 and compare_price > price:
            return variant

    return None


# ============================================================
# DESCRIPTION CLEANING
# ============================================================

def clean_description(text, name=""):
    """
    Clean Zigly product description.

    Removes:
        - | separators
        - Net Quantity
        - Address / Delivery Address / Shipping Address
        - Vet Pro Tip section
        - Zigly Tip section
        - Sub-Category Description section
        - Subcategory Description section
    """

    if not text:
        return ""

    text = str(text)

    # --------------------------------------------------------
    # Normalize HTML whitespace/entities
    # --------------------------------------------------------

    text = text.replace("\xa0", " ")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")

    # --------------------------------------------------------
    # Remove unwanted sections
    # --------------------------------------------------------

    text = re.split(
        r"\bVet\s*Pro\s*Tip\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    text = re.split(
        r"\bZigly\s*Tip\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    text = re.split(
        r"\bSub[\s-]*Category\s*Description\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    text = re.split(
        r"\bSubcategory\s*Description\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    # --------------------------------------------------------
    # Remove Net Quantity / Net Qty
    # --------------------------------------------------------

    text = re.sub(
        r"\bNet\s*(?:Quantity|Qty)\s*[:\-]?\s*[^,|;\n]+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # Remove Address values
    # --------------------------------------------------------

    text = re.sub(
        r"\b(?:Delivery\s+|Shipping\s+)?Address\s*[:\-]?\s*[^,|;\n]+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # --------------------------------------------------------
    # Convert pipe separators to commas
    # --------------------------------------------------------

    text = re.sub(
        r"\s*\|\s*",
        ", ",
        text,
    )

    # --------------------------------------------------------
    # Remove repeated separators
    # --------------------------------------------------------

    text = re.sub(
        r",\s*,+",
        ", ",
        text,
    )

    # --------------------------------------------------------
    # Normalize whitespace
    # --------------------------------------------------------

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    # --------------------------------------------------------
    # Avoid "Brand Brand ..." at beginning
    # --------------------------------------------------------

    if name:

        name_clean = re.sub(
            r"\s+",
            " ",
            name,
        ).strip()

        text = re.sub(
            rf"^\s*({re.escape(name_clean)})\s+\1\b",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )

    # --------------------------------------------------------
    # Remove spaces before punctuation
    # --------------------------------------------------------

    text = re.sub(
        r"\s+([,.!?;:])",
        r"\1",
        text,
    )

    return text.strip(" ,|")


# ============================================================
# DESCRIPTION EXTRACTION
# ============================================================

def extract_description(product_soup, name):
    """
    Extract useful Zigly product description.

    Only extracts content from accordion-content sections.

    Unwanted sections are filtered before their text is added.
    """

    accordion_contents = product_soup.select(
        "div.accordion-content"
    )

    if not accordion_contents:
        return ""

    parts = []

    for accordion in accordion_contents:

        # ----------------------------------------------------
        # Find nearest preceding heading
        # ----------------------------------------------------

        heading = accordion.find_previous(
            [
                "h2",
                "h3",
                "h4",
                "h5",
                "summary",
                "button",
            ]
        )

        heading_text = (
            heading.get_text(
                " ",
                strip=True,
            )
            if heading
            else ""
        )

        # ----------------------------------------------------
        # Skip unwanted sections
        # ----------------------------------------------------

        if re.search(
            r"Vet\s*Pro\s*Tip|"
            r"Zigly\s*Tip|"
            r"Sub[\s-]*Category\s*Description|"
            r"Subcategory\s*Description",
            heading_text,
            flags=re.IGNORECASE,
        ):
            continue

        # ----------------------------------------------------
        # Extract paragraphs
        # ----------------------------------------------------

        paragraphs = accordion.select("p")

        if paragraphs:

            for paragraph in paragraphs:

                value = paragraph.get_text(
                    " ",
                    strip=True,
                )

                if value:
                    parts.append(value)

        else:

            value = accordion.get_text(
                " ",
                strip=True,
            )

            if value:
                parts.append(value)

    description = " ".join(parts)

    return clean_description(
        description,
        name,
    )


# ============================================================
# MAIN ZIGLY SCRAPER
# ============================================================

async def zigly_store():
    """
    Main asynchronous Zigly scraper.

    This function is executed inside a separate process
    from the Scrapy spider.
    """

    results = []

    seen_links = set()
    seen_product_ids = set()

    category_counts = {}

    print("\n")
    print("=" * 70)
    print("ZIGLY SCRAPING STARTED")
    print("=" * 70)

    try:

        async with AsyncWebCrawler(
            verbose=True
        ) as crawler:

            # ------------------------------------------------
            # Browser configuration
            # ------------------------------------------------

            crawler.browser_config = {
                "headless": True,
                "javascript": True,
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }

            # ------------------------------------------------
            # Crawl configuration
            # ------------------------------------------------

            crawler.crawler_run_config = {
                "wait_until": "networkidle",
                "timeout": CRAWLER_TIMEOUT,
                "delay": CRAWLER_DELAY,
                "wait_for": {
                    "selector": (
                        "ul#product-grid li.grid__item"
                    ),
                    "timeout": WAIT_TIMEOUT,
                },
            }

            # =================================================
            # CATEGORY LOOP
            # =================================================

            for category, urls in BASE_URLS.items():

                print("\n")
                print("=" * 70)
                print(f" CATEGORY: {category}")
                print("=" * 70)

                category_results = []

                # ------------------------------------------------
                # URL LOOP
                # ------------------------------------------------

                for base_url in urls:

                    print("\n")
                    print("-" * 70)
                    print(f"BASE URL: {base_url}")
                    print("-" * 70)

                    page_num = 1

                    # =================================================
                    # PAGINATION
                    # =================================================

                    while page_num <= MAX_PAGES:

                        if (
                            len(category_results)
                            >= MAX_PRODUCTS_PER_CATEGORY
                        ):
                            print(
                                "Maximum category product limit reached."
                            )
                            break

                        # ------------------------------------------------
                        # Build pagination URL
                        # ------------------------------------------------

                        separator = (
                            "&"
                            if "?" in base_url
                            else "?"
                        )

                        url = (
                            f"{base_url}"
                            f"{separator}"
                            f"page={page_num}"
                        )

                        print("\n")
                        print(
                            "-" * 70
                        )
                        print(
                            f"PAGE {page_num}"
                        )
                        print(
                            f"URL: {url}"
                        )
                        print(
                            "-" * 70
                        )

                        # ------------------------------------------------
                        # Crawl listing page
                        # ------------------------------------------------

                        try:

                            page = await crawler.arun(
                                url=url
                            )

                        except Exception as e:

                            print(
                                f"[ERROR] Failed to crawl page "
                                f"{page_num}: {e}"
                            )

                            break

                        if not page.success:

                            print(
                                f"Failed to fetch page "
                                f"{page_num}"
                            )

                            break

                        # ------------------------------------------------
                        # Parse listing HTML
                        # ------------------------------------------------

                        soup = BeautifulSoup(
                            page.html,
                            "html.parser",
                        )

                        product_cards = soup.select(
                            "ul#product-grid > li.grid__item"
                        )

                        print(
                            f"Found {len(product_cards)} "
                            f"products on page {page_num}."
                        )

                        if not product_cards:

                            print(
                                "No more products found, "
                                "stopping pagination."
                            )

                            break

                        # =================================================
                        # PRODUCT LOOP
                        # =================================================

                        for card_index, card in enumerate(
                            product_cards,
                            start=1,
                        ):

                            if (
                                len(category_results)
                                >= MAX_PRODUCTS_PER_CATEGORY
                            ):
                                break

                            print("\n")
                            print(
                                f"PRODUCT {card_index}/"
                                f"{len(product_cards)}"
                            )

                            try:

                                # =================================================
                                # PRODUCT NAME
                                # =================================================

                                name_tag = card.select_one(
                                    "a.full-unstyled-link.line-clamp.fw-700"
                                )

                                name = (
                                    name_tag.get_text(
                                        strip=True
                                    )
                                    if name_tag
                                    else "N/A"
                                )

                                # =================================================
                                # PRODUCT LINK
                                # =================================================

                                link_tag = card.select_one(
                                    "h3.card__heading "
                                    "a[href*='/products/']"
                                )

                                if link_tag:

                                    product_link = urljoin(
                                        base_url,
                                        link_tag.get("href"),
                                    )

                                else:

                                    product_link = "N/A"

                                # =================================================
                                # BASIC VALIDATION
                                # =================================================

                                if (
                                    not name
                                    or name == "N/A"
                                    or not product_link
                                    or product_link == "N/A"
                                ):
                                    print(
                                        "Skipping product with "
                                        "missing name/link."
                                    )
                                    continue

                                # =================================================
                                # DUPLICATE CHECK
                                # =================================================

                                normalized_link = (
                                    product_link.split("?")[0]
                                    .strip()
                                    .lower()
                                )

                                if (
                                    normalized_link
                                    in seen_links
                                ):
                                    print(
                                        f"Duplicate skipped: {name}"
                                    )
                                    continue

                                # =================================================
                                # SELECT DISCOUNTED VARIANT
                                # =================================================

                                selected_variant = (
                                    get_discounted_variant(
                                        card
                                    )
                                )

                                if not selected_variant:

                                    print(
                                        f"Skipping without "
                                        f"discounted variant: {name}"
                                    )

                                    continue

                                # =================================================
                                # VARIANT DATA
                                # =================================================

                                price = (
                                    selected_variant.get(
                                        "data-price",
                                        "N/A",
                                    )
                                )

                                original_price = (
                                    selected_variant.get(
                                        "data-comprice",
                                        "N/A",
                                    )
                                )

                                discount = (
                                    selected_variant.get(
                                        "data-discount",
                                        "N/A",
                                    )
                                )

                                variant_title = (
                                    selected_variant.get(
                                        "data-variant-title",
                                        "N/A",
                                    )
                                )

                                # =================================================
                                # IMAGE
                                # =================================================

                                img_tag = card.select_one(
                                    "div.card__media "
                                    "img[src], "
                                    "div.card__media "
                                    "img[srcset]"
                                )

                                if img_tag:

                                    image_src = (
                                        img_tag.get("src")
                                        or img_tag.get(
                                            "srcset",
                                            "",
                                        )
                                        .split(",")[0]
                                        .strip()
                                        .split(" ")[0]
                                    )

                                    image_link = urljoin(
                                        base_url,
                                        image_src,
                                    )

                                else:

                                    image_link = "N/A"

                                # =================================================
                                # RATING
                                # =================================================

                                rating = (
                                    card.select_one(
                                        "div.custom-reviews-main "
                                        "span.custom-average-number"
                                    )
                                    or card.select_one(
                                        "span.custom-average-number.fw-700"
                                    )
                                )

                                if rating:

                                    rating_value = (
                                        parse_rating_to_float(
                                            rating.get_text(
                                                strip=True
                                            )
                                        )
                                    )

                                    ratings = (
                                        round(
                                            rating_value,
                                            1,
                                        )
                                        if rating_value
                                        is not None
                                        else None
                                    )

                                else:

                                    ratings = None

                                # =================================================
                                # DESCRIPTION
                                # =================================================

                                description = ""

                                print(
                                    f"Getting description: "
                                    f"{product_link}"
                                )

                                try:

                                    product_page = (
                                        await crawler.arun(
                                            url=product_link
                                        )
                                    )

                                    if product_page.success:

                                        product_soup = (
                                            BeautifulSoup(
                                                product_page.html,
                                                "html.parser",
                                            )
                                        )

                                        description = (
                                            extract_description(
                                                product_soup,
                                                name,
                                            )
                                        )

                                    else:

                                        print(
                                            "Detail page failed."
                                        )

                                except Exception as e:

                                    print(
                                        f"Description error: {e}"
                                    )

                                # =================================================
                                # REQUIRED FIELD VALIDATION
                                # =================================================

                                if (
                                    not name
                                    or name == "N/A"
                                    or not product_link
                                    or product_link == "N/A"
                                    or not image_link
                                    or image_link == "N/A"
                                    or not description
                                    or description == "N/A"
                                ):

                                    print(
                                        f"Skipping incomplete "
                                        f"product: {name}"
                                    )

                                    continue

                                # =================================================
                                # DISCOUNT VALIDATION
                                # =================================================

                                if (
                                    discount is None
                                    or str(discount)
                                    .strip() == ""
                                    or str(discount)
                                    .strip()
                                    .lower()
                                    == "n/a"
                                ):

                                    print(
                                        f"Skipping without "
                                        f"discount: {name}"
                                    )

                                    continue

                                # =================================================
                                # CONVERT NUMERIC VALUES
                                # =================================================

                                price_int = clean_to_int(
                                    price
                                )

                                original_price_int = (
                                    clean_to_int(
                                        original_price
                                    )
                                )

                                discount_int = (
                                    discount_to_int(
                                        discount
                                    )
                                )

                                # =================================================
                                # PRICE VALIDATION
                                # =================================================

                                if (
                                    price_int <= 0
                                    or original_price_int <= 0
                                ):

                                    print(
                                        f"Skipping invalid "
                                        f"price: {name}"
                                    )

                                    continue

                                # =================================================
                                # DISCOUNT VALIDATION
                                # =================================================

                                if (
                                    discount_int is None
                                    or discount_int <= 0
                                ):

                                    print(
                                        f"Skipping invalid "
                                        f"discount: {name}"
                                    )

                                    continue

                                # =================================================
                                # PRODUCT DATA
                                # =================================================

                                product_data = {
                                    "name": name,
                                    "price": price_int,
                                    "currency": "₹",
                                    "original_price": (
                                        original_price_int
                                    ),
                                    "discount": discount_int,
                                    "ratings": ratings,
                                    "description": description,
                                    "image_link": image_link,
                                    "product_link": (
                                        product_link
                                    ),
                                    "organization_id": (
                                        "Dealwallet"
                                    ),
                                    "store_id": "Zigly",
                                    "categories_id": category,
                                    "created_at": (
                                        datetime.now()
                                        .isoformat()
                                    ),
                                }

                                # =================================================
                                # SAVE RESULT
                                # =================================================

                                results.append(
                                    product_data
                                )

                                category_results.append(
                                    product_data
                                )

                                seen_links.add(
                                    normalized_link
                                )

                                # =================================================
                                # LOG
                                # =================================================

                                print(
                                    f"Scraped: {name}"
                                )

                                print(
                                    f"   Variant       : "
                                    f"{variant_title}"
                                )

                                print(
                                    f"   Price         : "
                                    f"₹{price_int}"
                                )

                                print(
                                    f"   MRP           : "
                                    f"₹{original_price_int}"
                                )

                                print(
                                    f"   Discount      : "
                                    f"{discount_int}%"
                                )

                                print(
                                    f"   Rating        : "
                                    f"{ratings}"
                                )

                                print(
                                    f"   Description   : "
                                    f"{description[:150]}..."
                                    if len(description) > 150
                                    else
                                    f"   Description   : "
                                    f"{description}"
                                )

                                print(
                                    f"   URL           : "
                                    f"{product_link}"
                                )

                            except Exception as e:

                                print(
                                    f"[ERROR] Product "
                                    f"processing error: {e}"
                                )

                        # =================================================
                        # PAGE COMPLETE
                        # =================================================

                        print("\n")
                        print(
                            f"Page {page_num} completed."
                        )

                        print(
                            f"Products on page: "
                            f"{len(product_cards)}"
                        )

                        print(
                            f"New products so far: "
                            f"{len(category_results)}"
                        )

                        page_num += 1

                # =================================================
                # CATEGORY COMPLETE
                # =================================================

                category_counts[category] = (
                    len(category_results)
                )

                print("\n")
                print("=" * 70)
                print(
                    f"Total products scraped for "
                    f"'{category}': "
                    f"{len(category_results)}"
                )
                print("=" * 70)

        # =========================================================
        # JSON SAVE
        # =========================================================

        with open(
            JSON_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                results,
                file,
                indent=4,
                ensure_ascii=False,
            )

        # =========================================================
        # FINAL SUMMARY
        # =========================================================

        print("\n")
        print("=" * 70)
        print("ZIGLY SCRAPING COMPLETED")
        print("=" * 70)

        print(
            f"Total products: {len(results)}"
        )

        print(
            f"Category counts: {category_counts}"
        )

        print(
            f"JSON saved successfully: "
            f"{JSON_FILE}"
        )

        print("=" * 70)

        return results

    except Exception as e:

        print("\n")
        print("=" * 70)
        print("ZIGLY SCRAPER ERROR")
        print("=" * 70)
        print(e)
        print("=" * 70)

        return results


# ============================================================
# PROCESS RUNNER
# ============================================================

def run_zigly_scraper():
    """
    Synchronous wrapper used by ProcessPoolExecutor.
    """

    try:

        return asyncio.run(
            zigly_store()
        )

    except Exception as e:

        print(
            f"Zigly scraper process error: {e}"
        )

        return []


# ============================================================
# SCRAPY SPIDER
# ============================================================

class ZiglySpider(scrapy.Spider):

    name = "zigly"

    allowed_domains = [
        "zigly.com",
    ]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 1,
    }

    async def start(self):

        self.logger.info(
            "Starting Zigly scraper..."
        )

        self.logger.info(
            "Running Crawl4AI in separate process..."
        )

        loop = asyncio.get_running_loop()

        # --------------------------------------------------------
        # Separate process
        # --------------------------------------------------------

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            try:

                products = await loop.run_in_executor(
                    executor,
                    run_zigly_scraper,
                )

            except Exception as e:

                self.logger.error(
                    f"Zigly scraper failed: {e}"
                )

                return

        # --------------------------------------------------------
        # Result validation
        # --------------------------------------------------------

        if not products:

            self.logger.warning(
                "No products returned from Zigly scraper."
            )

            return

        self.logger.info(
            f"Zigly scraper completed. "
            f"Products returned: {len(products)}"
        )

        # --------------------------------------------------------
        # Yield products to Scrapy pipeline
        # --------------------------------------------------------

        for product in products:

            try:

                if not product.get("name"):
                    continue

                if not product.get("product_link"):
                    continue

                if not product.get("image_link"):
                    continue

                if not product.get("description"):
                    continue

                yield product

            except Exception as e:

                self.logger.error(
                    f"Error yielding Zigly product: {e}"
                )

        self.logger.info(
            "Zigly spider completed."
        )