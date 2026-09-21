import asyncio
import json
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor

import scrapy
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode



BASE_URL = "https://www.amydus.com"

START_URLS = [
    "https://www.amydus.com/collections/plus-size-dresses",
    "https://www.amydus.com/collections/plus-size-kurtis",
]

PRODUCTS_PER_URL = 50

MAX_PAGES = 10
PAGE_START = 1

LISTING_PAGE_WAIT = 6
DETAIL_PAGE_WAIT = 4
PAGE_TIMEOUT = 180000

MAX_CRAWL_RETRIES = 2
RETRY_DELAY = 10

OUTPUT_FILE = "amydus_products.json"

ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Amydus"
CATEGORIES_ID = "Fashion & Lifestyle"
CURRENCY = "₹"



PRODUCT_CARD_SELECTOR = "product-item"

PRODUCT_LINK_SELECTOR = 'a[href*="/products/"]'

PRODUCT_NAME_SELECTOR = ".product-item-meta__title"

PRICE_SELECTOR = ".price--highlight"

ORIGINAL_PRICE_SELECTOR = ".price--compare"

PRIMARY_IMAGE_SELECTOR = "img.product-item__primary-image"

SECONDARY_IMAGE_SELECTOR = "img.product-item__secondary-image"

RATING_SELECTOR = ".jdgm-prev-badge"

RATING_ATTRIBUTE = "data-average-rating"




PDP_PRICE_SELECTOR = (
    ".product-meta__price-list-container "
    ".price--highlight.pdp_prices.price--large"
)

PDP_ORIGINAL_PRICE_SELECTOR = (
    ".product-meta__price-list-container "
    ".price--compare"
)

PDP_DISCOUNT_SELECTOR = (
    ".product-meta__label-list.label-list "
    ".label--highlight"
)


DESCRIPTION_SELECTOR = (
    ".product-tabs__tab-item-content.rte"
)


DESCRIPTION_PARAGRAPH_SELECTOR = "p"




def normalize_text(text):
    if not text:
        return ""

    text = str(text).replace(
        "\xa0",
        " ",
    )


    text = text.replace(
        '\\"',
        "",
    )

    text = text.replace(
        '"',
        "",
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def clean_optional_text(text):
    text = normalize_text(text)
    return text if text else None




def extract_int(text):
    if not text:
        return None

    match = re.search(
        r"[\d,]+",
        str(text),
    )

    if not match:
        return None

    try:
        return int(
            match.group(0).replace(
                ",",
                "",
            )
        )
    except (
        ValueError,
        TypeError,
    ):
        return None


def extract_float(text):
    if not text:
        return None

    match = re.search(
        r"\d+(?:\.\d+)?",
        str(text),
    )

    if not match:
        return None

    try:
        return float(
            match.group(0)
        )
    except (
        ValueError,
        TypeError,
    ):
        return None


def extract_discount(text):
    if not text:
        return None

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        str(text),
        flags=re.I,
    )

    if not match:
        return None

    try:
        return int(
            round(
                float(
                    match.group(1)
                )
            )
        )
    except (
        ValueError,
        TypeError,
    ):
        return None


def calculate_discount(
    price,
    original_price,
):
    if (
        price is None
        or original_price is None
    ):
        return None

    try:
        price = float(price)
        original_price = float(
            original_price
        )

        if (
            original_price > price > 0
        ):
            return int(
                round(
                    (
                        (
                            original_price
                            - price
                        )
                        / original_price
                    )
                    * 100
                )
            )

    except (
        ValueError,
        TypeError,
    ):
        pass

    return None



def clean_url(value):
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.startswith(
        "data:image"
    ):
        return None

    if value.startswith("//"):
        return "https:" + value

    return urljoin(
        BASE_URL,
        value,
    )


def extract_largest_srcset(
    srcset,
):
    if not srcset:
        return None

    candidates = []

    for part in str(srcset).split(","):
        part = part.strip()

        if not part:
            continue

        values = part.split()

        if not values:
            continue

        image_url = clean_url(
            values[0]
        )

        if not image_url:
            continue

        width = 0

        if len(values) > 1:
            match = re.search(
                r"(\d+)w",
                values[1],
            )

            if match:
                width = int(
                    match.group(1)
                )

        candidates.append(
            (
                width,
                image_url,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0]
    )

    return candidates[-1][1]


