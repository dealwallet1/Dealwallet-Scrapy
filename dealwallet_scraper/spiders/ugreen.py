
import asyncio
import logging
import scrapy
from concurrent.futures import ProcessPoolExecutor
import re
import json

from urllib.parse import (
    urljoin,
    urlparse,
    urldefrag,
    parse_qsl,
    urlencode,
    urlunparse,
)

from bs4 import BeautifulSoup

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    JsonCssExtractionStrategy,
)

from datetime import datetime, timezone


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def log(*values):
    logger.info(" ".join(str(value) for value in values))


BASE_URL = "https://www.ugreenindia.com"

MAX_PRODUCTS = 120


# ==========================================================
# DATABASE IDS - REPLACE WITH YOUR ACTUAL IDS
# ==========================================================

ORGANIZATION_ID = "Dealwallet"

STORE_ID = "Ugreen"


CATEGORY_IDS = {
    "Mobile Accessories": "YOUR_MOBILE_ACCESSORIES_CATEGORY_ID",
    "Electronics": "YOUR_ELECTRONICS_CATEGORY_ID",
}


COLLECTIONS = [
    {
        "url": "https://www.ugreenindia.com/collections/power_banks",
        "category": "Mobile Accessories",
    },
    {
        "url": "https://www.ugreenindia.com/collections/apple-convertors-adapters",
        "category": "Mobile Accessories",
    },
    {
        "url": "https://www.ugreenindia.com/collections/chargers",
        "category": "Mobile Accessories",
    },
    {
        "url": "https://www.ugreenindia.com/collections/networking-lan-cards",
        "category": "Electronics",
    },
]


BASE_SELECTOR = 'div[class*="product-card"]'


# ==========================================================
# LISTING SCHEMA
# ==========================================================

LISTING_SCHEMA = {
    "name": "UGREEN Power Bank Products",
    "baseSelector": 'div[class*="product-card"]',
    "fields": [
        {
            "name": "name",
            "selector": (
                'a[href*="/products/"] h2, '
                'a[href*="/products/"] h3, '
                '[class*="title"]'
            ),
            "type": "text",
        },
        {
            "name": "product_link",
            "selector": 'a[href*="/products/"]',
            "type": "attribute",
            "attribute": "href",
        },
        {
            "name": "image_link",
            "selector": "img",
            "type": "attribute",
            "attribute": "src",
        },
    ],
}


# ==========================================================
# COMMON HELPERS
# ==========================================================

def clean_text(value):

    if not value:
        return None

    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value or None


def safe_lower(value):

    if value is None:
        return ""

    return str(value).lower()


def normalize_url(url, base=BASE_URL):

    if not url:
        return None

    url = str(url).strip()

    if url.startswith("//"):
        url = "https:" + url
    else:
        url = urljoin(base, url)

    url, _ = urldefrag(url)

    return url


def is_product_url(url):

    if not url:
        return False

    parsed = urlparse(str(url))

    return (
        parsed.scheme in ("http", "https")
        and safe_lower(parsed.netloc).endswith("ugreenindia.com")
        and "/products/" in parsed.path
    )


def extract_currency_prices(text):

    if not text:
        return []

    pattern = r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)"

    matches = re.findall(
        pattern,
        str(text),
        flags=re.IGNORECASE,
    )

    prices = []

    for match in matches:

        try:
            prices.append(
                int(float(match.replace(",", "")))
            )

        except (ValueError, TypeError):
            continue

    return prices


def number_to_int(value):

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None

    match = re.search(
        r"\d[\d,]*(?:\.\d+)?",
        str(value),
    )

    if not match:
        return None

    try:

        number = int(
            float(match.group().replace(",", ""))
        )

        return number if number > 0 else None

    except (ValueError, TypeError):
        return None


def get_attribute_url(element):

    if not element:
        return None

    for attr in (
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-srcset",
        "srcset",
    ):

        value = element.get(attr)

        if not value:
            continue

        if attr.endswith("srcset"):

            parts = value.split(",")

            if not parts:
                continue

            value = parts[0].strip().split()[0]

        if value.startswith("data:image"):
            continue

        return value

    return None


# ==========================================================
# PRODUCT CARD EXTRACTION
# ==========================================================

