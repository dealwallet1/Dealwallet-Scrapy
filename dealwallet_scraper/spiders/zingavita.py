import asyncio
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
import scrapy


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URLS = {
    "Ayurveda": [
        "https://www.zingavita.com/collections/all"
    ],
}

MAX_PAGES = 2

CRAWLER_TIMEOUT = 30000
CRAWLER_DELAY = 500
WAIT_TIMEOUT = 10000

BASE_DOMAIN = "https://www.zingavita.com"


# ============================================================
# PRICE PARSER
# ============================================================

def parse_price_to_int(price_str):
    """
    Convert price text such as:

        ₹1,850
        1,850
        1850.00

    into integer 1850.

    Returns None when conversion is not possible.
    """

    if price_str is None:
        return None

    price_str = str(price_str).strip()

    if not price_str or price_str == "N/A":
        return None

    clean = re.sub(r"[^\d.]", "", price_str)

    if not clean:
        return None

    try:
        return int(float(clean))
    except (TypeError, ValueError):
        return None


# ============================================================
# RATING PARSER
# ============================================================

def parse_rating_to_float(rating_str):
    """
    Convert rating text such as:

        4.67 / 5.0
        4.5
        4

    into float.

    Returns None when conversion is not possible.
    """

    if rating_str is None:
        return None

    rating_str = str(rating_str).strip()

    if not rating_str:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        rating_str
    )

    if not match:
        return None

    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


# ============================================================
# DISCOUNT PARSER
# ============================================================

def parse_discount_to_int(discount_str):
    """
    Convert discount text such as:

        20% OFF
        30%
        15

    into integer.

    Returns None when conversion is not possible.
    """

    if discount_str is None:
        return None

    discount_str = str(discount_str).strip()

    if not discount_str or discount_str == "N/A":
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        discount_str
    )

    if not match:
        return None

    try:
        return int(float(match.group(1)))
    except (TypeError, ValueError):
        return None


# ============================================================
# DESCRIPTION CLEANER
# ============================================================

def clean_description(description_container):
    """
    Extract description from:

        <p>
        <ul>
        <li>
        headings, etc.

    Returns a clean comma-separated description.
    """

    if not description_container:
        return ""

    parts = []

    # --------------------------------------------------------
    # Get paragraphs and headings
    # --------------------------------------------------------

    for tag in description_container.find_all(
        [
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6"
        ]
    ):

        text = tag.get_text(
            " ",
            strip=True
        )

        if text:
            parts.append(text)

    # --------------------------------------------------------
    # Get list items
    # --------------------------------------------------------

    for li in description_container.select("li"):

        text = li.get_text(
            " ",
            strip=True
        )

        if text:
            parts.append(text)

    # --------------------------------------------------------
    # Clean individual parts
    # --------------------------------------------------------

    cleaned_parts = []

    for part in parts:

        part = re.sub(
            r"\s+",
            " ",
            part
        ).strip()

        if not part:
            continue

        cleaned_parts.append(part)

    # --------------------------------------------------------
    # Join with comma
    # --------------------------------------------------------

    description = ", ".join(
        cleaned_parts
    )

    # --------------------------------------------------------
    # Fix cases like:
    #
    # "struggling with:, Fatigue"
    #
    # into:
    #
    # "struggling with: Fatigue"
    # --------------------------------------------------------

    description = re.sub(
        r":,\s*",
        ": ",
        description
    )

    # --------------------------------------------------------
    # Remove duplicate commas
    # --------------------------------------------------------

    description = re.sub(
        r",\s*,+",
        ", ",
        description
    )

    # --------------------------------------------------------
    # Remove spaces before commas
    # --------------------------------------------------------

    description = re.sub(
        r"\s+,",
        ",",
        description
    )

    # --------------------------------------------------------
    # Remove excessive spaces
    # --------------------------------------------------------

    description = re.sub(
        r"\s+",
        " ",
        description
    ).strip()

    return description


