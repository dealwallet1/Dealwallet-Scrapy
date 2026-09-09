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
        "https://www.soulflower.in/collections/hair-oils",
        "https://www.soulflower.in/collections/hair-serum",
        "https://www.soulflower.in/collections/shampoos",
        "https://www.soulflower.in/collections/face-wash",
        "https://www.soulflower.in/collections/face-masks",
        "https://www.soulflower.in/collections/serums",
        "https://www.soulflower.in/collections/sunscreen",
    ]
}


# ============================================================
# REQUEST / RATE LIMIT SETTINGS
# ============================================================

# Delay between listing page requests.
LISTING_PAGE_DELAY = 5

# Delay before opening product detail pages.
DETAIL_PAGE_DELAY = 3

# Additional delay after receiving HTTP 429.
RATE_LIMIT_DELAY = 30

# Number of times to retry a 429 page.
MAX_429_RETRIES = 3

# Maximum page load timeout.
PAGE_TIMEOUT = 60000

# Product selector wait timeout.
PRODUCT_SELECTOR_TIMEOUT = 30000


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


def normalize_text(text):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


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

    # ========================================================
    # METHOD 1 - PRODUCT ACCORDIONS
    # ========================================================

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

        title = title_raw.lower().strip()

        if title in skip_titles:
            continue

        # Remove unnecessary HTML elements.
        for tag in content_tag.select(
            "svg, img, button, script, style"
        ):
            tag.decompose()

        # ====================================================
        # WHAT DOES IT DO
        # ====================================================

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

                desc = normalize_text(desc)

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

        # ====================================================
        # NORMAL CONTENT
        # ====================================================

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
            # Fallback to complete text content.
            fallback_text = content_tag.get_text(
                " ",
                strip=True
            )

            fallback_text = normalize_text(
                fallback_text
            )

            if fallback_text:
                texts.append(
                    fallback_text
                )

        if not texts:
            continue

        clean_text = " ".join(texts)

        clean_text = normalize_text(
            clean_text
        )

        # ====================================================
        # STOP INFORMATION
        # ====================================================

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

        # ====================================================
        # REMOVE DUPLICATE CONSECUTIVE WORDS
        # ====================================================

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

    # ========================================================
    # METHOD 2 - COMMON PRODUCT DESCRIPTION CONTAINERS
    # ========================================================

    if not sections:

        description_selectors = [
            ".product__description",
            ".product-description",
            ".product__info-description",
            ".product-description-wrapper",
            ".product-single__description",
            "[class*='product-description']",
        ]

        for selector in description_selectors:

            description_tag = product_soup.select_one(
                selector
            )

            if not description_tag:
                continue

            for tag in description_tag.select(
                "svg, img, button, script, style"
            ):
                tag.decompose()

            text = description_tag.get_text(
                " ",
                strip=True
            )

            text = normalize_text(text)

            if text:
                sections.append(
                    f"Description: {text}"
                )

                break

    # ========================================================
    # METHOD 3 - META DESCRIPTION
    # ========================================================

    if not sections:

        meta_description = product_soup.select_one(
            'meta[name="description"]'
        )

        if meta_description:

            content = meta_description.get(
                "content"
            )

            content = normalize_text(
                content
            )

            if content:
                sections.append(
                    f"Description: {content}"
                )

    # ========================================================
    # METHOD 4 - OG DESCRIPTION
    # ========================================================

    if not sections:

        og_description = product_soup.select_one(
            'meta[property="og:description"]'
        )

        if og_description:

            content = og_description.get(
                "content"
            )

            content = normalize_text(
                content
            )

            if content:
                sections.append(
                    f"Description: {content}"
                )

    # ========================================================
    # METHOD 5 - JSON-LD DESCRIPTION
    # ========================================================

    if not sections:

        scripts = product_soup.select(
            'script[type="application/ld+json"]'
        )

        for script in scripts:

            raw_json = script.string

            if not raw_json:
                continue

            try:
                data = json.loads(raw_json)
            except Exception:
                continue

            json_objects = []

            if isinstance(data, dict):
                json_objects.append(data)

                if isinstance(
                    data.get("@graph"),
                    list
                ):
                    json_objects.extend(
                        data["@graph"]
                    )

            elif isinstance(data, list):
                json_objects.extend(data)

            for item in json_objects:

                if not isinstance(item, dict):
                    continue

                description = item.get(
                    "description"
                )

                if not description:
                    continue

                description = normalize_text(
                    description
                )

                if description:
                    sections.append(
                        f"Description: {description}"
                    )

                    break

            if sections:
                break

    # ========================================================
    # FINAL RESULT
    # ========================================================

    if not sections:
        return None

    description = " || ".join(sections)

    # Remove an unwanted leading "Description:" label that
    # appears in some Soulflower descriptions.
    description = re.sub(
        r"^\s*description\s*:\s*",
        "",
        description,
        count=1,
        flags=re.IGNORECASE,
    )

    return description.strip()


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
        "data-lazy-src",
        "data-original",
        "data-srcset",
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

        # srcset can contain multiple URLs.
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
# PRODUCT NAME EXTRACTION
# ============================================================

