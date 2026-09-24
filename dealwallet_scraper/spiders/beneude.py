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
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode




BASE_URL = "https://www.beneude.com"


COLLECTION_URLS = [
    "https://www.beneude.com/collections/shop-all",
]


TOTAL_PRODUCTS = 100

PAGE_TIMEOUT = 180000

COLLECTION_RENDER_WAIT = 10
CLOUDFLARE_WAIT = 120
PRODUCT_DELAY = 3

OUTPUT_JSON = "beneude_all_collections.json"

ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Beneude"
CATEGORY_ID = "Beauty"
CURRENCY = "₹"




PRODUCT_CARD_SELECTORS = [
    "li.grid__item",
    "li.grid__item .card-wrapper",
    "li[class*='grid__item']",
]

NAME_SELECTORS = [
    ".card__heading.new-font a",
    ".card__heading a",
    ".card__heading",
    "h3.card__heading a",
    "h3.card__heading",
]

LINK_SELECTOR = 'a[href*="/products/"]'

PRICE_SELECTORS = [
    ".price-item.price-item--sale",
    ".price-item--sale",
    ".price-item.price-item--regular",
    ".price-item--regular",
]

ORIGINAL_PRICE_SELECTORS = [
    ".striked-price s",
    ".striked-price .price-item--regular",
    "s.price-item--regular",
]

DISCOUNT_SELECTORS = [
    ".pdp-discount-val",
]

RATING_SELECTORS = [
    ".rating-text span",
    ".rating[aria-label]",
    ".rating",
]

IMAGE_SELECTORS = [
    ".card__media img",
    "img",
]




def clean_text(value):
    if value is None:
        return None

    text = BeautifulSoup(
        str(value),
        "html.parser",
    ).get_text(" ", strip=True)

    text = re.sub(r"\s+", " ", text).strip()

    return text or None


def absolute_url(value):
    if not value:
        return None

    value = str(value).strip()

    if value.startswith("//"):
        return "https:" + value

    return urljoin(BASE_URL, value)


def parse_price(value):
    if not value:
        return None

    value = clean_text(value)

    if not value:
        return None

    value = value.replace(",", "")

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        value,
    )

    if not match:
        return None

    number = float(match.group(1))

    if number <= 0:
        return None

    return int(number) if number.is_integer() else number


def parse_discount(value):
    if not value:
        return None

    value = clean_text(value)

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:off)?",
        value,
        re.I,
    )

    if not match:
        return None

    number = float(match.group(1))

    if number <= 0 or number > 100:
        return None

    return int(number) if number.is_integer() else number


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    try:
        price = float(price)
        original_price = float(original_price)

        if original_price <= price:
            return None

        value = (
            (original_price - price)
            / original_price
        ) * 100

        return round(value)

    except Exception:
        return None


def normalize_rating(value):
    if not value:
        return None

    value = clean_text(value)

    match = re.search(
        r"([0-5](?:\.\d+)?)",
        value,
    )

    if not match:
        return None

    rating = float(match.group(1))

    if rating <= 0 or rating > 5:
        return None

    if rating == 0.00:
        return None

    return round(rating, 1)


def get_first_text(node, selectors):
    for selector in selectors:
        found = node.select_one(selector)

        if found:
            text = clean_text(
                found.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                return text

    return None


def get_first_node(node, selectors):
    for selector in selectors:
        found = node.select_one(selector)

        if found:
            return found

    return None


def get_image(node):
    image_node = get_first_node(
        node,
        IMAGE_SELECTORS,
    )

    if not image_node:
        return None

    value = (
        image_node.get("srcset")
        or image_node.get("data-srcset")
        or image_node.get("data-src")
        or image_node.get("src")
    )

    if not value:
        return None

    value = (
        value.split(",")[0]
        .strip()
        .split(" ")[0]
    )

    return absolute_url(value)


def looks_like_cloudflare(html):
    if not html:
        return False

    text = html.lower()

    indicators = [
        "just a moment",
        "checking your browser",
        "verify you are human",
        "challenge-platform",
        "cf-chl-",
        "attention required",
        "enable javascript and cookies",
    ]

    return any(
        item in text
        for item in indicators
    )


def mandatory_fields_present(product):
    required = [
        "price",
        "original_price",
        "discount",
        "image_link",
        "product_link",
        "description",
    ]

    for field in required:
        value = product.get(field)

        if value is None:
            return False

        if isinstance(value, str) and not value.strip():
            return False

    return True




def browser_config():
    return BrowserConfig(
        headless=True,
        verbose=False,
    )


def crawl_config():
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded",
    )