def extract_image_url(
    product_element,
):
    image = product_element.select_one(
        PRIMARY_IMAGE_SELECTOR
    )

    if image:
        for attribute in [
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
        ]:
            value = clean_url(
                image.get(attribute)
            )

            if value:
                return value

        value = extract_largest_srcset(
            image.get("srcset")
        )

        if value:
            return value

    image = product_element.select_one(
        SECONDARY_IMAGE_SELECTOR
    )

    if image:
        for attribute in [
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
        ]:
            value = clean_url(
                image.get(attribute)
            )

            if value:
                return value

        value = extract_largest_srcset(
            image.get("srcset")
        )

        if value:
            return value

    return None




def extract_product_url(
    product_element,
):
    link = product_element.select_one(
        PRODUCT_LINK_SELECTOR
    )

    if not link:
        return None

    href = link.get("href")

    if not href:
        return None

    return clean_url(
        href
    )




def extract_rating(
    product_element,
):
    rating_element = (
        product_element.select_one(
            RATING_SELECTOR
        )
    )

    if not rating_element:
        return None

    rating = extract_float(
        rating_element.get(
            RATING_ATTRIBUTE
        )
    )

    if (
        rating is None
        or rating <= 0
        or rating > 5
    ):
        return None

    return rating


def extract_rating_from_detail(
    soup,
):
    rating_element = soup.select_one(
        RATING_SELECTOR
    )

    if rating_element:
        rating = extract_float(
            rating_element.get(
                RATING_ATTRIBUTE
            )
        )

        if (
            rating is not None
            and 0 < rating <= 5
        ):
            return rating

    # JSON-LD fallback.
    scripts = soup.select(
        'script[type="application/ld+json"]'
    )

    for script in scripts:
        raw = script.string or script.get_text()

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = []

        if isinstance(
            data,
            dict,
        ):
            objects.append(data)

            graph = data.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):
                objects.extend(
                    graph
                )

        elif isinstance(
            data,
            list,
        ):
            objects.extend(data)

        for obj in objects:
            if not isinstance(
                obj,
                dict,
            ):
                continue

            aggregate = obj.get(
                "aggregateRating"
            )

            if isinstance(
                aggregate,
                dict,
            ):
                rating = extract_float(
                    aggregate.get(
                        "ratingValue"
                    )
                )

                if (
                    rating is not None
                    and 0 < rating <= 5
                ):
                    return rating

    return None




def extract_product_name(
    product_element,
):
    title_element = (
        product_element.select_one(
            PRODUCT_NAME_SELECTOR
        )
    )

    if title_element:
        name = normalize_text(
            title_element.get_text(
                " ",
                strip=True,
            )
        )

        if name:
            return name

    return None




def parse_listing_product(
    product_element,
):
    name = extract_product_name(
        product_element
    )

    product_link = extract_product_url(
        product_element
    )

    price_element = (
        product_element.select_one(
            PRICE_SELECTOR
        )
    )

    price = extract_int(
        price_element.get_text(
            " ",
            strip=True,
        )
        if price_element
        else None
    )

    original_price_element = (
        product_element.select_one(
            ORIGINAL_PRICE_SELECTOR
        )
    )

    original_price = extract_int(
        original_price_element.get_text(
            " ",
            strip=True,
        )
        if original_price_element
        else None
    )

    # Listing page does not have a reliable dedicated
    # percentage selector in the supplied markup.
    discount = calculate_discount(
        price,
        original_price,
    )

    image_link = extract_image_url(
        product_element
    )

    ratings = extract_rating(
        product_element
    )

    return {
        "name": name,
        "price": price,
        "currency": CURRENCY,
        "original_price": original_price,
        "discount": discount,
        "ratings": ratings,
        "description": None,
        "image_link": image_link,
        "product_link": product_link,
        "organization_id": ORGANIZATION_ID,
        "store_id": STORE_ID,
        "categories_id": CATEGORIES_ID,
        "created_at": datetime.now(
            timezone(
                timedelta(
                    hours=5,
                    minutes=30,
                )
            )
        ).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
    }




