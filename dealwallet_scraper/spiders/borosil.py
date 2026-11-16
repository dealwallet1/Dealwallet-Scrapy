
import asyncio
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import scrapy
from crawl4ai import AsyncWebCrawler
from parsel import Selector


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_FILE = PROJECT_ROOT / "scrape_borosil_appliances.json"

BASE_URLS = {
    "Home & Appliances": [
        "https://myborosil.com/collections/appliances",
    ],
    "Home Decor": [
        "https://myborosil.com/collections/home-decor",
    ],
    "Home & Kitchen": [
        "https://myborosil.com/collections/cookware",
    ]
}

MAX_PAGES = 10
PAGE_TIMEOUT = 60000
RETRY_COUNT = 3
RETRY_DELAY = 3

STORE_ID = "Borosil"
ORGANIZATION_ID = "Dealwallet"

logger = logging.getLogger(__name__)


def clean_text(value):
    if value is None:
        return "N/A"
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or "N/A"


def extract_text(element):
    if element is None:
        return "N/A"
    try:
        return clean_text(element.xpath("string(.)").get())
    except Exception:
        return "N/A"


def create_selector(html):
    if not html:
        return None
    try:
        return Selector(text=html)
    except Exception as exc:
        logger.error("Could not parse HTML: %s", exc)
        return None


def normalize_url(url):
    if not url:
        return None
    return urljoin("https://myborosil.com", str(url).strip())


def clean_product_url(url):
    url = normalize_url(url)
    if not url:
        return None

    try:
        parsed = urlsplit(url)
        path = parsed.path

        match = re.search(
            r"/collections/[^/]+(/products/[^/]+)$",
            path,
            flags=re.IGNORECASE,
        )
        if match:
            path = match.group(1)

        if "/products/" not in path:
            return None

        return urlunsplit((
            "https",
            "myborosil.com",
            path.rstrip("/"),
            "",
            "",
        ))
    except Exception:
        return None


def extract_number(value):
    if value is None:
        return None

    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None

    try:
        number = float(match.group(0).replace(",", ""))
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def shopify_amount(value):
    if value is None or value == "":
        return None
    try:
        amount = float(value) / 100
        return int(amount) if amount.is_integer() else round(amount, 2)
    except (TypeError, ValueError):
        return None


def calculate_discount(original_price, sale_price):
    if original_price is None or sale_price is None:
        return "N/A"

    try:
        original = float(original_price)
        sale = float(sale_price)

        if original <= 0 or sale < 0 or sale >= original:
            return 0 if sale >= original else "N/A"

        return round(((original - sale) / original) * 100)
    except (TypeError, ValueError):
        return "N/A"


def extract_borosil_json(card):
    if card is None:
        return None

    for script_text in card.css("script.variant-data::text").getall():
        try:
            data = json.loads(script_text.strip())
            if isinstance(data, dict) and data.get("id"):
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def extract_product_cards(selector):
    if selector is None:
        return []

    return selector.css(
        "borosil-product-card[data-product-card='true']"
    )


def extract_product_urls(selector):
    urls = []
    seen = set()

    for card in extract_product_cards(selector):
        href = card.css(
            "a[href*='/products/']::attr(href)"
        ).get()

        product_url = clean_product_url(href)

        if product_url and product_url not in seen:
            seen.add(product_url)
            urls.append(product_url)

    return urls


def find_product_card(product_url, selector):
    if not product_url or selector is None:
        return None

    for card in extract_product_cards(selector):
        href = card.css(
            "a[href*='/products/']::attr(href)"
        ).get()

        if clean_product_url(href) == product_url:
            return card

    return None


def extract_product_name(selector, card=None, product_data=None):
    if product_data:
        name = clean_text(product_data.get("title"))
        if name != "N/A":
            return name

    for obj in (card, selector):
        if obj is None:
            continue

        for css in (
            ".product__card__title",
            "a.product-card__title",
            "h1.product__title",
            "h1",
        ):
            for element in obj.css(css):
                name = extract_text(element)
                if name != "N/A":
                    return name

    return "N/A"


def normalize_image_url(url):
    if not url:
        return None

    url = str(url).strip()
    if not url or url.startswith("data:"):
        return None

    if url.startswith("//"):
        url = "https:" + url
    else:
        url = urljoin("https://myborosil.com", url)

    try:
        parsed = urlsplit(url)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    allowed_host = (
        host == "myborosil.com"
        or host.endswith(".myborosil.com")
        or host == "cdn.shopify.com"
    )

    if not allowed_host:
        return None

    rejected_terms = (
        "logo", "favicon", "placeholder", "no-image",
        "no_image", "tracking", "pixel", "sprite", "icon",
    )

    if any(term in path for term in rejected_terms):
        return None

    if not re.search(
        r"\.(?:jpe?g|png|webp|avif|gif)(?:$|[?#])",
        url,
        re.IGNORECASE,
    ) and not any(
        marker in path
        for marker in (
            "/cdn/shop/files/",
            "/cdn/shop/products/",
            "/s/files/",
        )
    ):
        return None

    return url