def extract_description(soup):

   
    first_description = soup.select_one(
        'div[data-id="prt-1"].bmtabcontent '
        '.truncatedescription p'
    )

    if first_description:
        text = clean_text(
            first_description.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            return text

  
    selectors = [
        ".product__description",
        ".product-description",
        ".product-description-wrapper",
        ".rte",
        ".product__info-container .rte",
        "[class*='description']",
    ]

    for selector in selectors:
        nodes = soup.select(selector)

        for node in nodes:
            text = clean_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if text and len(text) >= 20:
                return text


    for script in soup.select(
        'script[type="application/ld+json"]'
    ):
        try:
            raw = (
                script.string
                or script.get_text()
            )

            if not raw:
                continue

            data = json.loads(raw)

            objects = (
                data
                if isinstance(data, list)
                else [data]
            )

            for obj in objects:
                if not isinstance(obj, dict):
                    continue

                if obj.get("@type") == "Product":
                    description = clean_text(
                        obj.get("description")
                    )

                    if description:
                        return description

        except Exception:
            pass


    for selector in [
        'meta[property="og:description"]',
        'meta[name="description"]',
    ]:
        node = soup.select_one(selector)

        if node:
            text = clean_text(
                node.get("content")
            )

            if text:
                return text

    return None




def extract_rating(soup):

   
    rating_node = soup.select_one(
        ".jdgm-prev-badge[data-average-rating]"
    )

    if rating_node:
        value = rating_node.get(
            "data-average-rating"
        )

        if value:
            try:
                rating = float(value.strip())

                if rating <= 0 or rating > 5:
                    return None

                if rating == 0.00:
                    return None

                return rating

            except (ValueError, TypeError):
                pass

  
    for selector in [
        ".rating[aria-label]",
        ".rating-text span",
        ".rating",
    ]:
        node = soup.select_one(selector)

        if not node:
            continue

        value = (
            node.get("aria-label")
            or node.get_text(
                " ",
                strip=True,
            )
        )

        rating = normalize_rating(value)

        if rating is not None:
            return float(rating)

    return None




def extract_collection_products(html, max_products):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    cards = []
    seen_cards = set()

    for selector in PRODUCT_CARD_SELECTORS:
        for card in soup.select(selector):

            card_id = id(card)

            if card_id in seen_cards:
                continue

            seen_cards.add(card_id)
            cards.append(card)

    logging.info(
        "Collection product cards found: %s",
        len(cards),
    )

    products = []
    seen_links = set()

    for index, card in enumerate(
        cards,
        start=1,
    ):

        if len(products) >= max_products:
            break

        try:
     
            link_node = card.select_one(
                LINK_SELECTOR
            )

            if not link_node:
                continue

            product_link = absolute_url(
                link_node.get("href")
            )

            if not product_link:
                continue

            if product_link in seen_links:
                continue

            # Name.
            name = get_first_text(
                card,
                NAME_SELECTORS,
            )

            if not name:
                name = clean_text(
                    link_node.get("title")
                )

            if not name:
                name = clean_text(
                    link_node.get_text(
                        " ",
                        strip=True,
                    )
                )

            if not name:
                continue

            # Price.
            price_node = get_first_node(
                card,
                PRICE_SELECTORS,
            )

            price = parse_price(
                price_node.get_text(
                    " ",
                    strip=True,
                )
                if price_node
                else None
            )

       
            original_node = get_first_node(
                card,
                ORIGINAL_PRICE_SELECTORS,
            )

            original_price = parse_price(
                original_node.get_text(
                    " ",
                    strip=True,
                )
                if original_node
                else None
            )

            # Discount.
            discount_node = get_first_node(
                card,
                DISCOUNT_SELECTORS,
            )

            discount = parse_discount(
                discount_node.get_text(
                    " ",
                    strip=True,
                )
                if discount_node
                else None
            )

            if discount is None:
                discount = calculate_discount(
                    price,
                    original_price,
                )

            # Image.
            image_link = get_image(card)

            product = {
                "name": name,
                "price": price,
                "currency": CURRENCY,
                "original_price": original_price,
                "discount": discount,
                "ratings": None,
                "description": None,
                "image_link": image_link,
                "product_link": product_link,
                "organization_id": ORGANIZATION_ID,
                "store_id": STORE_ID,
                "categories_id": CATEGORY_ID,
            }

       
            missing = []

            for field in [
                "price",
                "original_price",
                "discount",
                "image_link",
                "product_link",
            ]:
                if product[field] is None:
                    missing.append(field)

            if missing:
                logging.warning(
                    "Skipping collection product | "
                    "%s | Missing: %s",
                    name,
                    ", ".join(missing),
                )
                continue

            products.append(product)
            seen_links.add(product_link)

            logging.info(
                "Collection product %s/%s | %s | "
                "Price=%s | Original=%s | Discount=%s",
                len(products),
                max_products,
                name,
                price,
                original_price,
                discount,
            )

        except Exception as error:
            logging.exception(
                "Collection product %s error: %s",
                index,
                error,
            )

    return products




async def scrape_product(
    crawler,
    product,
    number,
):
    logging.info(
        "Product %s | %s",
        number,
        product.get("name"),
    )

    logging.info(
        "Product URL | %s",
        product.get("product_link"),
    )

    try:
        result = await crawler.arun(
            url=product["product_link"],
            config=crawl_config(),
        )

        if not result.success:
            logging.error(
                "Product crawl failed | %s",
                getattr(
                    result,
                    "error_message",
                    "",
                ),
            )
            return None

        html = result.html or ""

      
        if looks_like_cloudflare(html):
            logging.warning(
                "Cloudflare challenge detected | %s",
                product["product_link"],
            )

            for second in range(
                CLOUDFLARE_WAIT
            ):
                await asyncio.sleep(1)

                retry = await crawler.arun(
                    url=product["product_link"],
                    config=crawl_config(),
                )

                retry_html = retry.html or ""

                if not looks_like_cloudflare(
                    retry_html
                ):
                    html = retry_html

                    logging.info(
                        "Cloudflare challenge cleared "
                        "after %ss",
                        second + 1,
                    )
                    break

            else:
                logging.error(
                    "Cloudflare did not clear | %s",
                    product["product_link"],
                )
                return None

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # Product page name.
        product_name_node = soup.select_one(
            "div.product__title h1.product__title-desk"
        )

        if not product_name_node:
            product_name_node = soup.select_one(
                "div.product__title a.product__title h2.h1"
            )

        if product_name_node:
            product_name = clean_text(
                product_name_node.get_text(
                    " ",
                    strip=True,
                )
            )

            if product_name:
                product["name"] = product_name

  
        description = extract_description(soup)

        if not description:
            logging.warning(
                "Description missing | %s",
                product.get("name"),
            )
            return None

        product["description"] = description

    
        product["ratings"] = extract_rating(soup)

    
        image_node = soup.select_one(
            'meta[property="og:image"]'
        )

        if image_node:
            image = absolute_url(
                image_node.get("content")
            )

            if image:
                product["image_link"] = image


        if not mandatory_fields_present(product):
            logging.warning(
                "Mandatory field missing after detail page | %s",
                product.get("name"),
            )
            return None

        logging.info(
            "SUCCESS | %s | Price=%s | Original=%s | "
            "Discount=%s | Rating=%s",
            product.get("name"),
            product.get("price"),
            product.get("original_price"),
            product.get("discount"),
            product.get("ratings"),
        )

       
        if product.get("price") is not None:
            product["price"] = int(float(product["price"]))

        if product.get("original_price") is not None:
            product["original_price"] = int(float(product["original_price"]))

        return product

    except Exception as error:
        logging.exception(
            "Product error | %s",
            error,
        )
        return None





async def scrape_beneude_async():
    all_products = []
    seen_product_links = set()

    logging.info("=" * 70)
    logging.info("BENE UDE CRAWL4AI SCRAPER")
    logging.info(
        "Collection URLs: %s",
        len(COLLECTION_URLS),
    )
    logging.info(
        "Maximum TOTAL products: %s",
        TOTAL_PRODUCTS,
    )
    logging.info(
        "Category: %s",
        CATEGORY_ID,
    )
    logging.info("=" * 70)

    async with AsyncWebCrawler(
        config=browser_config()
    ) as crawler:

        for url_index, collection_url in enumerate(
            COLLECTION_URLS,
            start=1,
        ):
            if len(all_products) >= TOTAL_PRODUCTS:
                break

            remaining = (
                TOTAL_PRODUCTS
                - len(all_products)
            )

            logging.info("=" * 70)
            logging.info(
                "Collection URL %s/%s",
                url_index,
                len(COLLECTION_URLS),
            )
            logging.info(
                "URL: %s",
                collection_url,
            )
            logging.info(
                "Remaining target: %s",
                remaining,
            )
            logging.info("=" * 70)

            try:
       
                result = await crawler.arun(
                    url=collection_url,
                    config=crawl_config(),
                )

                if not result.success:
                    logging.error(
                        "Collection crawl failed | %s",
                        getattr(
                            result,
                            "error_message",
                            "",
                        ),
                    )
                    continue

                collection_html = result.html or ""

                await asyncio.sleep(
                    COLLECTION_RENDER_WAIT
                )

          
                if looks_like_cloudflare(
                    collection_html
                ):
                    logging.warning(
                        "Cloudflare challenge detected "
                        "on collection."
                    )

                    cleared = False

                    for attempt in range(
                        1,
                        CLOUDFLARE_WAIT + 1,
                    ):
                        await asyncio.sleep(1)

                        retry = await crawler.arun(
                            url=collection_url,
                            config=crawl_config(),
                        )

                        retry_html = retry.html or ""

                        if not looks_like_cloudflare(
                            retry_html
                        ):
                            collection_html = retry_html
                            cleared = True

                            logging.info(
                                "Collection Cloudflare "
                                "cleared after %ss",
                                attempt,
                            )
                            break

                    if not cleared:
                        logging.error(
                            "Collection Cloudflare "
                            "did not clear."
                        )
                        continue

                page_products = (
                    extract_collection_products(
                        collection_html,
                        remaining,
                    )
                )

                logging.info(
                    "Collection candidates: %s",
                    len(page_products),
                )

                if not page_products:
                    logging.warning(
                        "No products found in collection."
                    )
                    continue

                collection_added = 0

         
                for product_index, product in enumerate(
                    page_products,
                    start=1,
                ):
                    if len(all_products) >= TOTAL_PRODUCTS:
                        break

                    product_link = product.get(
                        "product_link"
                    )

                    if (
                        not product_link
                        or product_link
                        in seen_product_links
                    ):
                        logging.info(
                            "Skipping duplicate | %s",
                            product_link,
                        )
                        continue

                    updated = await scrape_product(
                        crawler,
                        product,
                        product_index,
                    )

                    if not updated:
                        continue

                    updated["categories_id"] = CATEGORY_ID

                    if not mandatory_fields_present(
                        updated
                    ):
                        logging.warning(
                            "Skipping mandatory validation | %s",
                            updated.get("name"),
                        )
                        continue

                    if (
                        updated["product_link"]
                        in seen_product_links
                    ):
                        continue

                    seen_product_links.add(
                        updated["product_link"]
                    )

                    all_products.append(updated)
                    collection_added += 1

                    logging.info(
                        "GLOBAL COUNT: %s/%s",
                        len(all_products),
                        TOTAL_PRODUCTS,
                    )

                    if len(all_products) >= TOTAL_PRODUCTS:
                        break

                    await asyncio.sleep(
                        PRODUCT_DELAY
                    )

                logging.info(
                    "Collection complete | Added=%s | "
                    "Global=%s/%s",
                    collection_added,
                    len(all_products),
                    TOTAL_PRODUCTS,
                )

            except Exception as error:
                logging.exception(
                    "Collection exception | %s",
                    error,
                )
                continue


    return all_products[:TOTAL_PRODUCTS]


def run_beneude_scraper():
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

    logging.info(
        "Bene Ude Crawl4AI worker started."
    )

    logging.info(
        "Python: %s",
        sys.version,
    )

    logging.info(
        "Platform: %s",
        sys.platform,
    )

    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )
        except AttributeError:
            pass

    try:
        products = asyncio.run(
            scrape_beneude_async()
        )

        logging.info(
            "Bene Ude worker finished | Products=%s",
            len(products),
        )

        return products

    except Exception:
        logging.exception(
            "Bene Ude Crawl4AI worker failed."
        )
        raise