async def extract_product_name(card):

    selectors = [
        "a.full-unstyled-link",
        "a.product-title",
        "h2 a",
        "h3 a",
        ".card__heading a",
        ".card__heading",
        ".product-card__title",
        ".product-title",
        "[class*='product-title']",
        "[class*='card__heading']",
    ]

    for selector in selectors:

        locator = card.locator(
            selector
        )

        if await locator.count() == 0:
            continue

        try:

            text = await locator.first.inner_text()

            name = normalize_product_name(
                text
            )

            if name:
                return name

        except Exception:
            continue

    # ========================================================
    # FALLBACK - LINK TITLE ATTRIBUTE
    # ========================================================

    links = card.locator("a")

    link_count = await links.count()

    for index in range(link_count):

        try:

            link = links.nth(index)

            title = await link.get_attribute(
                "title"
            )

            if title:

                title = normalize_product_name(
                    title
                )

                if title:
                    return title

        except Exception:
            continue

    # ========================================================
    # FALLBACK - IMAGE ALT
    # ========================================================

    images = card.locator("img")

    image_count = await images.count()

    for index in range(image_count):

        try:

            image = images.nth(index)

            alt = await image.get_attribute(
                "alt"
            )

            if alt:

                alt = normalize_product_name(
                    alt
                )

                if alt:
                    return alt

        except Exception:
            continue

    return None


# ============================================================
# PRODUCT LINK EXTRACTION
# ============================================================

async def extract_product_link(card):

    selectors = [
        "a.full-unstyled-link",
        "a.product-title",
        "h2 a",
        "h3 a",
        "a[href*='/products/']",
    ]

    for selector in selectors:

        locator = card.locator(
            selector
        )

        if await locator.count() == 0:
            continue

        try:

            href = await locator.first.get_attribute(
                "href"
            )

            if href:

                return urljoin(
                    BASE_URL,
                    href
                )

        except Exception:
            continue

    # Generic anchor fallback.
    links = card.locator("a")

    link_count = await links.count()

    for index in range(link_count):

        try:

            href = await links.nth(index).get_attribute(
                "href"
            )

            if not href:
                continue

            if "/products/" in href:

                return urljoin(
                    BASE_URL,
                    href
                )

        except Exception:
            continue

    return None


# ============================================================
# PRICE EXTRACTION
# ============================================================

async def get_first_text(card, selectors):

    for selector in selectors:

        locator = card.locator(
            selector
        )

        if await locator.count() == 0:
            continue

        try:

            text = await locator.first.inner_text()

            if text and text.strip():
                return text.strip()

        except Exception:
            continue

    return ""


# ============================================================
# OPEN LISTING PAGE WITH 429 RETRY
# ============================================================

