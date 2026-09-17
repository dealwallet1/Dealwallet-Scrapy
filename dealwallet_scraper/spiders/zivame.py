import asyncio
import json
import logging
import os
import re
import sys

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import scrapy

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler


# ============================================================
# UTF-8 CONSOLE CONFIGURATION
# ============================================================

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    try:
        os.environ["PYTHONIOENCODING"] = "utf-8"
    except Exception:
        pass


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URLS = {

    "Fashion & Lifestyle": [

        "https://www.zivame.com/sleepwear-nightwear.html?topsku=ZI64ZG-Vapor%20Blue,ZI64Z1-DeauvilleMauve,ZI654A-Moonlight%20Jade,ZI6549-Lavender%20Fog,ZI6518-TrueNavy,ZI654B-Moonlight%20Jade,ZI6549-Fig,ZI64NN-Heirloom%20Lilac,ZI6549-Cerulean,ZI650M-Orchid%20Tint,ZI654D-Wisteria,ZI654C-Icemelt,ZI64V1-Wetweather,ZI64VH-Jazzy,ZI64VH-Peachparfai,ZI6518-CrystalRose,ZI650N-Heavenly%20Pink,ZI654C-Wisteria,ZI651A-AlmostAqua,ZI64N5-Salt%20Air,ZI651A-MistGreen,ZI650J-Raw%20Sienna,ZI64SJ-Fawn,ZI651A-NantucketBreeze,ZI650M-Heavenly%20Pink,ZI654B-Moonlight%20Jade,ZI651A-CrytalRose,ZI64N6-Soybee&trksrc=navbar&trkid=l1",

        #  "https://www.zivame.com/loungewear.html?&trksrc=navbar&trkid=l1",

        # "https://www.zivame.com/winter-must-haves.html?category=nightwear&topsku=ZI64Q8-Silver%20Peony,ZI64Q4-Medieval%20Blue,ZI652V-Black%20Beauty&trksrc=navbar&trkid=l1"

    ],

}

# ============================================================
# SETTINGS
# ============================================================

MAX_PAGES = 5

JSON_FILE = "scrape_zivame.json"




# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ============================================================
# PRICE PARSER
# ============================================================

def parse_price_to_float(
    price_str
):

    if not price_str:
        return None

    if price_str == "N/A":
        return None

    clean = re.sub(
        r"[^\d.]",
        "",
        str(price_str),
    )

    if not clean:
        return None

    try:

        return int(clean)

    except ValueError:

        return None


# ============================================================
# CLEAN PRICE
# ============================================================

def clean_price(
    price
):

    if price is None:
        return "N/A"

    price = str(
        price
    )

    clean = (
        price
        .replace("₹", "")
        .replace(",", "")
        .replace(".00", "")
        .strip()
    )

    return (
        clean
        if clean
        else "N/A"
    )


# ============================================================
# DISCOUNT
# ============================================================

def calculate_discount(
    original_price,
    price
):

    original_val = parse_price_to_float(
        original_price
    )

    price_val = parse_price_to_float(
        price
    )

    if (
        original_val
        and price_val
        and original_val > 0
        and price_val <= original_val
    ):

        discount_percent = round(
            (
                (
                    original_val
                    - price_val
                )
                / original_val
            )
            * 100
        )

        return f"{discount_percent}% OFF"

    return "N/A"


# ============================================================
# NORMALIZE TEXT
# ============================================================

def normalize_text(
    text
):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