def extract_image_from_card(card, product_data=None):
    if card is not None:
        for image in card.css(
            ".product_image img.product-card-image, "
            ".product_image img, .primary-image img"
        ):
            for attr in (
                "data-src",
                "data-original",
                "data-lazy-src",
                "src",
                "data-srcset",
                "srcset",
            ):
                value = image.attrib.get(attr)
                if not value:
                    continue

                if "srcset" in attr:
                    candidates = []

                    for item in value.split(","):
                        parts = item.strip().split()

                        if parts:
                            candidate = normalize_image_url(parts[0])

                            if candidate:
                                width = (
                                    extract_number(parts[1])
                                    if len(parts) > 1 else 0
                                )
                                candidates.append((width or 0, candidate))

                    if candidates:
                        return sorted(candidates, reverse=True)[0][1]

                else:
                    candidate = normalize_image_url(value)
                    if candidate:
                        return candidate

    if product_data:
        images = product_data.get("images") or []

        if isinstance(images, str):
            images = [images]

        for image in images:
            candidate = normalize_image_url(image)
            if candidate:
                return candidate

        candidate = normalize_image_url(
            product_data.get("featured_image")
        )

        if candidate:
            return candidate

    return None


def extract_json_ld_product(selector):
    if selector is None:
        return None

    for script_text in selector.css(
        "script[type='application/ld+json']::text"
    ).getall():
        try:
            data = json.loads(script_text)
        except (json.JSONDecodeError, TypeError):
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            if isinstance(obj.get("@graph"), list):
                objects.extend(obj["@graph"])

            object_type = obj.get("@type")

            if object_type == "Product" or (
                isinstance(object_type, list)
                and "Product" in object_type
            ):
                return obj

    return None


def extract_detail_product_json(selector):
    if selector is None:
        return None

    for script_text in selector.css(
        "script.variant-data::text"
    ).getall():
        try:
            data = json.loads(script_text.strip())

            if isinstance(data, dict) and data.get("id"):
                return data

        except (json.JSONDecodeError, TypeError):
            continue

    return extract_json_ld_product(selector)


def extract_description(product_data, selector):
    if product_data:
        description = (
            product_data.get("description")
            or product_data.get("content")
        )

        if description:
            return clean_text(
                Selector(text=str(description))
                .xpath("string(.)").get()
            )

    if selector is not None:
        for css in (
            ".product__description",
            ".product-description",
            "[class*='product-description']",
            ".rte",
        ):
            values = [
                extract_text(element)
                for element in selector.css(css)
            ]

            values = [
                value for value in values
                if value != "N/A"
            ]

            if values:
                return clean_text(
                    " ".join(dict.fromkeys(values))
                )

        meta_description = selector.css(
            "meta[name='description']::attr(content)"
        ).get()

        if meta_description:
            return clean_text(meta_description)

    return "N/A"


def extract_prices(product_data, selector=None):
    sale_price = None
    original_price = None

    if product_data:
        sale_price = shopify_amount(
            product_data.get("price")
            if product_data.get("price") is not None
            else product_data.get("price_min")
        )

        original_price = shopify_amount(
            product_data.get("compare_at_price")
            if product_data.get("compare_at_price") is not None
            else product_data.get("compare_at_price_min")
        )

        if sale_price is None:
            offers = product_data.get("offers")

            if isinstance(offers, list):
                offers = offers[0] if offers else None

            if isinstance(offers, dict):
                sale_price = extract_number(
                    offers.get("price")
                )

    if selector is not None and (
        sale_price is None or original_price is None
    ):
        if sale_price is None:
            for css in (
                ".price__sale .price-item--sale",
                ".price-item--sale",
                ".price__current",
                ".product__price",
                "[class*='sale-price']",
            ):
                value = selector.css(css).xpath(
                    "string(.)"
                ).get()

                sale_price = extract_number(value)

                if sale_price is not None:
                    break

        if original_price is None:
            for css in (
                ".price__compare",
                ".price-item--regular",
                "s.price-item",
                "del",
                "[class*='compare-at']",
            ):
                value = selector.css(css).xpath(
                    "string(.)"
                ).get()

                original_price = extract_number(value)

                if original_price is not None:
                    break

    return sale_price, original_price