def find_price_context(link):

    for parent in list(link.parents)[:12]:

        prices = extract_currency_prices(
            parent.get_text(" ", strip=True)
        )

        if len(prices) >= 2:
            return parent, prices[:2]

    for parent in list(link.parents)[:12]:

        prices = extract_currency_prices(
            parent.get_text(" ", strip=True)
        )

        if prices:
            return parent, prices

    return link.parent, []


def find_product_name(card, link):

    selectors = [
        "h1.product-title.h5",
        '[class*="product-title"]',
        '[class*="product__title"]',
        '[class*="card__heading"]',
        "h1",
        "h2",
        "h3",
        '[class*="title"]',
    ]

    for selector in selectors:

        element = card.select_one(selector)

        if not element:
            continue

        value = clean_text(
            element.get("title")
            or element.get_text(" ", strip=True)
        )

        if (
            value
            and len(value) > 3
            and safe_lower(value) not in (
                "view details",
                "quick view",
                "add to cart",
            )
        ):
            return value

    value = clean_text(
        link.get_text(" ", strip=True)
    )

    if (
        value
        and safe_lower(value) not in (
            "view details",
            "quick view",
            "add to cart",
        )
    ):
        return value

    image = card.select_one("img")

    if image:

        return clean_text(
            image.get("alt") or image.get("title")
        )

    return None


def extract_listing_products(html):

    soup = BeautifulSoup(
        html or "",
        "html.parser",
    )

    cards = soup.select(BASE_SELECTOR)

    log("BASE SELECTOR:", BASE_SELECTOR)
    log("BASE SELECTOR MATCHES:", len(cards))

    if not cards:
        cards = [soup]

    products = {}

    for base_card in cards:

        for link in base_card.select('a[href*="/products/"]'):

            href = link.get("href")

            if not href:
                continue

            product_url = normalize_url(href)

            if not is_product_url(product_url):
                continue

            context, prices = find_price_context(link)

            if context is None:
                context = base_card

            name = find_product_name(context, link)

            if not name:
                name = find_product_name(base_card, link)

            image_element = (
                context.select_one("img")
                or base_card.select_one("img")
            )

            image_url = get_attribute_url(image_element)

            record = {
                "name": name,
                "product_link": product_url,
                "price": prices[0] if len(prices) >= 1 else None,
                "original_price": prices[1] if len(prices) >= 2 else None,
                "image_link": (
                    normalize_url(image_url, product_url)
                    if image_url else None
                ),
            }

            existing = products.get(product_url)

            score = (
                int(record["price"] is not None)
                + int(record["original_price"] is not None)
                + int(bool(record["name"]))
                + int(bool(record["image_link"]))
            )

            if existing is None:

                products[product_url] = record

            else:

                existing_score = (
                    int(existing["price"] is not None)
                    + int(existing["original_price"] is not None)
                    + int(bool(existing["name"]))
                    + int(bool(existing["image_link"]))
                )

                if score > existing_score:
                    products[product_url] = record

    if not products:

        log("Trying fallback: all product links...")

        for link in soup.select('a[href*="/products/"]'):

            product_url = normalize_url(
                link.get("href")
            )

            if not is_product_url(product_url):
                continue

            context, prices = find_price_context(link)

            if context is None:
                context = link.parent

            products.setdefault(
                product_url,
                {
                    "name": find_product_name(context, link),
                    "product_link": product_url,
                    "price": prices[0] if len(prices) >= 1 else None,
                    "original_price": prices[1] if len(prices) >= 2 else None,
                    "image_link": None,
                },
            )

    return list(products.values())


# ==========================================================
# PRODUCT PAGE EXTRACTION
# ==========================================================

def extract_jsonld_product(soup):

    def walk(obj):

        if isinstance(obj, list):

            for item in obj:
                yield from walk(item)

        elif isinstance(obj, dict):

            types = obj.get("@type", [])

            if isinstance(types, str):
                types = [types]

            if "Product" in types:
                yield obj

            for key, value in obj.items():

                if key in (
                    "@graph",
                    "mainEntity",
                    "itemListElement",
                ):
                    yield from walk(value)

    for script in soup.select(
        'script[type="application/ld+json"]'
    ):

        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except (json.JSONDecodeError, TypeError):
            continue

        for product in walk(data):
            return product

    return {}


# ==========================================================
# PRODUCT PRICE EXTRACTION
# ==========================================================