async def open_listing_page(
    page,
    url,
):
    """
    Open listing page.

    If Soulflower returns HTTP 429,
    wait and retry before giving up.
    """

    for attempt in range(
        1,
        MAX_429_RETRIES + 1
    ):

        try:

            logging.info(
                f"Opening listing page "
                f"(attempt {attempt}/"
                f"{MAX_429_RETRIES}): {url}"
            )

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            if response:

                status = response.status

                logging.info(
                    f"HTTP status: {status}"
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        logging.warning(
                            f"HTTP 429 received for "
                            f"{url}. Waiting "
                            f"{RATE_LIMIT_DELAY} seconds "
                            f"before retry."
                        )

                        await page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    logging.error(
                        f"HTTP 429 received after "
                        f"{MAX_429_RETRIES} attempts: "
                        f"{url}"
                    )

                    return None

            return response

        except Exception as exc:

            logging.warning(
                f"Failed to open listing page "
                f"{url}: {exc}"
            )

            if attempt < MAX_429_RETRIES:

                await page.wait_for_timeout(
                    DETAIL_PAGE_DELAY * 1000
                )

            else:

                logging.error(
                    f"Giving up listing page: "
                    f"{url}"
                )

    return None


# ============================================================
# OPEN PRODUCT DETAIL PAGE WITH 429 RETRY
# ============================================================

async def open_product_page(
    product_page,
    product_link,
):

    for attempt in range(
        1,
        MAX_429_RETRIES + 1
    ):

        try:

            response = await product_page.goto(
                product_link,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )

            if response:

                status = response.status

                logging.info(
                    f"Product page HTTP status: "
                    f"{status}"
                )

                if status == 429:

                    if attempt < MAX_429_RETRIES:

                        logging.warning(
                            f"HTTP 429 on product page. "
                            f"Waiting "
                            f"{RATE_LIMIT_DELAY} seconds "
                            f"before retry: "
                            f"{product_link}"
                        )

                        await product_page.wait_for_timeout(
                            RATE_LIMIT_DELAY * 1000
                        )

                        continue

                    logging.error(
                        f"Product page still returned "
                        f"429 after retries: "
                        f"{product_link}"
                    )

                    return None

            return response

        except Exception as exc:

            logging.warning(
                f"Failed product page "
                f"{product_link}: {exc}"
            )

            if attempt < MAX_429_RETRIES:

                await product_page.wait_for_timeout(
                    DETAIL_PAGE_DELAY * 1000
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
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = await context.new_page()

        page.set_default_timeout(
            PAGE_TIMEOUT
        )

        page.set_default_navigation_timeout(
            PAGE_TIMEOUT
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

                    logging.info("=" * 60)

                    logging.info(
                        f"Fetching: {url}"
                    )

                    # =================================================
                    # DELAY BETWEEN REQUESTS
                    # =================================================

                    if page_number > 1:
                        logging.info(
                            f"Waiting "
                            f"{LISTING_PAGE_DELAY} seconds "
                            f"before next page request."
                        )

                        await page.wait_for_timeout(
                            LISTING_PAGE_DELAY * 1000
                        )

                    # =================================================
                    # OPEN LISTING PAGE
                    # =================================================

                    response = await open_listing_page(
                        page,
                        url,
                    )

                    if response is None:
                        logging.warning(
                            f"Unable to fetch listing page: "
                            f"{url}"
                        )

                        break

                    # =================================================
                    # WAIT FOR PRODUCT CARDS
                    # =================================================

                    try:

                        await page.wait_for_selector(
                            "li.grid__item",
                            timeout=PRODUCT_SELECTOR_TIMEOUT,
                        )

                    except Exception:

                        logging.warning(
                            f"Product selector "
                            f"not found on page "
                            f"{page_number}: {url}"
                        )

                        break

                    # =================================================
                    # PRODUCT CARDS
                    # =================================================

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

                    # =================================================
                    # PRODUCT LOOP
                    # =================================================

                    for index in range(
                        card_count
                    ):

                        try:

                            card = product_cards.nth(
                                index
                            )

                            # =========================================
                            # PRODUCT NAME
                            # =========================================

                            name = await extract_product_name(
                                card
                            )

                            if not name:

                                logging.warning(
                                    f"Product name "
                                    f"not found for "
                                    f"card #{index + 1}"
                                )

                                continue

                            # =========================================
                            # PRODUCT LINK
                            # =========================================

                            product_link = (
                                await extract_product_link(
                                    card
                                )
                            )

                            if not product_link:

                                logging.warning(
                                    f"Product link "
                                    f"not found for "
                                    f"{name}"
                                )

                                continue

                            # =========================================
                            # DUPLICATE CHECK
                            # =========================================

                            if product_link in seen_links:
                                continue

                            if name.lower() in seen_names:
                                continue

                            # =========================================
                            # ORIGINAL PRICE
                            # =========================================

                            original_price_text = await get_first_text(
                                card,
                                [
                                    "s.price-item.price-item--regular",
                                    "span.price--compare-at",
                                    "span.original-price",
                                    ".price__sale s",
                                    "s",
                                ],
                            )

                            original_price_text = (
                                original_price_text
                                .replace("₹", "")
                                .replace(",", "")
                                .replace("Rs. ", "")
                                .strip()
                            )

                            # =========================================
                            # SALE PRICE
                            # =========================================

                            sale_price_text = await get_first_text(
                                card,
                                [
                                    "span.price-item.price-item--sale",
                                    "span.price-item.price-item--regular",
                                    "span.price",
                                    "span.current-price",
                                    ".price__sale .price-item",
                                ],
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

                            if not sale_price_text:
                                sale_price_text = "N/A"

                            # =========================================
                            # PRICE VALUES
                            # =========================================

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

                            # =========================================
                            # PRICE VALIDATION
                            # =========================================

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

                            # =========================================
                            # DISCOUNT
                            # =========================================

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

                            # =========================================
                            # ONLY DISCOUNTED PRODUCTS
                            # =========================================

                            if discount is None:

                                logging.info(
                                    f"Skipping {name} "
                                    f"because discount "
                                    f"was not detected."
                                )

                                continue

                            # =========================================
                            # IMAGE
                            # =========================================

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

                            # =========================================
                            # RATING
                            # =========================================

                            rating = None

                            rating_locator = card.locator(
                                "p.rating-text"
                            )

                            if (
                                await rating_locator.count()
                                > 0
                            ):

                                try:

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

                                except Exception:
                                    rating = None

                            # =========================================
                            # WAIT BEFORE DETAIL PAGE
                            # =========================================

                            logging.info(
                                f"Waiting "
                                f"{DETAIL_PAGE_DELAY} seconds "
                                f"before opening product detail: "
                                f"{name}"
                            )

                            await page.wait_for_timeout(
                                DETAIL_PAGE_DELAY * 1000
                            )

                            # =========================================
                            # PRODUCT DETAIL PAGE
                            # =========================================

                            description = None

                            product_page = (
                                await context.new_page()
                            )

                            product_page.set_default_timeout(
                                PAGE_TIMEOUT
                            )

                            product_page.set_default_navigation_timeout(
                                PAGE_TIMEOUT
                            )

                            try:

                                detail_response = (
                                    await open_product_page(
                                        product_page,
                                        product_link,
                                    )
                                )

                                if detail_response:

                                    # Give Shopify page time
                                    # to populate dynamic content.
                                    await product_page.wait_for_timeout(
                                        2500
                                    )

                                    product_html = (
                                        await product_page.content()
                                    )

                                    description = (
                                        extract_product_description(
                                            product_html
                                        )
                                    )

                                    if description:

                                        logging.info(
                                            f"Description extracted "
                                            f"for: {name}"
                                        )

                                    else:

                                        logging.warning(
                                            f"Description not found "
                                            f"for: {name}"
                                        )

                            except Exception as exc:

                                logging.warning(
                                    f"Failed product page "
                                    f"{product_link}: "
                                    f"{exc}"
                                )

                            finally:

                                await product_page.close()

                            # =========================================
                            # DESCRIPTION
                            # =========================================
                            #
                            # IMPORTANT:
                            # Do NOT skip the product if description
                            # is missing.
                            #
                            # Database allows description to be NULL.
                            #
                            # =========================================

                            if not description:

                                logging.info(
                                    f"Continuing {name} "
                                    f"without description."
                                )

                                description = None

                            # =========================================
                            # REQUIRED FIELDS
                            # =========================================

                            if (
                                not name
                                or price_val is None
                                or original_val is None
                                or not image_link
                                or not product_link
                            ):

                                logging.info(
                                    f"Skipping incomplete "
                                    f"product: {name}"
                                )

                                continue

                            # =========================================
                            # FINAL CLEANING
                            # =========================================

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
                            # FINAL PRODUCT
                            # =========================================

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

                                "organization_id": "Dealwallet",

                                "store_id": "Soulflower",

                                "categories_id": category,

                                "created_at": timestamp,
                            }

                            # =========================================
                            # ADD RESULT
                            # =========================================

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

                    # =============================================
                    # NEXT PAGE
                    # =============================================

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
# WINDOWS PROCESS RUNNER
# ============================================================

def run_soulflower_scraper():

    # Configure logging inside the separate browser
    # process so Playwright progress is visible
    # in the Scrapyd job log.

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

    logging.info(
        "Soulflower browser process started."
    )

    logging.info("=" * 60)

    try:

        results = asyncio.run(
            scrape_soulflower_async()
        )

        logging.info(
            "Soulflower browser process finished. "
            "Products returned: %s",
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

        # ----------------------------------------------------
        # SAVE RAW SCRAPED DATA
        # ----------------------------------------------------

        with open(
            "scrape_soulflower.json",
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                products,
                f,
                ensure_ascii=False,
                indent=2
            )

        self.logger.info(
            "Soulflower scraper returned "
            "%s products.",
            len(products)
        )

        # ----------------------------------------------------
        # YIELD TO SCRAPY
        # ----------------------------------------------------

        for product in products:

            # Final validation.
            #
            # Description is intentionally NOT required.
            # It can be NULL in the database.

            if not product.get(
                "image_link"
            ):
                continue

            if not product.get(
                "product_link"
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