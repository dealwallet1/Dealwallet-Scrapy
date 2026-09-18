import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode




BASE_URL = "https://www.abhishti.com"

MAX_TOTAL_PRODUCTS = 100

START_URLS = [
    (
        "https://www.abhishti.com/collections/kurta-bottom-sets",
        "Kurta Bottom Sets",
    ),
    (
        "https://www.abhishti.com/collections/kurtas",
        "Kurtas",
    ),
    (
        "https://www.abhishti.com/collections/dresses",
        "Dresses",
    ),
    (
        "https://www.abhishti.com/collections/banarasi",
        "Banarasi",
    ),
]






PRODUCT_CARD_SELECTOR = "product-item"

PRODUCT_LINK_SELECTOR = 'a[href*="/products/"]'

PRODUCT_NAME_SELECTOR = ".product-item-meta__title"

PRICE_SELECTOR = ".price--highlight"

ORIGINAL_PRICE_SELECTOR = ".price--compare"

DISCOUNT_SELECTOR = ".label--highlight"

PRIMARY_IMAGE_SELECTOR = "img.product-item__primary-image"

SECONDARY_IMAGE_SELECTOR = "img.product-item__secondary-image"




DESCRIPTION_SELECTOR = (
    ".product-tabs__content "
    ".product-tabs__tab-item-wrapper:first-child "
    ".product-tabs__tab-item-content.rte"
)




LISTING_PAGE_DELAY = 5
DETAIL_PAGE_DELAY = 3

RATE_LIMIT_DELAY = 30
MAX_RETRIES = 3

PAGE_TIMEOUT = 60000




def clean_text(text):
    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(text).replace("\xa0", " "),
    ).strip()


def extract_price(text):
    if not text:
        return None

    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        str(text).replace(",", ""),
    )

    if not numbers:
        return None

    try:
        value = float(numbers[0])

        if value.is_integer():
            return int(value)

        return value

    except (TypeError, ValueError):
        return None


def extract_discount(text):
    if not text:
        return None

    text = clean_text(text)

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        text,
        flags=re.I,
    )

    if not match:
        return None

    try:
        value = float(match.group(1))
        return int(round(value))

    except (TypeError, ValueError):
        return None


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    try:
        price = float(price)
        original_price = float(original_price)

        if original_price > price > 0:
            percent = round(
                (
                    (original_price - price)
                    / original_price
                ) * 100
            )

            return int(percent)

    except (TypeError, ValueError):
        pass

    return None


def normalize_product_name(name):
    if not name:
        return ""

    return clean_text(name)


def clean_product_url(url):
    if not url:
        return None

    url = url.strip()

    if not url:
        return None

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return urljoin(BASE_URL, url)

    return url


def clean_image_url(url):
    if not url:
        return None

    url = url.strip()

    if not url:
        return None

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return urljoin(BASE_URL, url)

    return url


def clean_rating(value):
    if not value:
        return None

    value = clean_text(value)

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        value,
    )

    if not match:
        return None

    try:
        rating = float(match.group(1))

        if rating <= 0 or rating > 5:
            return None

        return rating

    except (TypeError, ValueError):
        return None


def build_page_url(base_url, page_number):
    """
    Adds or replaces the Shopify page parameter.

    Example:
        /collections/kurtas
        -> /collections/kurtas?page=2
    """

    parsed = urlparse(base_url)

    query = dict(parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ))

    query["page"] = str(page_number)

    new_query = urlencode(query)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )




def extract_product_description(product_html):

    if not product_html:
        return None

    soup = BeautifulSoup(
        product_html,
        "html.parser",
    )



    description_element = soup.select_one(
        DESCRIPTION_SELECTOR
    )

    if not description_element:
        return None

    paragraphs = []

    for paragraph in description_element.find_all("p"):

        text = clean_text(
            paragraph.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue



        exact_type_match = re.search(
            r"^Type\s*:\s*Sets?\s*$",
            text,
            flags=re.I,
        )

        if exact_type_match:
            break

  
        type_match = re.search(
            r"\bType\s*:\s*Sets?\b",
            text,
            flags=re.I,
        )

        if type_match:

            text_before_type = (
                text[:type_match.start()].strip()
            )

            if text_before_type:
                paragraphs.append(
                    text_before_type
                )

            break

        paragraphs.append(text)

    description = clean_text(
        " ".join(paragraphs)
    )

    return description or None



def extract_product_rating(product_html):

    if not product_html:
        return None

    soup = BeautifulSoup(
        product_html,
        "html.parser",
    )



    scripts = soup.find_all(
        "script",
        type="application/ld+json",
    )

    for script in scripts:

        raw_json = (
            script.string
            or script.get_text()
        )

        if not raw_json:
            continue

        try:
            data = json.loads(raw_json)

        except Exception:
            continue

        objects = []

        if isinstance(data, dict):
            objects.append(data)

            graph = data.get("@graph")

            if isinstance(graph, list):
                objects.extend(graph)

        elif isinstance(data, list):
            objects.extend(data)

        for item in objects:

            if not isinstance(item, dict):
                continue

            aggregate = item.get(
                "aggregateRating"
            )

            if isinstance(
                aggregate,
                dict,
            ):

                rating = clean_rating(
                    aggregate.get(
                        "ratingValue"
                    )
                )

                if rating is not None:
                    return rating


    selectors = [
        "[data-rating]",
        "[data-product-rating]",
        ".rating",
        ".product-rating",
        ".spr-starrating",
        "[class*='rating']",
        "[class*='Rating']",
    ]

    for selector in selectors:

        elements = soup.select(
            selector
        )

        for element in elements:

            for attribute in [
                "data-rating",
                "data-product-rating",
                "data-value",
            ]:

                value = element.get(
                    attribute
                )

                rating = clean_rating(
                    value
                )

                if rating is not None:
                    return rating

   
            rating = clean_rating(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if rating is not None:
                return rating

   

    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True,
        )
    )

    patterns = [
        r"(\d+(?:\.\d+)?)\s*/\s*5",
        r"rating\s*[:\-]?\s*(\d+(?:\.\d+)?)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            page_text,
            flags=re.I,
        )

        if match:

            rating = clean_rating(
                match.group(1)
            )

            if rating is not None:
                return rating

    return None



async def open_listing_page(
    crawler,
    url,
):

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            logging.info(
                "Opening Abhishti collection "
                "(attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                url,
            )

            config = CrawlerRunConfig(
                wait_for=None,
                page_timeout=PAGE_TIMEOUT,
                cache_mode=CacheMode.BYPASS,
                delay_before_return_html=4,
                verbose=False,
            )

            result = await crawler.arun(
                url=url,
                config=config,
            )

            if result.success:
                return result

            logging.warning(
                "Abhishti collection request failed "
                "(attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                url,
            )

        except Exception as exc:

            logging.warning(
                "Failed to open Abhishti collection "
                "%s: %s",
                url,
                exc,
            )

        if attempt < MAX_RETRIES:

            logging.info(
                "Waiting %s seconds before retry.",
                RATE_LIMIT_DELAY,
            )

            await asyncio.sleep(
                RATE_LIMIT_DELAY
            )

    logging.error(
        "Giving up Abhishti collection: %s",
        url,
    )

    return None



async def open_product_page(
    crawler,
    product_link,
):

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            logging.info(
                "Opening Abhishti product detail "
                "(attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                product_link,
            )

            config = CrawlerRunConfig(
                wait_for=None,
                page_timeout=PAGE_TIMEOUT,
                cache_mode=CacheMode.BYPASS,
                delay_before_return_html=2,
                verbose=False,
            )

            result = await crawler.arun(
                url=product_link,
                config=config,
            )

            if result.success:
                return result

            logging.warning(
                "Abhishti product request failed "
                "(attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                product_link,
            )

        except Exception as exc:

            logging.warning(
                "Failed Abhishti product page "
                "%s: %s",
                product_link,
                exc,
            )

        if attempt < MAX_RETRIES:

            logging.info(
                "Waiting %s seconds before retry.",
                RATE_LIMIT_DELAY,
            )

            await asyncio.sleep(
                RATE_LIMIT_DELAY
            )

    logging.error(
        "Giving up Abhishti product page: %s",
        product_link,
    )

    return None