def parse_detail_page(
    html,
):
    if not html:
        return {
            "price": None,
            "original_price": None,
            "discount": None,
            "description": None,
            "ratings": None,
            "image_link": None,
        }

    soup = BeautifulSoup(
        html,
        "html.parser",
    )



    price_element = soup.select_one(
        PDP_PRICE_SELECTOR
    )

    price = extract_int(
        price_element.get_text(
            " ",
            strip=True,
        )
        if price_element
        else None
    )

    original_price_element = soup.select_one(
        PDP_ORIGINAL_PRICE_SELECTOR
    )

    original_price = extract_int(
        original_price_element.get_text(
            " ",
            strip=True,
        )
        if original_price_element
        else None
    )

 
    discount_element = soup.select_one(
        PDP_DISCOUNT_SELECTOR
    )

    discount = extract_discount(
        discount_element.get_text(
            " ",
            strip=True,
        )
        if discount_element
        else None
    )

    if discount is None:
        discount = calculate_discount(
            price,
            original_price,
        )

  

    description = None

    description_container = soup.select_one(
        DESCRIPTION_SELECTOR
    )

    if description_container:
        paragraphs = (
            description_container.select(
                DESCRIPTION_PARAGRAPH_SELECTOR
            )
        )

        parts = []

        for paragraph in paragraphs[:3]:
            text = normalize_text(
                paragraph.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                parts.append(text)

        if parts:
            description = normalize_text(
                " ".join(parts)
            )

   

    ratings = extract_rating_from_detail(
        soup
    )


    image_link = None

    image = soup.select_one(
        'meta[property="og:image"]'
    )

    if image:
        image_link = clean_url(
            image.get("content")
        )

    if not image_link:
        product_json_scripts = soup.select(
            'script[type="application/ld+json"]'
        )

        for script in product_json_scripts:
            raw = (
                script.string
                or script.get_text()
            )

            if not raw:
                continue

            try:
                data = json.loads(raw)
            except Exception:
                continue

            objects = []

            if isinstance(
                data,
                dict,
            ):
                objects.append(data)

                graph = data.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list,
                ):
                    objects.extend(
                        graph
                    )

            elif isinstance(
                data,
                list,
            ):
                objects.extend(data)

            for obj in objects:
                if not isinstance(
                    obj,
                    dict,
                ):
                    continue

                images = obj.get(
                    "image"
                )

                if isinstance(
                    images,
                    list,
                ):
                    for value in images:
                        image_link = clean_url(
                            value
                        )

                        if image_link:
                            break
                else:
                    image_link = clean_url(
                        images
                    )

                if image_link:
                    break

            if image_link:
                break

    return {
        "price": price,
        "original_price": original_price,
        "discount": discount,
        "description": description,
        "ratings": ratings,
        "image_link": image_link,
    }




async def open_amydus_page(
    crawler,
    url,
    page_type="page",
):
    for attempt in range(
        1,
        MAX_CRAWL_RETRIES + 1,
    ):
        try:
            logging.info(
                "Opening Amydus %s "
                "(attempt %s/%s): %s",
                page_type,
                attempt,
                MAX_CRAWL_RETRIES,
                url,
            )

            config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_until="domcontentloaded",
                page_timeout=PAGE_TIMEOUT,
                delay_before_return_html=(
                    LISTING_PAGE_WAIT
                    if page_type == "listing"
                    else DETAIL_PAGE_WAIT
                ),
                verbose=False,
            )

            result = await crawler.arun(
                url=url,
                config=config,
            )

            logging.info(
                "Crawl4AI result | "
                "type=%s | success=%s | url=%s",
                page_type,
                getattr(
                    result,
                    "success",
                    None,
                ),
                getattr(
                    result,
                    "url",
                    None,
                ),
            )

            if result.success:
                html = result.html or ""

                logging.info(
                    "Amydus %s HTML length: %s",
                    page_type,
                    len(html),
                )

                return result

            logging.error(
                "Amydus %s crawl unsuccessful | error=%s",
                page_type,
                getattr(
                    result,
                    "error_message",
                    None,
                ),
            )

        except Exception as exc:
            logging.exception(
                "Amydus %s crawl exception: %s",
                page_type,
                exc,
            )

        if attempt < MAX_CRAWL_RETRIES:
            logging.info(
                "Waiting %s seconds before retrying Amydus %s.",
                RETRY_DELAY,
                page_type,
            )

            await asyncio.sleep(
                RETRY_DELAY
            )

    logging.error(
        "Giving up Amydus %s: %s",
        page_type,
        url,
    )

    return None




