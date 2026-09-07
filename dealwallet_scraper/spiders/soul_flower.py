import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.soulflower.in"

MAX_PAGES = 2

BASE_URLS = {
    "Beauty": [
        "https://www.soulflower.in/collections/essential-oils",

        # Add more collections here when required:
        # "https://www.soulflower.in/collections/hair-oils",
        # "https://www.soulflower.in/collections/hair-serum",
        # "https://www.soulflower.in/collections/shampoos",
        # "https://www.soulflower.in/collections/face-wash",
        # "https://www.soulflower.in/collections/face-masks",
        # "https://www.soulflower.in/collections/serums",
        # "https://www.soulflower.in/collections/sunscreen",
    ]
}

STORE_NAME = "Soulflower"
ORGANIZATION_ID = "Dealwallet"


# ============================================================
# HELPERS
# ============================================================

def clean_to_int(text):
    if not text:
        return 0

    txt = (
        str(text)
        .replace("₹", "")
        .replace(",", "")
        .strip()
    )

    match = re.search(r"(\d+)", txt)

    return int(match.group(1)) if match else 0


def discount_to_int(value):
    if value is None:
        return None

    match = re.search(
        r"(\d+)",
        str(value)
    )

    return int(match.group(1)) if match else None


def parse_rating_to_float(rating_str):
    if rating_str is None:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(rating_str)
    )

    if not match:
        return None

    try:
        value = float(match.group())

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def parse_price_to_float(price_str):
    if not price_str:
        return None

    if price_str in (
        "N/A",
        "-",
        "",
    ):
        return None

    clean = re.sub(
        r"[^\d.]",
        "",
        str(price_str)
    )

    if not clean:
        return None

    try:
        value = float(clean)

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def clean_truncated_text(text):
    if not text:
        return None

    return (
        str(text)
        .rstrip(".")
        .rstrip("…")
        .strip()
    )


def extract_rating_preserve_decimal(text):
    """
    Examples:

    5 / 5       -> 5
    4.5 / 5     -> 4.5
    4.88 / 5    -> 4.88
    """

    if not text:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(text)
    )

    if not match:
        return None

    value = match.group()

    if "." in value:
        return float(value)

    return int(value)


# ============================================================
# PRODUCT DESCRIPTION
# ============================================================

def extract_product_description(product_html):

    if not product_html:
        return None

    product_soup = BeautifulSoup(
        product_html,
        "html.parser"
    )

    sections = []

    stop_keywords = [
        "additional information",
        "commodity",
        "country of origin",
        "manufacturer",
        "customer care",
        "best before",
        "net quantity",
        "email",
        "tel",
    ]

    skip_titles = {
        "ingredients",
        "additional information",
    }

    accordions = product_soup.select(
        "div.product__accordion details"
    )

    for accordion in accordions:

        title_tag = accordion.select_one(
            "h2.accordion__title"
        )

        content_tag = accordion.select_one(
            "div.accordion__content"
        )

        if not title_tag or not content_tag:
            continue

        title_raw = title_tag.get_text(
            " ",
            strip=True
        )

        title = title_raw.lower()

        if title in skip_titles:
            continue

        # Remove unnecessary HTML elements
        for tag in content_tag.select(
            "svg, img, button"
        ):
            tag.decompose()

        # ----------------------------------------------------
        # WHAT DOES IT DO
        # ----------------------------------------------------

        if title == "what does it do":

            blocks = content_tag.select(
                "div.what-does-it-do > div"
            )

            parts = []

            for block in blocks:

                heading_tag = block.select_one(
                    "h6"
                )

                if not heading_tag:
                    continue

                heading = heading_tag.get_text(
                    " ",
                    strip=True
                )

                heading_tag.decompose()

                desc = block.get_text(
                    " ",
                    strip=True
                )

                desc = " ".join(
                    desc.split()
                )

                if desc:
                    parts.append(
                        f"{heading} - {desc}"
                    )

            if parts:

                sections.append(
                    f"{title_raw}: "
                    + " ".join(parts)
                )

            continue

        # ----------------------------------------------------
        # NORMAL CONTENT
        # ----------------------------------------------------

        texts = []

        for element in content_tag.find_all(
            [
                "p",
                "li",
                "strong",
                "h6",
                "label",
            ],
            recursive=True
        ):

            text = element.get_text(
                " ",
                strip=True
            )

            if text:
                texts.append(text)

        if not texts:
            continue

        clean_text = " ".join(texts)

        clean_text = " ".join(
            clean_text.split()
        )

        # ----------------------------------------------------
        # STOP INFORMATION
        # ----------------------------------------------------

        lower_text = clean_text.lower()

        for keyword in stop_keywords:

            index = lower_text.find(
                keyword
            )

            if index != -1:

                clean_text = (
                    clean_text[:index]
                    .strip()
                )

                break

        # ----------------------------------------------------
        # REMOVE DUPLICATE CONSECUTIVE WORDS
        # ----------------------------------------------------

        words = clean_text.split()

        deduped = []

        for word in words:

            if (
                not deduped
                or deduped[-1].lower()
                != word.lower()
            ):
                deduped.append(word)

        final_text = " ".join(
            deduped
        )

        if final_text:

            sections.append(
                f"{title_raw}: "
                f"{final_text}"
            )

    if not sections:
        return None

    return " || ".join(
        sections
    )