def extract_rating(product_data, selector):
    # First priority: Judge.me rating from the detail page.
    if selector is not None:
        for css in (
            ".jdgm-prev-badge::attr(data-average-rating)",
            "[data-average-rating]::attr(data-average-rating)",
            "[itemprop='ratingValue']::attr(content)",
        ):
            value = selector.css(css).get()
            rating = extract_number(value)

            if rating is not None and 0 <= float(rating) <= 5:
                return rating

    # Second priority: JSON rating.
    if product_data:
        aggregate = product_data.get("aggregateRating")

        if isinstance(aggregate, dict):
            rating = extract_number(
                aggregate.get("ratingValue")
            )

            if rating is not None and 0 <= float(rating) <= 5:
                return rating

    # Third priority: Other rating elements.
    if selector is not None:
        for css in (
            ".jdgm-prev-badge__text",
            ".rating-value",
            ".rating",
            "[class*='rating-value']",
        ):
            value = selector.css(css).get()

            if value is None:
                value = selector.css(css).xpath(
                    "string(.)"
                ).get()

            rating = extract_number(value)

            if rating is not None and 0 <= float(rating) <= 5:
                return rating

    return "N/A"


class BorosilAppliancesSpider(scrapy.Spider):

    name = "borosil_appliances"

    allowed_domains = [
        "myborosil.com",
        "cdn.shopify.com",
    ]

    custom_settings = {
        "DOWNLOAD_TIMEOUT": 120,
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.all_products = []
        self.seen_product_urls = set()

    async def fetch_page(self, crawler, url):
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                logger.info(
                    "Fetching page (%s/%s): %s",
                    attempt,
                    RETRY_COUNT,
                    url,
                )

                result = await crawler.arun(url=url)

                if not result:
                    raise RuntimeError(
                        "Empty Crawl4AI result"
                    )

                html = getattr(result, "html", None)

                if not html:
                    raise RuntimeError(
                        "Empty HTML response"
                    )

                return html

            except Exception as exc:
                logger.warning(
                    "Fetch failed for %s: %s",
                    url,
                    exc,
                )

                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY)

        return None

    async def scrape_product(
        self,
        crawler,
        product_url,
        listing_card,
        category,
    ):
        detail_html = await self.fetch_page(
            crawler,
            product_url,
        )

        detail_selector = create_selector(detail_html)

        if detail_selector is None:
            logger.warning(
                "Skipping product; detail page unavailable: %s",
                product_url,
            )
            return None

        detail_json = extract_detail_product_json(
            detail_selector
        )

        card_json = extract_borosil_json(listing_card)

        product_data = detail_json or card_json

        name = extract_product_name(
            detail_selector,
            listing_card,
            product_data,
        )

        if name == "N/A":
            logger.warning(
                "Product name missing: %s",
                product_url,
            )
            return None

        sale_price, original_price = extract_prices(
            product_data,
            detail_selector,
        )

        if sale_price is None or original_price is None:
            card_sale, card_original = extract_prices(
                card_json
            )

            if sale_price is None:
                sale_price = card_sale

            if original_price is None:
                original_price = card_original

        if sale_price is None:
            logger.warning(
                "Product price missing: %s",
                product_url,
            )
            return None

        if (
            original_price is not None
            and sale_price > original_price
        ):
            sale_price, original_price = (
                original_price,
                sale_price,
            )

        image_link = extract_image_from_card(
            listing_card,
            card_json,
        )

        if not image_link and product_data:
            image_link = extract_image_from_card(
                None,
                product_data,
            )

        if not image_link:
            image_link = extract_image_from_card(
                None,
                detail_json if isinstance(detail_json, dict)
                else None,
            )

        if not image_link:
            logger.warning(
                "Product image missing: %s",
                product_url,
            )
            return None

        description = extract_description(
            product_data,
            detail_selector,
        )

        rating = extract_rating(
            product_data,
            detail_selector,
        )

        discount = calculate_discount(
            original_price,
            sale_price,
        )

        timestamp = datetime.now(
            timezone(timedelta(hours=5, minutes=30))
        ).strftime("%Y-%m-%dT%H:%M:%S")

        return {
            "name": name,
            "price": sale_price,
            "currency": "₹",
            "original_price": (
                original_price
                if original_price is not None
                else "N/A"
            ),
            "discount": discount,
            "ratings": rating,
            "description": description,
            "image_link": image_link,
            "product_link": clean_product_url(product_url),
            "organization_id": ORGANIZATION_ID,
            "store_id": STORE_ID,
            "categories_id": category,
            "created_at": timestamp,
        }

    async def scrape_collection(
        self,
        crawler,
        category,
        base_url,
    ):
        collected = []

        for page_number in range(1, MAX_PAGES + 1):
            separator = "&" if "?" in base_url else "?"

            page_url = (
                base_url
                if page_number == 1
                else f"{base_url}{separator}page={page_number}"
            )

            logger.info(
                "Scraping category: %s | Page %s/%s",
                category,
                page_number,
                MAX_PAGES,
            )

            html = await self.fetch_page(
                crawler,
                page_url,
            )

            selector = create_selector(html)

            if selector is None:
                logger.warning(
                    "Could not parse collection page: %s",
                    page_url,
                )
                continue

            product_urls = extract_product_urls(selector)

            logger.info(
                "[%s] Product URLs found on page %s: %s",
                category,
                page_number,
                len(product_urls),
            )

            if not product_urls:
                logger.info(
                    "[%s] No product URLs found. Pagination finished.",
                    category,
                )
                break

            new_urls = [
                url for url in product_urls
                if url not in self.seen_product_urls
            ]

            if not new_urls:
                logger.info(
                    "[%s] No new products on page %s. Stopping.",
                    category,
                    page_number,
                )
                break

            page_total = len(new_urls)

            for product_index, product_url in enumerate(
                new_urls,
                start=1,
            ):
                self.seen_product_urls.add(product_url)

                card = find_product_card(
                    product_url,
                    selector,
                )

                logger.info(
                    "[%s] Page %s | Product (%s/%s): %s",
                    category,
                    page_number,
                    product_index,
                    page_total,
                    product_url,
                )

                product = await self.scrape_product(
                    crawler,
                    product_url,
                    card,
                    category,
                )

                if product:
                    collected.append(product)
                    self.all_products.append(product)

                    logger.info(
                        "[%s] Added (%s/%s) %s | Price: %s | Original: %s",
                        category,
                        product_index,
                        page_total,
                        product["name"],
                        product["price"],
                        product["original_price"],
                    )
                else:
                    logger.warning(
                        "[%s] Skipped (%s/%s): %s",
                        category,
                        product_index,
                        page_total,
                        product_url,
                    )

            logger.info(
                "[%s] Page %s completed. Category products collected: %s",
                category,
                page_number,
                len(collected),
            )

        return collected

    async def _scrape_all(self):
        self.logger.info("Starting Borosil scraper.")

        crawler = AsyncWebCrawler(verbose=True)

        crawler.browser_config = {
            "headless": True,
            "javascript": True,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

        crawler.crawler_run_config = {
            "wait_until": "networkidle",
            "timeout": PAGE_TIMEOUT,
            "delay_before_return_html": 2,
            "wait_for": {
                "selector": (
                    "borosil-product-card[data-product-card='true']"
                ),
                "timeout": 30000,
            },
        }

        category_totals = {}

        try:
            try:
                await crawler.start()
            except AttributeError:
                pass

            for category, urls in BASE_URLS.items():
                category_start_count = len(self.all_products)

                self.logger.info("=" * 65)
                self.logger.info("STARTING CATEGORY: %s", category)
                self.logger.info("=" * 65)

                for base_url in urls:
                    try:
                        await self.scrape_collection(
                            crawler,
                            category,
                            base_url,
                        )
                    except Exception as exc:
                        self.logger.exception(
                            "Collection failed (%s): %s",
                            base_url,
                            exc,
                        )

                category_totals[category] = (
                    len(self.all_products) - category_start_count
                )

                self.logger.info(
                    "CATEGORY COMPLETED | %s | Products added: %s",
                    category,
                    category_totals[category],
                )

        finally:
            try:
                await crawler.close()
            except Exception:
                self.logger.exception(
                    "Error while closing Crawl4AI crawler."
                )

        # Remove duplicate product links.
        unique_products = []
        seen = set()

        for product in self.all_products:
            key = product.get("product_link")

            if key and key not in seen:
                seen.add(key)
                unique_products.append(product)

        OUTPUT_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                unique_products,
                file,
                ensure_ascii=False,
                indent=4,
            )

        # Final category-wise summary.
        self.logger.info("")
        self.logger.info("=" * 65)
        self.logger.info("BOROSIL CATEGORY-WISE SUMMARY")
        self.logger.info("=" * 65)

        for category, count in category_totals.items():
            self.logger.info(
                "%-25s : %s products added",
                category,
                count,
            )

        self.logger.info("-" * 65)
        self.logger.info(
            "TOTAL UNIQUE PRODUCTS : %s",
            len(unique_products),
        )
        self.logger.info(
            "JSON OUTPUT           : %s",
            OUTPUT_FILE,
        )
        self.logger.info("=" * 65)

        return unique_products

    def _run_crawl4ai(self):
        # Run Playwright on its own Windows Proactor event loop.
        return asyncio.run(self._scrape_all())

    async def start(self):
        self.logger.info(
            "Starting Crawl4AI in a separate thread."
        )

        products = await asyncio.to_thread(
            self._run_crawl4ai
        )

        for product in products:
            yield product


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [BOROSIL] %(levelname)s %(message)s",
    )

    from scrapy.crawler import CrawlerProcess

    process = CrawlerProcess()
    process.crawl(BorosilAppliancesSpider)
    process.start()