async def scrape_amydus_async():
    all_products = []



    logging.info(
        "Amydus Crawl4AI: starting browser crawler."
    )

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

        for collection_url in START_URLS:

            collection_products = []
            seen_links = set()

            logging.info("=" * 60)
            logging.info(
                "Amydus collection: %s",
                collection_url,
            )
            logging.info("=" * 60)

            for page_number in range(
                PAGE_START,
                PAGE_START + MAX_PAGES,
            ):
                if (
                    len(collection_products)
                    >= PRODUCTS_PER_URL
                ):
                    break

                if page_number == 1:
                    listing_url = collection_url
                else:
                    parsed = urlparse(
                        collection_url
                    )

                    query = dict(
                        parse_qsl(
                            parsed.query,
                            keep_blank_values=True,
                        )
                    )

                    query["page"] = str(
                        page_number
                    )

                    listing_url = urlunparse(
                        (
                            parsed.scheme,
                            parsed.netloc,
                            parsed.path,
                            parsed.params,
                            urlencode(query),
                            parsed.fragment,
                        )
                    )

                logging.info(
                    "Amydus listing page %s/%s: %s",
                    page_number,
                    PAGE_START + MAX_PAGES - 1,
                    listing_url,
                )

                result = await open_amydus_page(
                    crawler,
                    listing_url,
                    "listing",
                )

                if result is None:
                    continue

                html = result.html or ""

                if not html:
                    logging.warning(
                        "Amydus listing returned empty HTML: %s",
                        listing_url,
                    )
                    continue

                soup = BeautifulSoup(
                    html,
                    "html.parser",
                )

                product_elements = soup.select(
                    PRODUCT_CARD_SELECTOR
                )

                logging.info(
                    "Amydus product cards found: %s",
                    len(product_elements),
                )

                if not product_elements:
                    logging.warning(
                        "NO AMYDUS PRODUCTS FOUND | page=%s | url=%s",
                        page_number,
                        listing_url,
                    )

             
                    if page_number == 1:
                        break

                    continue

                page_new_products = 0

                for product_element in (
                    product_elements
                ):
                    if (
                        len(collection_products)
                        >= PRODUCTS_PER_URL
                    ):
                        break

                    product = (
                        parse_listing_product(
                            product_element
                        )
                    )

                    product_link = product.get(
                        "product_link"
                    )

                    if not product_link:
                        continue

                    if (
                        product_link
                        in seen_links
                    ):
                        continue

                    seen_links.add(
                        product_link
                    )

                    collection_products.append(
                        product
                    )

                    page_new_products += 1

                    logging.info(
                        "Amydus product %s/%s: %s",
                        len(collection_products),
                        PRODUCTS_PER_URL,
                        product.get(
                            "name"
                        ),
                    )

                logging.info(
                    "Amydus page %s complete | "
                    "new=%s | collection total=%s",
                    page_number,
                    page_new_products,
                    len(collection_products),
                )

                if page_new_products == 0:
                    break

            logging.info(
                "Amydus collection complete | "
                "%s | products=%s",
                collection_url,
                len(collection_products),
            )

            all_products.extend(
                collection_products
            )

 

    if not all_products:
        logging.error(
            "Amydus Crawl4AI collected ZERO products."
        )

        return all_products

    logging.info("=" * 60)
    logging.info(
        "Amydus detail phase started. "
        "Products collected: %s",
        len(all_products),
    )
    logging.info("=" * 60)

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

        for index, product in enumerate(
            all_products,
            start=1,
        ):
            product_url = product.get(
                "product_link"
            )

            if not product_url:
                continue

            logging.info(
                "Amydus detail page %s/%s: %s",
                index,
                len(all_products),
                product_url,
            )

            result = await open_amydus_page(
                crawler,
                product_url,
                "detail",
            )

            if result is None:
                continue

            details = parse_detail_page(
                result.html
            )

            # Product-page values are authoritative.
            if details["price"] is not None:
                product["price"] = (
                    details["price"]
                )

            if (
                details["original_price"]
                is not None
            ):
                product[
                    "original_price"
                ] = details[
                    "original_price"
                ]

            if details["discount"] is not None:
                product["discount"] = (
                    details["discount"]
                )
            else:
                product["discount"] = (
                    calculate_discount(
                        product["price"],
                        product["original_price"],
                    )
                )

            if details["description"]:
                product["description"] = (
                    details["description"]
                )

            if details["ratings"] is not None:
                product["ratings"] = (
                    details["ratings"]
                )

            if (
                details["image_link"]
                and not product[
                    "image_link"
                ]
            ):
                product["image_link"] = (
                    details["image_link"]
                )

    logging.info(
        "Amydus Crawl4AI completed. "
        "Total products: %s",
        len(all_products),
    )

    return all_products