def extract_product_prices(soup, schema_product=None):

    price = None
    original_price = None

    sale_element = soup.select_one(
        ".price__current .js-value"
    )

    regular_element = soup.select_one(
        ".price__was .js-value"
    )

    if sale_element:

        sale_prices = extract_currency_prices(
            sale_element.get_text(" ", strip=True)
        )

        if sale_prices:
            price = sale_prices[0]

    if regular_element:

        regular_prices = extract_currency_prices(
            regular_element.get_text(" ", strip=True)
        )

        if regular_prices:
            original_price = regular_prices[0]

    if schema_product:

        offers = schema_product.get("offers", [])

        if isinstance(offers, dict):
            offers = [offers]

        for offer in offers:

            if not isinstance(offer, dict):
                continue

            if price is None:

                price = number_to_int(
                    offer.get("price")
                )

            if original_price is None:

                original_price = number_to_int(
                    offer.get("highPrice")
                )

            if price is not None and original_price is not None:
                break

    if original_price is None:
        original_price = price

    return price, original_price


# ==========================================================
# DESCRIPTION EXTRACTION
# ONLY INTRO + CHIPS + KEY FEATURES
# ==========================================================

def extract_description(soup, schema_product=None):

    description_parts = []

    intro = soup.select_one("p.ugd-intro")

    if not intro:
        return None

    intro_text = clean_text(
        intro.get_text(" ", strip=True)
    )

    if intro_text:
        description_parts.append(intro_text)

    intro_container = intro.find_parent("div")

    if intro_container:

        chips = intro_container.select_one("div.ugd-chips")

        if chips:

            chips_text = clean_text(
                chips.get_text(" ", strip=True)
            )

            if chips_text:
                description_parts.append(chips_text)

    search_from = (
        intro_container
        if intro_container
        else intro
    )

    features_heading = None

    for heading in search_from.find_all_next("h2"):

        heading_text = clean_text(
            heading.get_text(" ", strip=True)
        )

        if "key features" in safe_lower(heading_text):

            features_heading = heading
            break

    if not features_heading:

        return clean_text(
            " ".join(description_parts)
        ) or None

    heading_text = clean_text(
        features_heading.get_text(" ", strip=True)
    )

    if heading_text:
        description_parts.append(heading_text)

    features_list = features_heading.find_next(
        "ul",
        class_="ugd-kf",
    )

    if features_list:

        features_text = clean_text(
            features_list.get_text(" ", strip=True)
        )

        if features_text:
            description_parts.append(features_text)

    return clean_text(
        " ".join(description_parts)
    ) or None


# ==========================================================
# PRODUCT AVAILABILITY VALIDATION
# ==========================================================

def is_unavailable_product(soup, schema_product=None):

    if schema_product:

        offers = schema_product.get("offers", [])

        if isinstance(offers, dict):
            offers = [offers]

        for offer in offers:

            if not isinstance(offer, dict):
                continue

            availability = safe_lower(
                offer.get("availability")
            )

            if any(status in availability for status in (
                "outofstock",
                "soldout",
                "discontinued",
                "out_of_stock",
            )):
                return True, "SOLD OUT / OUT OF STOCK"

    availability_selectors = [
        '[class*="sold-out"]',
        '[class*="soldout"]',
        '[class*="out-of-stock"]',
        '[class*="unavailable"]',
        '[class*="availability"]',
        '[class*="inventory"]',
        '[class*="stock-status"]',
        '[class*="product-form"]',
        '[class*="product__info"]',
        '[class*="product-info"]',
        'form[action*="/cart/add"]',
        '[data-availability]',
    ]

    status_parts = []

    for selector in availability_selectors:

        for element in soup.select(selector):

            text = clean_text(
                element.get_text(" ", strip=True)
            )

            if text:
                status_parts.append(text)

            for attr in (
                "aria-label",
                "title",
                "data-availability",
                "data-stock",
            ):

                value = element.get(attr)

                if value:
                    status_parts.append(str(value))

    status_text = safe_lower(
        " ".join(status_parts)
    )

    unavailable_patterns = [
        (r"\bsold\s*out\b", "SOLD OUT"),
        (r"\bout\s*of\s*stock\b", "OUT OF STOCK"),
        (r"\bcurrently\s*unavailable\b", "UNAVAILABLE"),
        (r"\btemporarily\s*unavailable\b", "UNAVAILABLE"),
        (r"\bnot\s*available\b", "UNAVAILABLE"),
        (r"\bunavailable\b", "UNAVAILABLE"),
        (r"\bout\s*of\s*delivery\b", "OUT OF DELIVERY"),
        (r"\bnot\s*deliverable\b", "NOT DELIVERABLE"),
        (r"\bdelivery\s*not\s*available\b", "DELIVERY NOT AVAILABLE"),
        (r"\bnot\s*available\s*for\s*delivery\b", "NOT AVAILABLE FOR DELIVERY"),
        (r"\bdelivery\s*unavailable\b", "DELIVERY UNAVAILABLE"),
    ]

    for pattern, reason in unavailable_patterns:

        if re.search(pattern, status_text):
            return True, reason

    return False, None