# ============================================================
# CRAWL4AI SCRAPER
# ============================================================

async def scrape_zingavita_async():

    products = []

    seen_links = set()

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

        # ====================================================
        # CATEGORY LOOP
        # ====================================================

        for category, urls in BASE_URLS.items():

            category_results = []

            for base_url in urls:

                # =================================================
                # PAGE LOOP
                # =================================================

                for page_num in range(
                    1,
                    MAX_PAGES + 1
                ):

                    url = (
                        f"{base_url}?page={page_num}"
                    )

                    print("\n" + "=" * 80)
                    print(
                        f"Category : {category}"
                    )
                    print(
                        f"Page     : {page_num}"
                    )
                    print(
                        f"URL      : {url}"
                    )
                    print("=" * 80)

                    # =================================================
                    # SCRAPE CATEGORY PAGE
                    # =================================================

                    try:

                        page = await crawler.arun(
                            url=url
                        )

                    except Exception as e:

                        print(
                            f"[ERROR] Failed to crawl "
                            f"page {page_num}: {e}"
                        )

                        continue

                    if not page.success:

                        print(
                            f"[ERROR] Crawl4AI failed "
                            f"on page {page_num}"
                        )

                        continue

                    # =================================================
                    # PARSE HTML
                    # =================================================

                    soup = BeautifulSoup(
                        page.html,
                        "html.parser"
                    )

                    # =================================================
                    # PRODUCT CARDS
                    # =================================================

                    product_cards = soup.select(
                        "li.grid__item.scroll-trigger.animate--slide-in"
                    )

                    if not product_cards:

                        print(
                            f"[INFO] No products found "
                            f"on page {page_num}"
                        )

                        break

                    print(
                        f"[INFO] Products found: "
                        f"{len(product_cards)}"
                    )

                    page_count = 0

                    # =================================================
                    # PRODUCT LOOP
                    # =================================================

                    for card in product_cards:

                        try:

                            # =========================================
                            # PRODUCT NAME
                            # =========================================

                            name_tag = card.select_one(
                                "a.full-unstyled-link"
                            )

                            name = (
                                name_tag.get_text(
                                    " ",
                                    strip=True
                                )
                                if name_tag
                                else "N/A"
                            )

                            # =========================================
                            # PRODUCT LINK
                            # =========================================

                            link_tag = card.select_one(
                                "a[href*='/products/']"
                            )

                            if (
                                link_tag
                                and link_tag.get("href")
                            ):

                                product_link = urljoin(
                                    BASE_DOMAIN,
                                    link_tag["href"]
                                )

                            else:

                                product_link = "N/A"

                            # =========================================
                            # DUPLICATE CHECK
                            # =========================================

                            if (
                                product_link != "N/A"
                                and product_link in seen_links
                            ):

                                print(
                                    f"[SKIP] Duplicate: "
                                    f"{name}"
                                )

                                continue

                            if product_link != "N/A":

                                seen_links.add(
                                    product_link
                                )

                            # =========================================
                            # ORIGINAL PRICE
                            # =========================================

                            original_price_tag = (
                                card.select_one(
                                    "s.price-item.price-item--regular"
                                )
                            )

                            original_price_text = (
                                original_price_tag.get_text(
                                    " ",
                                    strip=True
                                )
                                if original_price_tag
                                else ""
                            )

                            original_price = (
                                parse_price_to_int(
                                    original_price_text
                                )
                            )

                            # =========================================
                            # SALE PRICE
                            # =========================================

                            price_tag = (
                                card.select_one(
                                    "span.price-item.price-item--sale.price-item--last"
                                )
                            )

                            price_text = (
                                price_tag.get_text(
                                    " ",
                                    strip=True
                                )
                                if price_tag
                                else ""
                            )

                            price = parse_price_to_int(
                                price_text
                            )

                            # =========================================
                            # SAME PRICE CHECK
                            # =========================================

                            if (
                                price is not None
                                and original_price is not None
                                and price == original_price
                            ):

                                original_price = None

                            # =========================================
                            # DISCOUNT
                            # =========================================

                            discount_tag = card.select_one(
                                "hjv"
                            )

                            discount = None

                            if discount_tag:

                                discount_text = (
                                    discount_tag.get_text(
                                        " ",
                                        strip=True
                                    )
                                )

                                discount = (
                                    parse_discount_to_int(
                                        discount_text
                                    )
                                )

                            else:

                                if (
                                    original_price is not None
                                    and price is not None
                                    and original_price > 0
                                    and price <= original_price
                                ):

                                    discount = round(
                                        (
                                            (
                                                original_price
                                                - price
                                            )
                                            / original_price
                                        )
                                        * 100
                                    )

                            # =========================================
                            # IMAGE
                            # =========================================

                            img_tag = card.select_one(
                                "img.motion-reduce"
                            )

                            image_link = "N/A"

                            if img_tag:

                                src = (
                                    img_tag.get("src")
                                    or img_tag.get("data-src")
                                    or img_tag.get("data-srcset")
                                )

                                if src:

                                    if "," in src:

                                        src = (
                                            src
                                            .split(",")[0]
                                            .strip()
                                            .split(" ")[0]
                                        )

                                    image_link = urljoin(
                                        BASE_DOMAIN,
                                        src
                                    )

                            # =========================================
                            # RATING
                            # =========================================

                            rating_tag = card.select_one(
                                "p.rating-text.caption span"
                            )

                            rating = None

                            if rating_tag:

                                rating_text = (
                                    rating_tag.get_text(
                                        " ",
                                        strip=True
                                    )
                                )

                                rating = (
                                    parse_rating_to_float(
                                        rating_text
                                    )
                                )

                            # =========================================
                            # DEFAULT DESCRIPTION
                            # =========================================

                            description = (
                                name
                                if name != "N/A"
                                else "N/A"
                            )

                            # =========================================
                            # REQUIRED FIELD CHECK
                            # =========================================

                            if (
                                name == "N/A"
                                or price is None
                                or image_link == "N/A"
                                or product_link == "N/A"
                            ):

                                print(
                                    f"[SKIP] Incomplete product: "
                                    f"{name}"
                                )

                                continue

                            # =========================================
                            # PRODUCT DETAIL PAGE
                            # =========================================

                            print(
                                f"[INFO] Getting description: "
                                f"{name}"
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
                                            "html.parser"
                                        )
                                    )

                                    description_container = (
                                        product_soup.select_one(
                                            "div.metafield-rich_text_field"
                                        )
                                    )

                                    if description_container:

                                        extracted_description = (
                                            clean_description(
                                                description_container
                                            )
                                        )

                                        if extracted_description:

                                            description = (
                                                extracted_description
                                            )

                            except Exception as e:

                                print(
                                    f"[WARNING] Detail page "
                                    f"failed for {name}: {e}"
                                )

                            # =========================================
                            # TIMESTAMP
                            # =========================================

                            timestamp = datetime.now(
                                timezone(
                                    timedelta(
                                        hours=5,
                                        minutes=30
                                    )
                                )
                            ).strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )

                            # =========================================
                            # PRODUCT DATA
                            # =========================================

                            product_data = {

                                "name": name,

                                "price": price,

                                "currency": "₹",

                                "original_price": (
                                    original_price
                                    if original_price is not None
                                    else None
                                ),

                                "discount": discount,

                                "ratings": rating,

                                "description": description,

                                "image_link": image_link,

                                "product_link": product_link,

                                "organization_id": "Dealwallet",

                                "store_id": "Zinga Vita",

                                "categories_id": category,

                                "created_at": timestamp
                            }

                            # =========================================
                            # ADD RESULT
                            # =========================================

                            products.append(
                                product_data
                            )

                            category_results.append(
                                product_data
                            )

                            page_count += 1

                            print(
                                f"[SUCCESS] "
                                f"{len(products)}. "
                                f"{name} | "
                                f"₹{price} | "
                                f"Discount: {discount} | "
                                f"Rating: {rating}"
                            )

                        except Exception as e:

                            print(
                                f"[ERROR] Product processing "
                                f"failed: {e}"
                            )

                            continue

                    # =================================================
                    # PAGE SUMMARY
                    # =================================================

                    print(
                        f"[INFO] Valid products from "
                        f"page {page_num}: {page_count}"
                    )

                    print(
                        f"[INFO] Total products so far: "
                        f"{len(products)}"
                    )

                    await asyncio.sleep(
                        CRAWLER_DELAY / 1000
                    )

    # ================================================================
    # FINAL SUMMARY
    # ================================================================

    print("\n" + "=" * 80)
    print(
        f"[SUCCESS] Total products scraped: "
        f"{len(products)}"
    )
    print("=" * 80)

    return products