# ============================================================
# IMAGE EXTRACTION
# ============================================================

async def extract_image(card):

    image_locator = card.locator(
        "img"
    )

    if await image_locator.count() == 0:

        image_locator = card.locator(
            "picture img"
        )

    if await image_locator.count() == 0:
        return None

    image_tag = image_locator.first

    attributes = [
        "src",
        "data-src",
        "data-srcset",
        "data-lazy-src",
    ]

    for attribute in attributes:

        value = await image_tag.get_attribute(
            attribute
        )

        if not value:
            continue

        value = value.strip()

        if not value:
            continue

        # srcset can contain multiple URLs
        image_url = value.split(",")[0].strip()

        image_url = image_url.split()[0]

        if image_url.startswith("//"):
            return "https:" + image_url

        return urljoin(
            BASE_URL,
            image_url
        )

    return None


# ============================================================
# CRAWL SOULFLOWER
# ============================================================

async def scrape_soulflower_async():

    results = []

    seen_links = set()
    seen_names = set()

    category_counts = {}

    async with async_playwright() as playwright:

        # ====================================================
        # BROWSER
        # ====================================================

        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 "
                "Safari/537.36"
            ),
            viewport={
                "width": 1920,
                "height": 1080,
            },
        )

        page = await context.new_page()

        page.set_default_timeout(
            60000
        )

        page.set_default_navigation_timeout(
            60000
        )

        # ====================================================
        # CATEGORY LOOP
        # ====================================================

        for category, urls in BASE_URLS.items():

            category_results = []

            # =================================================
            # URL LOOP
            # =================================================

            for base_url in urls:

                page_number = 1

                while page_number <= MAX_PAGES:

                    url = (
                        f"{base_url}"
                        f"?page={page_number}"
                    )

                    logging.info(
                        f"Fetching: {url}"
                    )

                    # =========================================
                    # OPEN LISTING PAGE
                    # =========================================

                    try:

                        response = await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=60000,
                        )

                    except Exception as exc:

                        logging.error(
                            f"Failed to fetch "
                            f"{url}: {exc}"
                        )

                        break

                    if response:

                        logging.info(
                            f"HTTP status: "
                            f"{response.status}"
                        )

                    # =========================================
                    # WAIT FOR PRODUCT CARDS
                    # =========================================

                    try:

                        await page.wait_for_selector(
                            "li.grid__item",
                            timeout=30000,
                        )

                    except Exception:

                        logging.warning(
                            f"Product selector "
                            f"not found on page "
                            f"{page_number}: {url}"
                        )

                        break

                    # =========================================
                    # PRODUCT CARDS
                    # =========================================

                    product_cards = page.locator(
                        "li.grid__item"
                    )

                    card_count = await product_cards.count()

                    if card_count == 0:

                        logging.info(
                            f"No product cards "
                            f"found on page "
                            f"{page_number}"
                        )

                        break

                    logging.info(
                        f"Found {card_count} "
                        f"product cards on "
                        f"page {page_number}"
                    )

                    # =========================================
                    # PRODUCT LOOP
                    # =========================================

                    for index in range(
                        card_count
                    ):

                        try:

                            card = product_cards.nth(
                                index
                            )

                            # =================================
                            # PRODUCT NAME
                            # =================================

                            name_locator = card.locator(
                                "a.full-unstyled-link"
                            )

                            if (
                                await name_locator.count()
                                == 0
                            ):

                                name_locator = (
                                    card.locator(
                                        "a.product-title"
                                    )
                                )

                            if (
                                await name_locator.count()
                                == 0
                            ):

                                name_locator = (
                                    card.locator(
                                        "h2 a"
                                    )
                                )

                            if (
                                await name_locator.count()
                                == 0
                            ):

                                logging.warning(
                                    f"Product name "
                                    f"not found for "
                                    f"card #{index + 1}"
                                )

                                continue

                            name = await name_locator.first.inner_text()

                            name = normalize_product_name(
                                name
                            )

                            if not name:
                                continue

                            # =================================
                            # PRODUCT LINK
                            # =================================

                            link_locator = card.locator(
                                "a"
                            )

                            product_link = None

                            if (
                                await link_locator.count()
                                > 0
                            ):

                                href = await (
                                    link_locator
                                    .first
                                    .get_attribute(
                                        "href"
                                    )
                                )

                                if href:

                                    product_link = (
                                        urljoin(
                                            base_url,
                                            href
                                        )
                                    )

                            if not product_link:
                                continue

                            # =================================
                            # ORIGINAL PRICE
                            # =================================

                            original_price_locator = (
                                card.locator(
                                    "s.price-item.price-item--regular"
                                )
                            )

                            if (
                                await original_price_locator.count()
                                == 0
                            ):

                                original_price_locator = (
                                    card.locator(
                                        "span.price--compare-at"
                                    )
                                )

                            if (
                                await original_price_locator.count()
                                == 0
                            ):

                                original_price_locator = (
                                    card.locator(
                                        "span.original-price"
                                    )
                                )

                            original_price_text = ""

                            if (
                                await original_price_locator.count()
                                > 0
                            ):

                                original_price_text = (
                                    await
                                    original_price_locator
                                    .first
                                    .inner_text()
                                )

                                original_price_text = (
                                    original_price_text
                                    .replace("₹", "")
                                    .replace(",", "")
                                    .replace("Rs. ", "")
                                    .strip()
                                )

                            # =================================
                            # SALE PRICE
                            # =================================

                            price_locator = card.locator(
                                "span.price-item.price-item--sale"
                            )

                            if (
                                await price_locator.count()
                                == 0
                            ):

                                price_locator = (
                                    card.locator(
                                        "span.price-item.price-item--regular"
                                    )
                                )

                            if (
                                await price_locator.count()
                                == 0
                            ):

                                price_locator = (
                                    card.locator(
                                        "span.price"
                                    )
                                )

                            if (
                                await price_locator.count()
                                == 0
                            ):

                                price_locator = (
                                    card.locator(
                                        "span.current-price"
                                    )
                                )

                            sale_price_text = ""

                            if (
                                await price_locator.count()
                                > 0
                            ):

                                sale_price_text = (
                                    await
                                    price_locator
                                    .first
                                    .inner_text()
                                )

                                sale_price_text = (
                                    sale_price_text
                                    .replace("₹", "")
                                    .replace(",", "")
                                    .replace("Rs. ", "")
                                )

                                if original_price_text:

                                    sale_price_text = (
                                        sale_price_text.replace(
                                            original_price_text,
                                            ""
                                        )
                                    )

                                sale_price_text = re.sub(
                                    r"(Regular price|Sale price)",
                                    "",
                                    sale_price_text,
                                    flags=re.I,
                                ).strip()

                            else:

                                sale_price_text = "N/A"

                            # =================================
                            # PRICE VALUES
                            # =================================

                            original_val = (
                                parse_price_to_float(
                                    original_price_text
                                )
                            )

                            price_val = (
                                parse_price_to_float(
                                    sale_price_text
                                )
                            )

                            # =================================
                            # PRICE VALIDATION
                            # =================================

                            if (
                                original_val is not None
                                and price_val is not None
                                and price_val > original_val
                            ):

                                logging.info(
                                    f"Skipping {name} "
                                    f"because sale price "
                                    f"({price_val}) is greater "
                                    f"than original price "
                                    f"({original_val})"
                                )

                                continue

                            # =================================
                            # DISCOUNT
                            # =================================

                            if (
                                original_val is not None
                                and price_val is not None
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

                                discount = (
                                    discount_percent
                                )

                            else:

                                discount = None

                            # =================================
                            # ONLY DISCOUNTED PRODUCTS
                            # =================================

                            if discount is None:
                                continue

                            # =================================
                            # IMAGE
                            # =================================

                            image_link = await extract_image(
                                card
                            )

                            if not image_link:
                                logging.info(
                                    f"Skipping {name} "
                                    f"because image "
                                    f"was not found"
                                )

                                continue

                            # =================================
                            # RATING
                            # =================================

                            rating = None

                            rating_locator = card.locator(
                                "p.rating-text"
                            )

                            if (
                                await rating_locator.count()
                                > 0
                            ):

                                rating_text = (
                                    await
                                    rating_locator
                                    .first
                                    .inner_text()
                                )

                                rating = (
                                    extract_rating_preserve_decimal(
                                        rating_text
                                    )
                                )

                            # =================================
                            # DUPLICATE
                            # =================================

                            if product_link in seen_links:
                                continue

                            if name.lower() in seen_names:
                                continue

                            # =================================
                            # PRODUCT DETAIL PAGE
                            # =================================

                            description = None

                            product_page = (
                                await context.new_page()
                            )

                            product_page.set_default_timeout(
                                60000
                            )

                            product_page.set_default_navigation_timeout(
                                60000
                            )

                            try:

                                await product_page.goto(
                                    product_link,
                                    wait_until="domcontentloaded",
                                    timeout=60000,
                                )

                                await product_page.wait_for_timeout(
                                    2000
                                )

                                product_html = (
                                    await product_page.content()
                                )

                                description = (
                                    extract_product_description(
                                        product_html
                                    )
                                )

                            except Exception as exc:

                                logging.warning(
                                    f"Failed product page "
                                    f"{product_link}: "
                                    f"{exc}"
                                )

                            finally:

                                await product_page.close()

                            # =================================
                            # DESCRIPTION VALIDATION
                            # =================================

                            if not description:
                                logging.info(
                                    f"Skipping {name} "
                                    f"because description "
                                    f"was not found"
                                )

                                continue

                            # =================================
                            # REQUIRED FIELDS
                            # =================================

                            if (
                                not name
                                or price_val is None
                                or original_val is None
                                or not image_link
                                or not product_link
                            ):

                                continue

                            # =================================
                            # FINAL CLEANING
                            # =================================

                            price_val = clean_to_int(
                                price_val
                            )

                            original_val = clean_to_int(
                                original_val
                            )

                            discount_value = (
                                discount_to_int(
                                    discount
                                )
                            )

                            rating = (
                                parse_rating_to_float(
                                    rating
                                )
                            )

                            # =================================
                            # TIMESTAMP
                            # =================================

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

                            # =================================
                            # FINAL PRODUCT
                            # =================================

                            product_data = {

                                "name": name,

                                "price": price_val,

                                "currency": "₹",

                                "original_price": original_val,

                                "discount": discount_value,

                                "ratings": rating,

                                "description": description,

                                "image_link": image_link,

                                "product_link": product_link,

                                "organization_id": (
                                    ORGANIZATION_ID
                                ),

                                "store_id": STORE_NAME,

                                "categories_id": category,

                                "timestamp": timestamp,
                            }

                            # =================================
                            # ADD RESULT
                            # =================================

                            results.append(
                                product_data
                            )

                            category_results.append(
                                product_data
                            )

                            seen_links.add(
                                product_link
                            )

                            seen_names.add(
                                name.lower()
                            )

                            logging.info(
                                f"Scraped: {name}"
                            )

                        except Exception as exc:

                            logging.error(
                                f"Error parsing "
                                f"product card "
                                f"#{index + 1} "
                                f"on page "
                                f"{page_number}: "
                                f"{exc}"
                            )

                    # =========================================
                    # NEXT PAGE
                    # =========================================

                    page_number += 1

            # =================================================
            # CATEGORY SUMMARY
            # =================================================

            category_counts[category] = (
                len(category_results)
            )

            logging.info(
                f"Total products scraped "
                f"for '{category}': "
                f"{len(category_results)}"
            )

        # ====================================================
        # CLOSE BROWSER
        # ====================================================

        await page.close()

        await context.close()

        await browser.close()

    logging.info(
        f"Total Soulflower products scraped: "
        f"{len(results)}"
    )

    return results


# ============================================================
# NAME CLEANING
# ============================================================

def normalize_product_name(name):

    if not name:
        return ""

    name = normalize_text(name)

    name = (
        name
        .replace("–", "")
        .replace("—", "")
    )

    return name.strip()


def normalize_text(text):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


# ============================================================
# WINDOWS PROCESS RUNNER
# ============================================================

def run_soulflower_scraper():

    # Configure logging inside the separate browser process so
    # Playwright progress is visible in the Scrapyd job log.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    if sys.platform.startswith("win"):

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    logging.info("=" * 60)
    logging.info("Soulflower browser process started.")
    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_soulflower_async()
        )

        logging.info(
            "Soulflower browser process finished. Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "Soulflower browser process failed."
        )

        raise


# ============================================================
# SCRAPY SPIDER
# ============================================================

class SoulFlowerSpider(scrapy.Spider):

    name = "soul_flower"

    allowed_domains = [
        "soulflower.in",
        "www.soulflower.in",
    ]

    async def start(self):

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Starting Soulflower Playwright scraper..."
        )

        self.logger.info(
            "=" * 60
        )

        # ----------------------------------------------------
        # Run Playwright in separate process
        # ----------------------------------------------------

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_soulflower_scraper
            )

        with open("scrape_soulflower.json", "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        

        self.logger.info(
            "Soulflower scraper returned "
            "%s products.",
            len(products)
        )

        # ----------------------------------------------------
        # Yield to Scrapy
        # ----------------------------------------------------

        for product in products:

            # Final validation
            if not product.get(
                "image_link"
            ):
                continue

            if not product.get(
                "product_link"
            ):
                continue

            if not product.get(
                "description"
            ):
                continue

            yield product

        self.logger.info(
            "=" * 60
        )

        self.logger.info(
            "Soulflower spider completed."
        )

        self.logger.info(
            "=" * 60
        )