# ==========================================================
# PRODUCT IMAGE EXTRACTION
# ==========================================================

def extract_product_image(soup, schema_product=None):

    meta = soup.select_one(
        'meta[property="og:image"]'
    )

    if meta and meta.get("content"):
        return meta["content"]

    if schema_product:

        image = schema_product.get("image")

        if isinstance(image, str):
            return image

        if isinstance(image, list) and image:

            first = image[0]

            if isinstance(first, str):
                return first

            if isinstance(first, dict):
                return first.get("url")

        if isinstance(image, dict):
            return image.get("url")

    selectors = [
        '[class*="product__media"] img',
        '[class*="product-media"] img',
        '[class*="product-image"] img',
        'img[itemprop="image"]',
    ]

    for selector in selectors:

        element = soup.select_one(selector)

        image_url = get_attribute_url(element)

        if image_url:
            return image_url

    return None


# ==========================================================
# PRODUCT RATING EXTRACTION
# ==========================================================

def extract_rating(soup):

    rating_element = soup.select_one(
        "div.jdgm-prev-badge .jdgm-prev-badge__stars"
    )

    if not rating_element:
        return None

    filled_stars = rating_element.select(
        "span.jdgm-star.jdgm--on"
    )

    return len(filled_stars) if filled_stars else None


# ==========================================================
# MANDATORY FIELD VALIDATION
# ==========================================================

def validate_product(
    name,
    price,
    original_price,
    description,
    image_link,
    product_link,
):

    if not name:
        return False, "Product name missing"

    if price is None or price <= 0:
        return False, "Selling price missing or invalid"

    if original_price is None or original_price <= 0:
        return False, "Original price missing or invalid"

    if original_price < price:
        return False, "Original price lower than selling price"

    if not description:
        return False, "Product description missing"

    if not image_link:
        return False, "Product image link missing"

    if not product_link:
        return False, "Product link missing"

    return True, None


# ==========================================================
# COLLECTION PAGINATION
# ==========================================================

def collection_page_url(base_url, page_number):

    parsed = urlparse(base_url)

    query = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    if page_number <= 1:
        query.pop("page", None)

    else:
        query["page"] = str(page_number)

    return urlunparse(
        parsed._replace(
            query=urlencode(query)
        )
    )


# ==========================================================
# MAIN SCRAPER
# ==========================================================