def save_json(
    products,
):
    with open(
        OUTPUT_FILE,
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
        "Amydus JSON generated: %s",
        OUTPUT_FILE,
    )

    logging.info(
        "Products written: %s",
        len(products),
    )



def run_amydus_scraper():
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
        "Amydus Crawl4AI browser process started."
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
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    try:
        products = asyncio.run(
            scrape_amydus_async()
        )

        logging.info(
            "Amydus Crawl4AI browser process finished. "
            "Products returned: %s",
            len(products),
        )

        return products

    except Exception:
        logging.exception(
            "Amydus Crawl4AI browser process failed."
        )
        raise




class AmydusSpider(scrapy.Spider):

    name = "amydus"

    allowed_domains = [
        "amydus.com",
        "www.amydus.com",
    ]

    async def start(self):

        self.logger.info(
            "=" * 70
        )

        self.logger.info(
            "Starting Amydus Crawl4AI scraper..."
        )

        self.logger.info(
            "=" * 70
        )

        loop = asyncio.get_running_loop()

        try:
            # Run Crawl4AI in a separate process exactly like
            # the reference spider architecture.
            with ProcessPoolExecutor(
                max_workers=1
            ) as executor:

                products = await loop.run_in_executor(
                    executor,
                    run_amydus_scraper,
                )

        except Exception as exc:
            self.logger.exception(
                "Amydus Crawl4AI worker failed: %s",
                exc,
            )
            return

        self.logger.info(
            "Crawl4AI returned %s products.",
            len(products),
        )

        yielded = 0
        skipped = 0


        for product in products:

            name = product.get(
                "name"
            )

            price = product.get(
                "price"
            )

            image_link = product.get(
                "image_link"
            )

            product_link = product.get(
                "product_link"
            )

            if (
                not name
                or price is None
                or not image_link
                or not product_link
            ):
                skipped += 1

                self.logger.warning(
                    "Skipping Amydus product because "
                    "mandatory field is missing | "
                    "name=%r | price=%r | image_link=%r | product_link=%r",
                    name,
                    price,
                    image_link,
                    product_link,
                )

                continue

            yielded += 1

            yield product

        self.logger.info(
            "=" * 70
        )

        self.logger.info(
            "Amydus spider completed."
        )

        self.logger.info(
            "Valid products yielded: %s",
            yielded,
        )

        self.logger.info(
            "Products skipped: %s",
            skipped,
        )

        self.logger.info(
            "=" * 70
        )




if __name__ == "__main__":
    data = run_amydus_scraper()

    valid_products = []

    for product in data:
        if (
            product.get("name")
            and product.get("price") is not None
            and product.get("image_link")
            and product.get("product_link")
        ):
            valid_products.append(
                product
            )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            valid_products,
            file,
            ensure_ascii=False,
            indent=4,
        )

    print(
        "=" * 70
    )

    print(
        "Valid products written:",
        len(valid_products),
    )

    print(
        "Skipped products:",
        len(data) - len(valid_products),
    )

    print(
        "Output:",
        OUTPUT_FILE,
    )

    print(
        "=" * 70
    )