# ============================================================
# WINDOWS / PLAYWRIGHT PROCESS
# ============================================================

def run_crawl4ai_process():

    """
    Run Crawl4AI inside a separate process.

    Required on Windows because Playwright
    requires the Proactor event loop for
    subprocess support.
    """

    import sys

    if sys.platform == "win32":

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return asyncio.run(
        scrape_zingavita_async()
    )


# ============================================================
# SCRAPY SPIDER
# ============================================================

class ZingavitaSpider(scrapy.Spider):

    name = "zingavita"

    allowed_domains = [
        "zingavita.com",
        "www.zingavita.com"
    ]

    custom_settings = {

        "CONCURRENT_REQUESTS": 1,

        "DOWNLOAD_DELAY": 1,

    }

    async def start(self):

        print("\n" + "=" * 80)
        print("Starting Zingavita Spider")
        print("=" * 80)

        # ========================================================
        # RUN CRAWL4AI IN SEPARATE PROCESS
        # ========================================================

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_crawl4ai_process
            )

        # ========================================================
        # SAFETY CHECK
        # ========================================================

        if not isinstance(products, list):

            self.logger.error(
                "Unexpected Crawl4AI result type: %s",
                type(products).__name__
            )

            return

        # ========================================================
        # YIELD PRODUCTS
        # ========================================================

        for product in products:

            if not isinstance(product, dict):

                self.logger.error(
                    "Skipping invalid product type: %s",
                    type(product).__name__
                )

                continue

            # ====================================================
            # DATA TYPE CHECK
            # ====================================================

            print("\n[DATA TYPE CHECK]")

            print(
                "name           :",
                type(
                    product.get("name")
                ).__name__
            )

            print(
                "price          :",
                type(
                    product.get("price")
                ).__name__
            )

            print(
                "original_price :",
                type(
                    product.get("original_price")
                ).__name__
            )

            print(
                "discount       :",
                type(
                    product.get("discount")
                ).__name__
            )

            print(
                "ratings        :",
                type(
                    product.get("ratings")
                ).__name__
            )

            print(
                "currency       :",
                type(
                    product.get("currency")
                ).__name__
            )

            print(
                "description    :",
                type(
                    product.get("description")
                ).__name__
            )

            print(
                "image_link     :",
                type(
                    product.get("image_link")
                ).__name__
            )

            print(
                "product_link   :",
                type(
                    product.get("product_link")
                ).__name__
            )

            print(
                "organization_id:",
                type(
                    product.get("organization_id")
                ).__name__
            )

            print(
                "store_id       :",
                type(
                    product.get("store_id")
                ).__name__
            )

            print(
                "categories_id  :",
                type(
                    product.get("categories_id")
                ).__name__
            )

            # ====================================================
            # YIELD TO DEALWALLET PIPELINE
            # ====================================================

            yield product