async def ugreen_store():

    browser_config = BrowserConfig(
        headless=True,
        verbose=False,
    )

    show_more_js = """
    (async () => {
        for (let i = 0; i < 5; i++) {

            const button = Array.from(
                document.querySelectorAll('button, a, [role="button"]')
            ).find(el => /show more|load more/i.test(
                (el.innerText || el.textContent || '').trim()
            ));

            if (!button) break;

            button.click();

            await new Promise(resolve => setTimeout(resolve, 1200));
        }

        window.scrollTo(0, document.body.scrollHeight);

        await new Promise(resolve => setTimeout(resolve, 800));
    })()
    """

    listing_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=120000,
        delay_before_return_html=3,
        word_count_threshold=0,
        js_code=show_more_js,
        extraction_strategy=JsonCssExtractionStrategy(
            LISTING_SCHEMA
        ),
    )

    product_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=120000,
        delay_before_return_html=3,
        word_count_threshold=0,
    )

    max_listing_pages = 10
    max_candidates = MAX_PRODUCTS * 3

    candidates_by_url = {}

    collection_states = [
        {
            "url": collection["url"],
            "category": collection["category"],
            "page": 1,
            "finished": False,
        }
        for collection in COLLECTIONS
    ]

    results = []

    async with AsyncWebCrawler(
        config=browser_config
    ) as crawler:

        log("\n========== UGREEN COLLECTION DISCOVERY ==========")

        log("Collections:", len(COLLECTIONS))

        log(
            "Target valid products across all collections:",
            MAX_PRODUCTS,
        )

        # COLLECTION DISCOVERY

        while (
            len(candidates_by_url) < max_candidates
            and any(
                not state["finished"]
                for state in collection_states
            )
        ):

            for state in collection_states:

                if state["finished"]:
                    continue

                page_number = state["page"]

                if page_number > max_listing_pages:

                    state["finished"] = True
                    continue

                page_url = collection_page_url(
                    state["url"],
                    page_number,
                )

                category = state["category"]

                log(
                    f"\nCollection: {category} | Page: {page_number}"
                )

                log("URL:", page_url)

                try:

                    listing_result = await crawler.arun(
                        url=page_url,
                        config=listing_config,
                    )

                except Exception as exc:

                    log(
                        "Collection crawl error:",
                        repr(exc),
                    )

                    state["finished"] = True
                    continue

                if not listing_result.success:

                    log(
                        "Collection crawl failed:",
                        listing_result.error_message,
                    )

                    state["finished"] = True
                    continue

                html = listing_result.html or ""

                log(
                    "Listing HTML length:",
                    len(html),
                )

                if not html:

                    log(
                        "No listing HTML returned; ending this collection."
                    )

                    state["finished"] = True
                    continue

                page_products = extract_listing_products(
                    html
                )

                log(
                    "Product candidates on this page:",
                    len(page_products),
                )

                added_this_page = 0

                for item in page_products:

                    product_url = item.get("product_link")

                    if not product_url:
                        continue

                    parsed_product = urlparse(product_url)

                    product_url = urlunparse(
                        parsed_product._replace(
                            query="",
                            fragment="",
                        )
                    )

                    item["product_link"] = product_url

                    if product_url in candidates_by_url:
                        continue

                    item["category"] = category

                    candidates_by_url[product_url] = item

                    added_this_page += 1

                    if len(candidates_by_url) >= max_candidates:
                        break

                log(
                    "New unique candidates added:",
                    added_this_page,
                )

                log(
                    "Total unique candidates:",
                    len(candidates_by_url),
                )

                if added_this_page == 0 or not page_products:

                    state["finished"] = True

                else:

                    state["page"] += 1

                if len(candidates_by_url) >= max_candidates:
                    break

        listing_products = list(
            candidates_by_url.values()
        )

        log(
            "\nTotal unique candidates across collections:",
            len(listing_products),
        )

        # PRODUCT PAGE SCRAPING

        for index, listing in enumerate(
            listing_products,
            start=1,
        ):

            if len(results) >= MAX_PRODUCTS:
                break

            product_url = listing["product_link"]

            category = listing.get(
                "category",
                "UGREEN Products",
            )

            category_id = CATEGORY_IDS.get(category)

            log(
                f"\n[Candidate {index}/{len(listing_products)}] {product_url}"
            )

            try:

                product_result = await crawler.arun(
                    url=product_url,
                    config=product_config,
                )

                product_html = product_result.html or ""

                if not product_result.success or not product_html:

                    log(
                        "Skipped: Product page crawl failed"
                    )

                    continue

                product_soup = BeautifulSoup(
                    product_html,
                    "html.parser",
                )

                schema_product = extract_jsonld_product(
                    product_soup
                )

                # SOLD OUT / OUT OF DELIVERY VALIDATION

                unavailable, reason = is_unavailable_product(
                    product_soup,
                    schema_product,
                )

                if unavailable:

                    log(
                        "Skipped:",
                        reason,
                    )

                    continue

                # PRODUCT NAME

                name_element = product_soup.select_one(
                    "h1.product-title.h5"
                )

                if not name_element:
                    name_element = product_soup.select_one("h1")

                name = (
                    clean_text(
                        name_element.get_text(" ", strip=True)
                    )
                    if name_element
                    else None
                )

                name = (
                    name
                    or clean_text(schema_product.get("name"))
                    or clean_text(listing.get("name"))
                )

                # PRODUCT PRICE

                page_price, page_original_price = extract_product_prices(
                    product_soup,
                    schema_product,
                )

                price = number_to_int(
                    page_price if page_price is not None
                    else listing.get("price")
                )

                original_price = number_to_int(
                    page_original_price
                    if page_original_price is not None
                    else listing.get("original_price")
                )

                if original_price is None:
                    original_price = price

                # PRODUCT DESCRIPTION

                description = extract_description(
                    product_soup,
                    schema_product,
                )

                # PRODUCT IMAGE

                image_url = extract_product_image(
                    product_soup,
                    schema_product,
                )

                image_url = (
                    image_url
                    or listing.get("image_link")
                )

                if image_url:

                    image_url = normalize_url(
                        image_url,
                        product_url,
                    )

                # MANDATORY FIELD VALIDATION

                valid, validation_reason = validate_product(
                    name,
                    price,
                    original_price,
                    description,
                    image_url,
                    product_url,
                )

                if not valid:

                    log(
                        "Skipped:",
                        validation_reason,
                    )

                    continue

                # DISCOUNT

                discount = (
                    round(
                        (
                            (original_price - price)
                            / original_price
                        ) * 100
                    )
                    if original_price > 0
                    else 0
                )

                # SKIP PRODUCTS WITH ZERO OR NEGATIVE DISCOUNT

                if discount <= 0:

                    log(
                        "Skipping product with zero discount:",
                        name,
                    )

                    continue

                # RATINGS

                ratings = extract_rating(
                    product_soup
                )

                # FINAL PRODUCT RECORD

                record = {
                    "name": name,
                    "price": price,
                    "currency": "₹",
                    "original_price": original_price,
                    "discount": int(discount),
                    "ratings": ratings,
                    "description": description,
                    "image_link": image_url,
                    "product_link": product_url,
                    "organization_id": "Dealwallet",
                    "store_id": "Ugreen",
                    "categories_id": category,
                    "created_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                results.append(record)

                log("Name:", name)
                log("Price:", price)
                log("Original price:", original_price)
                log("Category ID:", category_id)
                log("Description:", description[:150])
                log("Image link:", image_url)
                log("Product link:", product_url)
                log("Rating:", ratings)

                log(
                    "Saved products:",
                    len(results),
                    "/",
                    MAX_PRODUCTS,
                )

            except Exception as exc:

                log(
                    "Product error:",
                    repr(exc),
                )

    # ======================================================
    # SAVE JSON
    # ======================================================

    with open(
        "ugreen_products.json",
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            results,
            file,
            ensure_ascii=False,
            indent=4,
        )

    log("========== SCRAPING COMPLETED ==========")
    log("Products saved:", len(results), "/", MAX_PRODUCTS)

    if len(results) < MAX_PRODUCTS:

        log(
            "Only the valid products found were saved. "
            "More products may be needed if pages are unavailable "
            "or mandatory fields are missing."
        )

    log("JSON: ugreen_products.json")

    return results


# ==========================================================
# PROCESS RUNNER
# ==========================================================

def run_ugreen_scraper():
    """
    Synchronous wrapper used by ProcessPoolExecutor.
    """

    try:
        return asyncio.run(ugreen_store())

    except Exception:
        logger.exception("UGREEN scraper process failed")
        return []


# ==========================================================
# SCRAPY SPIDER
# ==========================================================

class UgreenSpider(scrapy.Spider):

    name = "ugreen"

    allowed_domains = [
        "ugreenindia.com",
    ]

    custom_settings = {
        "CONCURRENT_REQUESTS": 1,
        "DOWNLOAD_DELAY": 1,
    }

    async def start(self):

        self.logger.info("Starting UGREEN scraper...")
        self.logger.info("Running Crawl4AI in separate process...")

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:

            try:

                products = await loop.run_in_executor(
                    executor,
                    run_ugreen_scraper,
                )

            except Exception:

                self.logger.exception("UGREEN scraper failed")
                return

        if not products:

            self.logger.warning("No products returned from UGREEN scraper.")
            return

        self.logger.info(
            "UGREEN scraper completed. Products returned: %d",
            len(products),
        )

        for product in products:

            try:

                if not product.get("name"):
                    continue

                if not product.get("product_link"):
                    continue

                if not product.get("description"):
                    continue

                if not product.get("image_link"):
                    continue

                yield product

            except Exception:

                self.logger.exception("Error yielding UGREEN product")

        self.logger.info("UGREEN spider completed.")