def save_json(products):
    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            products,
            file,
            ensure_ascii=False,
            indent=4,
        )

    logging.info(
        "JSON generated: %s",
        OUTPUT_JSON,
    )

    logging.info(
        "Products written: %s",
        len(products),
    )



class BeneudeSpider(scrapy.Spider):

    name = "beneude"

    allowed_domains = [
        "beneude.com",
        "www.beneude.com",
    ]

    async def start(self):
        self.logger.info("=" * 70)
        self.logger.info(
            "Starting Bene Ude Crawl4AI scraper..."
        )
        self.logger.info("=" * 70)

        loop = asyncio.get_running_loop()

        try:
         
            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:

                products = await loop.run_in_executor(
                    executor,
                    run_beneude_scraper,
                )

        except Exception as exc:
            self.logger.exception(
                "Bene Ude Crawl4AI worker failed: %s",
                exc,
            )
            return

        self.logger.info(
            "Crawl4AI returned %s products.",
            len(products),
        )

        valid_products = []
        skipped = 0

        for product in products:

            if not mandatory_fields_present(product):
                skipped += 1

                self.logger.warning(
                    "Skipping Bene Ude product because "
                    "mandatory field is missing | "
                    "name=%r",
                    product.get("name"),
                )
                continue

            valid_products.append(product)

            
            yield product

      
        save_json(valid_products)

        self.logger.info("=" * 70)
        self.logger.info(
            "Bene Ude spider completed."
        )
        self.logger.info(
            "Valid products: %s",
            len(valid_products),
        )
        self.logger.info(
            "Skipped products: %s",
            skipped,
        )
        self.logger.info(
            "Output: %s",
            OUTPUT_JSON,
        )
        self.logger.info("=" * 70)




if __name__ == "__main__":

    data = run_beneude_scraper()

    valid_products = [
        product
        for product in data
        if mandatory_fields_present(product)
    ]

    save_json(valid_products)

    print("=" * 70)
    print("BENE UDE SCRAPING COMPLETED")
    print("Valid products:", len(valid_products))
    print("Skipped products:", len(data) - len(valid_products))
    print("Output:", OUTPUT_JSON)
    print("=" * 70)