async def get_product_description(
    crawler,
    product_link
):

    if (
        not product_link
        or product_link == "N/A"
    ):

        return "N/A"

    try:

        print(
            f"   [INFO] Getting description: "
            f"{product_link}"
        )

        detail_page = await crawler.arun(
            url=product_link
        )

        if not detail_page.success:

            print(
                "   [WARNING] Detail page failed"
            )

            return "N/A"

        detail_soup = BeautifulSoup(
            detail_page.html,
            "html.parser",
        )

        # ====================================================
        # PRODUCT DESCRIPTION SECTION
        # ====================================================

        description_section = (
            detail_soup.select_one(
                "section#prd-description"
            )
        )

        if not description_section:

            print(
                "   [WARNING] Description section not found"
            )

            return "N/A"

        # ====================================================
        # DESCRIPTION ITEMS
        # ====================================================

        description_items = (
            description_section.select(
                "ul.ProductDescription_list__yCQPj li"
            )
        )

        descriptions = []

        for item in description_items:

            text = item.get_text(
                " ",
                strip=True,
            )

            if not text:
                continue

            # ------------------------------------------------
            # REMOVE NET QUANTITY
            # ------------------------------------------------

            text = re.sub(
                r"\s*,?\s*Net\s*Quantity\s*:\s*[^,|]+",
                "",
                text,
                flags=re.IGNORECASE,
            )

            # ------------------------------------------------
            # REMOVE PHONE NUMBER
            # ------------------------------------------------

            text = re.sub(
                r"\s*,?\s*Phone\s*number\s*[-:]?.*$",
                "",
                text,
                flags=re.IGNORECASE,
            )

            # ------------------------------------------------
            # REMOVE EMAIL
            # ------------------------------------------------

            text = re.sub(
                r"\s*,?\s*Email\s*[-:]?.*$",
                "",
                text,
                flags=re.IGNORECASE,
            )

            # ------------------------------------------------
            # REMOVE ADDRESS / CONTACT
            # ------------------------------------------------

            text = re.sub(
                r"\s*,?\s*(?:Address|Contact\s*Us|"
                r"Customer\s*Care|Customer\s*Service)"
                r"\s*[-:]?.*$",
                "",
                text,
                flags=re.IGNORECASE,
            )

            # ------------------------------------------------
            # REPLACE PIPE
            # ------------------------------------------------

            text = text.replace(
                "|",
                ",",
            )

            # ------------------------------------------------
            # CLEAN COMMAS
            # ------------------------------------------------

            text = re.sub(
                r"\s*,\s*,+",
                ",",
                text,
            )

            # ------------------------------------------------
            # CLEAN SPACES
            # ------------------------------------------------

            text = re.sub(
                r"\s+",
                " ",
                text,
            )

            text = text.strip(
                " ,"
            )

            if text:

                descriptions.append(
                    text
                )

        # ====================================================
        # JOIN DESCRIPTION
        # ====================================================

        if descriptions:

            description = ", ".join(
                descriptions
            )

            return description

        return "N/A"

    except Exception as exc:

        print(
            f"   [ERROR] Description error: {exc}"
        )

        return "N/A"


# ============================================================
# CRAWL4AI SCRAPER
# ============================================================