async def scrape_abhishti_products():

    all_products = []

    seen_names = set()
    seen_links = set()

    category_counts = {}

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

       

        for category_url, collection_name in START_URLS:

            if len(all_products) >= MAX_TOTAL_PRODUCTS:
                break

            category_count = 0
            page_number = 1

            logging.info("=" * 70)
            logging.info(
                "Processing Abhishti collection: %s",
                collection_name,
            )
            logging.info(
                "Collection URL: %s",
                category_url,
            )
            logging.info(
                "Total progress: %s/%s",
                len(all_products),
                MAX_TOTAL_PRODUCTS,
            )

        
            remaining_total = MAX_TOTAL_PRODUCTS - len(all_products)
            remaining_urls = len(START_URLS) - START_URLS.index(
                (category_url, collection_name)
            )
            target_for_this_url = min(
                remaining_total,
                (remaining_total + remaining_urls - 1) // remaining_urls,
            )

            while len(all_products) < MAX_TOTAL_PRODUCTS:

                page_url = build_page_url(
                    category_url,
                    page_number,
                )

                logging.info("-" * 70)
                logging.info(
                    "Abhishti collection: %s | Page: %s",
                    collection_name,
                    page_number,
                )
                logging.info(
                    "Page URL: %s",
                    page_url,
                )

                if page_number > 1:

                    await asyncio.sleep(
                        LISTING_PAGE_DELAY
                    )

                result = await open_listing_page(
                    crawler,
                    page_url,
                )

                if result is None:
                    break

                if not result.html:
                    logging.warning(
                        "Empty HTML received: %s",
                        page_url,
                    )
                    break

                soup = BeautifulSoup(
                    result.html,
                    "html.parser",
                )

                cards = soup.select(
                    PRODUCT_CARD_SELECTOR
                )

                logging.info(
                    "Found %s Abhishti product cards on page %s.",
                    len(cards),
                    page_number,
                )

                if not cards:
                    logging.info(
                        "No product cards found. Ending this collection."
                    )
                    break

                new_products_this_page = 0

                for index, card in enumerate(cards, start=1):

                    if len(all_products) >= MAX_TOTAL_PRODUCTS:
                        break

                 
                    if category_count >= target_for_this_url and len(all_products) < MAX_TOTAL_PRODUCTS:
                        break

                    try:


                        link_el = card.select_one(
                            PRODUCT_LINK_SELECTOR
                        )

                        product_link = (
                            clean_product_url(
                                link_el.get("href", "")
                            )
                            if link_el
                            else None
                        )

                        if product_link is None:
                            logging.info(
                                "Product link not found for card #%s. Storing None.",
                                index,
                            )

                      

                        name_el = card.select_one(
                            PRODUCT_NAME_SELECTOR
                        )

                        name = normalize_product_name(
                            name_el.get_text(" ", strip=True)
                            if name_el
                            else ""
                        )

                        if not name:

                            img_fallback = card.select_one(
                                PRIMARY_IMAGE_SELECTOR
                            )

                            if img_fallback:
                                name = normalize_product_name(
                                    img_fallback.get("alt", "")
                                )

                        if not name:
                            continue

                        name_key = name.lower()

                

                        if name_key in seen_names:
                            continue

                        if (
                            product_link is not None
                            and product_link in seen_links
                        ):
                            continue

               

                        price_el = card.select_one(
                            PRICE_SELECTOR
                        )

                        price = extract_price(
                            price_el.get_text(" ", strip=True)
                            if price_el
                            else ""
                        )

             

                        original_price_el = card.select_one(
                            ORIGINAL_PRICE_SELECTOR
                        )

                        original_price = extract_price(
                            original_price_el.get_text(" ", strip=True)
                            if original_price_el
                            else ""
                        )

                    

                        discount_el = card.select_one(
                            DISCOUNT_SELECTOR
                        )

                        discount = extract_discount(
                            discount_el.get_text(" ", strip=True)
                            if discount_el
                            else ""
                        )

                        if not discount:
                            discount = calculate_discount(
                                price,
                                original_price,
                            )

          

                        image_el = card.select_one(
                            PRIMARY_IMAGE_SELECTOR
                        )

                        if not image_el:
                            image_el = card.select_one(
                                SECONDARY_IMAGE_SELECTOR
                            )

                        image_link = clean_image_url(
                            (
                                image_el.get("src")
                                or image_el.get("data-src")
                                or image_el.get("data-original")
                                or ""
                            )
                            if image_el
                            else ""
                        )

                        if image_link is None:
                            logging.info(
                                "Image not found for %s. Storing None.",
                                name,
                            )


                        await asyncio.sleep(
                            DETAIL_PAGE_DELAY
                        )

                        detail_result = None

                        if product_link is not None:
                            detail_result = await open_product_page(
                                crawler,
                                product_link,
                            )

                        description = None
                        ratings = None

                        if detail_result and detail_result.html:

                            description = extract_product_description(
                                detail_result.html
                            )

                            ratings = extract_product_rating(
                                detail_result.html
                            )

           

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

               

                        product_data = {
                            "name": name if name else None,
                            "price": price if price is not None else None,
                            "currency": "₹",
                            "original_price": (
                                original_price
                                if original_price is not None
                                else None
                            ),
                            "discount": (
                                discount
                                if discount is not None
                                else None
                            ),
                            "ratings": (
                                ratings
                                if ratings is not None
                                else None
                            ),
                            "description": (
                                description
                                if description is not None
                                else None
                            ),
                            "image_link": (
                                image_link
                                if image_link is not None
                                else None
                            ),
                            "product_link": (
                                product_link
                                if product_link is not None
                                else None
                            ),
                            "organization_id": "Dealwallet",
                            "store_id": "Abhishti",
                            "categories_id": "Fashion & Lifestyle",
                            "created_at": timestamp,
                        }

                        all_products.append(
                            product_data
                        )

                        seen_names.add(
                            name_key
                        )

                        if product_link is not None:
                            seen_links.add(
                                product_link
                            )

                        category_count += 1
                        new_products_this_page += 1

                        logging.info(
                            "Scraped Abhishti product %s/%s: %s",
                            len(all_products),
                            MAX_TOTAL_PRODUCTS,
                            name,
                        )

                    except Exception as exc:

                        logging.exception(
                            "Error parsing Abhishti product card #%s on page %s: %s",
                            index,
                            page_number,
                            exc,
                        )

                if len(all_products) >= MAX_TOTAL_PRODUCTS:
                    break

                if category_count >= target_for_this_url:
                    break

                if new_products_this_page == 0:
                    logging.info(
                        "No new unique products found on page %s. Ending collection %s.",
                        page_number,
                        collection_name,
                    )
                    break

                page_number += 1

            category_counts[collection_name] = category_count

            logging.info(
                "Finished Abhishti collection '%s': %s products",
                collection_name,
                category_count,
            )
            logging.info(
                "Overall progress: %s/%s",
                len(all_products),
                MAX_TOTAL_PRODUCTS,
            )

      

        all_products = all_products[:MAX_TOTAL_PRODUCTS]


  

    logging.info("=" * 70)
    logging.info(
        "Total Abhishti products scraped: %s",
        len(all_products),
    )
    logging.info(
        "Abhishti collection counts: %s",
        category_counts,
    )
    logging.info(
        "Category: Fashion & Lifestyle"
    )
    logging.info(
        "Store: Abhishti"
    )
    logging.info(
        "Organization: Dealwallet"
    )
    logging.info("=" * 70)

    return all_products




def run_abhishti_scraper():

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

    logging.info("=" * 70)

    logging.info(
        "Abhishti browser process started."
    )

    logging.info("=" * 70)

    try:

        results = asyncio.run(
            scrape_abhishti_products()
        )

        logging.info(
            "Abhishti browser process finished. "
            "Products returned: %s",
            len(results),
        )

        return results

    except Exception:

        logging.exception(
            "Abhishti browser process failed."
        )

        raise




class AbhishtiSpider(scrapy.Spider):

    name = "abhishti"

    allowed_domains = [
        "abhishti.com",
        "www.abhishti.com",
    ]

    async def start(self):

        self.logger.info("=" * 70)

        self.logger.info(
            "Starting Abhishti Crawl4AI scraper..."
        )

        self.logger.info("=" * 70)

  

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_abhishti_scraper,
            )

        self.logger.info(
            "Abhishti scraper returned %s products.",
            len(products),
        )


        for product in products:
            yield product

        self.logger.info("=" * 70)

        self.logger.info(
            "Abhishti spider completed."
        )

        self.logger.info("=" * 70)




if __name__ == "__main__":

    data = run_abhishti_scraper()

    print(
        "Total products:",
        len(data),
    )