async def scrape_zivame():

    results = []

    # --------------------------------------------------------
    # DUPLICATE TRACKING
    # --------------------------------------------------------

    seen_links = set()

    seen_product_ids = set()

    # --------------------------------------------------------
    # CATEGORY COUNTS
    # --------------------------------------------------------

    category_counts = {}

    # --------------------------------------------------------
    # START CRAWLER
    # --------------------------------------------------------

    for category, urls in BASE_URLS.items():

        category_results = []

        print(
            "\n" + "=" * 70
        )

        print(
            f"SCRAPING CATEGORY: {category}"
        )

        print(
            "=" * 70
        )

        # =================================================
        # URL LOOP
        # =================================================

        for url_index, base_url in enumerate(
            urls,
            start=1,
        ):

            page_num = 1

            url_products = 0

            print(
                "\n" + "#" * 70
            )

            print(
                f"STARTING URL {url_index}/{len(urls)}"
            )

            print(
                base_url
            )

            print(
                "#" * 70
            )

            # =================================================
            # PAGINATION
            # =================================================

            # =================================================
            # FRESH CRAWLER FOR THIS URL
            # =================================================

            async with AsyncWebCrawler(
                verbose=True
            ) as crawler:

                # ====================================================
                # BROWSER CONFIG
                # ====================================================

                crawler.browser_config = {

                    "headless": True,

                    "javascript": True,

                    "user_agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/131.0.0.0 "
                        "Safari/537.36"
                    ),
                }

                # ====================================================
                # CRAWLER CONFIG
                # ====================================================

                crawler.crawler_run_config = {

                    "wait_until": "networkidle",
                    "timeout": 60000,
                    "delay": 5000,
                    "wait_for": {
                        "selector":
                            "article.ProductCard_card__xnckO",
                        "timeout":
                            30000,
                    },
                }

                while page_num <= MAX_PAGES:

                    # -------------------------------------------------
                    # PAGINATION URL
                    # -------------------------------------------------

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

                    print(
                        "\n" + "-" * 70
                    )

                    print(
                        f"Category : {category}"
                    )

                    print(
                        f"URL      : {url_index}/{len(urls)}"
                    )

                    print(
                        f"Page     : {page_num}"
                    )

                    print(
                        f"URL      : {url}"
                    )

                    print(
                        "-" * 70
                    )

                    # =================================================
                    # CRAWL CATEGORY PAGE
                    # =================================================

                    try:

                        page = await crawler.arun(
                            url=url
                        )

                    except Exception as exc:

                        print(
                            f"[ERROR] Crawler error "
                            f"on page {page_num}: "
                            f"{exc}"
                        )

                        break

                    # =================================================
                    # CHECK SUCCESS
                    # =================================================

                    if not page.success:

                        print(
                            f"[ERROR] Failed to crawl "
                            f"page {page_num}"
                        )

                        break

                    # =================================================
                    # PARSE HTML
                    # =================================================

                    soup = BeautifulSoup(
                        page.html,
                        "html.parser",
                    )

                    # =================================================
                    # PRODUCT CARDS
                    # =================================================

                    product_cards = soup.select(
                        "article.ProductCard_card__xnckO"
                    )

                    print(
                        f"Products found on page "
                        f"{page_num}: "
                        f"{len(product_cards)}"
                    )

                    # =================================================
                    # NO PRODUCTS
                    # =================================================

                    if not product_cards:

                        print(
                            "[WARNING] No product cards found. "
                            "Stopping pagination."
                        )

                        break

                    # =================================================
                    # PRODUCT LOOP
                    # =================================================

                    for card_index, card in enumerate(
                        product_cards,
                        start=1,
                    ):

                        try:

                            # =========================================
                            # PRODUCT ID
                            # =========================================

                            product_id = card.get(
                                "data-product-id",
                                "",
                            ).strip()

                            # =========================================
                            # SKU
                            # =========================================

                            sku = card.get(
                                "data-sku",
                                "",
                            ).strip()

                            # =========================================
                            # PRODUCT NAME
                            # =========================================

                            name = card.get(
                                "data-name",
                                "",
                            ).strip()

                            # -----------------------------------------
                            # FALLBACK NAME
                            # -----------------------------------------

                            if not name:

                                name_tag = (
                                    card.select_one(
                                        "a.ProductCard_name__ZkOcu"
                                    )
                                )

                                if name_tag:

                                    name = (
                                        name_tag
                                        .get_text(
                                            strip=True
                                        )
                                    )

                                else:

                                    name = "N/A"

                            name = normalize_text(
                                name
                            )

                            # =========================================
                            # PRODUCT LINK
                            # =========================================

                            link_tag = (
                                card.select_one(
                                    "a.ProductCard_name__ZkOcu"
                                )
                            )

                            if (
                                link_tag
                                and link_tag.get("href")
                            ):

                                product_link = urljoin(
                                    base_url,
                                    link_tag.get(
                                        "href"
                                    ),
                                )

                            else:

                                purl = card.get(
                                    "data-purl"
                                )

                                if purl:

                                    product_link = urljoin(
                                        base_url,
                                        purl,
                                    )

                                else:

                                    product_link = "N/A"

                            # =========================================
                            # DUPLICATE KEY
                            # =========================================

                            unique_key = (
                                product_id
                                or product_link
                                or name.lower()
                            )

                            if (
                                unique_key
                                in seen_product_ids
                                or product_link
                                in seen_links
                            ):

                                print(
                                    f"[SKIP] Duplicate skipped: "
                                    f"{name}"
                                )

                                continue

                            # =========================================
                            # CURRENT PRICE
                            # =========================================

                            price = card.get(
                                "data-specialprice"
                            )

                            # -----------------------------------------
                            # FALLBACK PRICE
                            # -----------------------------------------

                            if not price:

                                price_tag = (
                                    card.select_one(
                                        "span.ProductCard_price__7yDPv"
                                    )
                                )

                                if price_tag:

                                    price = (
                                        price_tag
                                        .get_text(
                                            strip=True
                                        )
                                    )

                            price = clean_price(
                                price
                            )

                            # =========================================
                            # ORIGINAL PRICE
                            # =========================================

                            original_price = card.get(
                                "data-price"
                            )

                            # -----------------------------------------
                            # FALLBACK MRP
                            # -----------------------------------------

                            if not original_price:

                                original_price_tag = (
                                    card.select_one(
                                        "span.ProductCard_mrp__tAO8J"
                                    )
                                )

                                if original_price_tag:

                                    original_price = (
                                        original_price_tag
                                        .get_text(
                                            strip=True
                                        )
                                    )

                            original_price = clean_price(
                                original_price
                            )

                            # =========================================
                            # SAME PRICE CHECK
                            # =========================================

                            price_val = (
                                parse_price_to_float(
                                    price
                                )
                            )

                            original_val = (
                                parse_price_to_float(
                                    original_price
                                )
                            )

                            if (
                                price_val
                                and original_val
                                and price_val == original_val
                            ):

                                original_price = ""

                            # =========================================
                            # DISCOUNT
                            # =========================================

                            discount_tag = (
                                card.select_one(
                                    "span.ProductCard_discount__jJc10"
                                )
                            )

                            if discount_tag:

                                discount = (
                                    discount_tag
                                    .get_text(
                                        strip=True
                                    )
                                )

                                discount = (
                                    discount
                                    if "%"
                                    in discount
                                    else (
                                        f"{discount}"
                                        f"% OFF"
                                    )
                                )

                            else:

                                discount = (
                                    calculate_discount(
                                        original_price,
                                        price,
                                    )
                                )

                            # =========================================
                            # IMAGE
                            # =========================================

                            img_tag = (
                                card.select_one(
                                    "img.ProductCard_image__EmM6A"
                                )
                            )

                            if img_tag:

                                src = (
                                    img_tag.get(
                                        "src"
                                    )
                                    or img_tag.get(
                                        "data-src"
                                    )
                                    or img_tag.get(
                                        "data-srcset"
                                    )
                                )

                                if src:

                                    image_link = urljoin(
                                        base_url,
                                        src,
                                    )

                                else:

                                    image_link = "N/A"

                            else:

                                image_link = "N/A"

                            # =========================================
                            # RATING
                            # =========================================

                            rating = card.get(
                                "data-rating",
                                "",
                            ).strip()

                            # -----------------------------------------
                            # FALLBACK RATING
                            # -----------------------------------------

                            if not rating:

                                rating_tag = (
                                    card.select_one(
                                        "span.ProductCard_ratingVal__l2VBz"
                                    )
                                )

                                if rating_tag:

                                    rating = (
                                        rating_tag
                                        .get_text(
                                            strip=True
                                        )
                                    )

                            rating = (
                                normalize_text(
                                    rating
                                )
                                if rating
                                else "N/A"
                            )

                            # =========================================
                            # VALIDATION
                            # =========================================

                            if name == "N/A":

                                print(
                                    "[WARNING] Skipping product "
                                    "without name"
                                )

                                continue

                            if price == "N/A":

                                print(
                                    f"[WARNING] Skipping {name}: "
                                    f"price unavailable"
                                )

                                continue

                            if image_link == "N/A":

                                print(
                                    f"[WARNING] Skipping {name}: "
                                    f"image unavailable"
                                )

                                continue

                            if product_link == "N/A":

                                print(
                                    f"[WARNING] Skipping {name}: "
                                    f"URL unavailable"
                                )

                                continue

                            # =========================================
                            # PRODUCT DETAIL DESCRIPTION
                            # =========================================

                            description = (
                                await get_product_description(
                                    crawler,
                                    product_link,
                                )
                            )

                            # =========================================
                            # TIMESTAMP
                            # =========================================

                            timestamp = datetime.now(
                                timezone(
                                    timedelta(
                                        hours=5,
                                        minutes=30,
                                    )
                                )
                            ).strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )

                            # =========================================
                            # PRODUCT DATA
                            # =========================================

                            # =========================================
                            # DATA TYPE CONVERSION
                            # =========================================

                            price_value = parse_price_to_float(
                                price
                            )

                            original_price_value = (
                                parse_price_to_float(
                                    original_price
                                )
                                if original_price
                                else None
                            )

                            discount_value = None

                            if discount and discount != "N/A":
                                discount_match = re.search(
                                    r"(\d+(?:\.\d+)?)",
                                    str(discount),
                                )

                                if discount_match:
                                    discount_value = int(
                                        float(
                                            discount_match.group(1)
                                        )
                                    )

                            rating_value = None

                            if rating and rating != "N/A":
                                rating_match = re.search(
                                    r"(\d+(?:\.\d+)?)",
                                    str(rating),
                                )

                                if rating_match:
                                    rating_value = float(
                                        rating_match.group(1)
                                    )

                            # =========================================
                            # PRODUCT DATA
                            # =========================================

                            product_data = {
                                "name":
                                    str(name),

                                "price":
                                    price_value,

                                "currency":
                                    "₹",

                                "original_price":
                                    original_price_value,

                                "discount":
                                    discount_value,

                                "ratings":
                                    rating_value,

                                "description":
                                str(name)
                                if not description or description == "N/A"
                                else str(description),

                                "image_link":
                                    str(image_link),

                                "product_link":
                                    str(product_link),

                                "organization_id":
                                    "Dealwallet",

                                "store_id":
                                    "Zivame",

                                "categories_id":
                                    str(category),

                                "created_at":
                                    timestamp,

                                "affiliate_url":
                                    None,
                            }

                            # =========================================
                            # SAVE
                            # =========================================

                            results.append(
                                product_data
                            )

                            category_results.append(
                                product_data
                            )

                            url_products += 1

                            # =========================================
                            # MARK SEEN
                            # =========================================

                            if product_id:

                                seen_product_ids.add(
                                    product_id
                                )

                            if product_link:

                                seen_links.add(
                                    product_link
                                )

                            # =========================================
                            # PRINT
                            # =========================================

                            print(
                                f"\n[SUCCESS] PRODUCT "
                                f"{len(results)}"
                            )

                            print(
                                f"   Name        : "
                                f"{name}"
                            )

                            print(
                                f"   Price       : "
                                f"₹{price}"
                            )

                            print(
                                f"   MRP         : "
                                f"₹{original_price}"
                            )

                            print(
                                f"   Discount    : "
                                f"{discount}"
                            )

                            print(
                                f"   Rating      : "
                                f"{rating}"
                            )

                            print(
                                f"   Description : "
                                f"{description[:200]}"
                                if description
                                and description != "N/A"
                                else
                                "   Description : N/A"
                            )

                            print(
                                f"   URL         : "
                                f"{product_link}"
                            )

                        except Exception as exc:

                            print(
                                f"[ERROR] Error parsing "
                                f"product #{card_index}: "
                                f"{exc}"
                            )

                    # =================================================
                    # PAGE COMPLETE
                    # =================================================

                    print(
                        f"\n[PAGE] Page {page_num} completed"
                    )

                    print(
                        f"   Products on page: "
                        f"{len(product_cards)}"
                    )

                    print(
                        f"   New products: "
                        f"{url_products}"
                    )

                    # =================================================
                    # NEXT PAGE
                    # =================================================

                    page_num += 1

                # =================================================
                print(
                    "Fresh Crawl4AI session completed for this URL."
                )

            # URL SUMMARY
            # =================================================

            print(
                "\n" + "-" * 70
            )

            print(
                f"URL {url_index} COMPLETED"
            )

            print(
                f"Products saved from URL: "
                f"{url_products}"
            )

            print(
                "-" * 70
            )

        # =================================================
        # CATEGORY COUNT
        # =================================================

        category_counts[category] = len(
            category_results
        )

        print(
            "\n" + "=" * 70
        )

        print(
            f"[PRODUCTS] Total products scraped "
            f"for '{category}': "
            f"{len(category_results)}"
        )

        print(
            f"[STATS] Total products so far: "
            f"{len(results)}"
        )

        print(
            "=" * 70
        )

# ========================================================
    # FINAL RESULT
    # ========================================================

    print(
        "\n" + "=" * 70
    )

    print(
        "ZIVAME SCRAPING COMPLETED"
    )

    print(
        "=" * 70
    )

    print(
        f"Total products: "
        f"{len(results)}"
    )

    print(
        f"Category counts: "
        f"{category_counts}"
    )

    print(
        "=" * 70
    )

    # ========================================================
    # SAVE JSON
    # ========================================================

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            results,
            file,
            ensure_ascii=False,
            indent=4,
        )

    print(
        f"[SUCCESS] JSON saved successfully: "
        f"{JSON_FILE}"
    )

    print(
        f"[PRODUCTS] Products saved: "
        f"{len(results)}"
    )

    return results


# ============================================================
# SEPARATE PROCESS RUNNER
# ============================================================

def run_zivame_scraper():

    # --------------------------------------------------------
    # FORCE UTF-8 AGAIN INSIDE CHILD PROCESS
    # --------------------------------------------------------

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "[%(levelname)s] "
            "%(message)s"
        ),
        stream=sys.stdout,
        force=True,
    )

    # --------------------------------------------------------
    # WINDOWS EVENT LOOP
    # --------------------------------------------------------

    if sys.platform == "win32":

        try:

            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )

        except AttributeError:

            pass

    logging.info(
        "=" * 60
    )

    logging.info(
        "Zivame Crawl4AI process started."
    )

    logging.info(
        "=" * 60
    )

    try:

        results = asyncio.run(
            scrape_zivame()
        )

        logging.info(
            "Zivame Crawl4AI process completed."
        )

        logging.info(
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "Zivame Crawl4AI process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class ZivameSpider(
    scrapy.Spider
):

    name = "zivame"

    allowed_domains = [
        "zivame.com",
        "www.zivame.com",
    ]

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS": 1,
    }

    # ========================================================
    # SCRAPY 2.18 START
    # ========================================================

    async def start(
        self
    ):

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Starting Zivame Scrapy spider..."
        )

        self.logger.info(
            "=" * 60
        )

        # ----------------------------------------------------
        # RUN CRAWL4AI IN SEPARATE PROCESS
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_zivame_scraper,
            )

        # ----------------------------------------------------
        # SAVE SCRAPY JSON
        # ----------------------------------------------------

        with open(
            JSON_FILE,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                products,
                file,
                ensure_ascii=False,
                indent=4,
            )

        self.logger.info(
            "Total products received: %s",
            len(products),
        )

        # ----------------------------------------------------
        # YIELD PRODUCTS TO SCRAPY PIPELINE
        # ----------------------------------------------------

        for product in products:

            if not product.get(
                "name"
            ):

                continue

            if not product.get(
                "price"
            ):

                continue

            if not product.get(
                "product_link"
            ):

                continue

            if not product.get(
                "image_link"
            ):

                continue

            yield product

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Zivame Scrapy spider completed."
        )

        self.logger.info(
            "=" * 60
        )
