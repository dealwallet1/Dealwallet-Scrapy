
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import scrapy


class PetShopIndiaSpider(scrapy.Spider):
    name = "petshopindia"
    allowed_domains = ["petshopindia.com"]

    start_urls = [
        "https://petshopindia.com/index.php?route=product/category&language=en-gb&path=59",
        "https://petshopindia.com/index.php?route=product/category&language=en-gb&path=60",
    ]

    MAX_PRODUCTS_PER_URL = 100

    organization_name = "Dealwallet"
    store_name = "petshopindia"
    category_name = "Pet Essentials"

    custom_settings = {
    "CONCURRENT_REQUESTS": 2,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    "DOWNLOAD_DELAY": 2,
    "DOWNLOAD_TIMEOUT": 60,
    "RETRY_ENABLED": True,
    "RETRY_TIMES": 5,
    "RETRY_HTTP_CODES": [408, 429, 500, 502, 503, 504],
    "FEEDS": {
        "petshopindia_products.json": {
            "format": "json",
            "encoding": "utf-8",
            "overwrite": True,
        }
    },
    "FEED_EXPORT_ENCODING": "utf-8",
}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.products_scheduled = {}
        self.products_scraped = {}
        self.products_skipped = {}
        self.pages_visited = {}
        self.seen_category_urls = set()
        self.seen_product_urls = set()

        for url in self.start_urls:
            category_path = self.get_category_path(url)

            self.products_scheduled[category_path] = 0
            self.products_scraped[category_path] = 0
            self.products_skipped[category_path] = 0
            self.pages_visited[category_path] = 0

    @staticmethod
    def extract_text(values):
        return " ".join(" ".join(values).split()).strip()

    @staticmethod
    def get_category_path(url):
        query = parse_qs(urlparse(url).query)
        return query.get("path", ["unknown"])[0].strip().rstrip("+")

    @staticmethod
    def parse_price(value):
        if not value:
            return None

        cleaned = re.sub(r"[^\d.]", "", value.replace(",", ""))

        try:
            amount = float(cleaned)
            if amount <= 0:
                return None

            # Store prices as integers, as requested.
            return int(amount)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_discount(value):
        if not value:
            return None

        match = re.search(r"(\d+(?:\.\d+)?)\s*%", value)

        if not match:
            return None

        try:
            return float(match.group(1))
        except (ValueError, TypeError):
            return None

    def start_requests(self):
        for url in self.start_urls:
            category_path = self.get_category_path(url)

            yield scrapy.Request(
                url=url,
                callback=self.parse,
                errback=self.category_request_failed,
                meta={"category_path": category_path},
            )

    def parse(self, response):
        category_path = response.meta.get(
            "category_path",
            self.get_category_path(response.url),
        )

        if response.url in self.seen_category_urls:
            return

        self.seen_category_urls.add(response.url)
        self.pages_visited[category_path] += 1

        self.logger.info(
            "[Category %s] Page %s: %s",
            category_path,
            self.pages_visited[category_path],
            response.url,
        )

        product_cards = response.css(".products-list .product-layout")

        self.logger.info(
            "[Category %s] Found %s product cards.",
            category_path,
            len(product_cards),
        )

        for card in product_cards:
            if (
                self.products_scheduled[category_path]
                >= self.MAX_PRODUCTS_PER_URL
            ):
                self.logger.info(
                    "[Category %s] Reached the limit of %s products.",
                    category_path,
                    self.MAX_PRODUCTS_PER_URL,
                )
                break

            product_url = (
                card.css(
                    ".product-image-container a::attr(href)"
                ).get()
                or card.css(".right-block h4 a::attr(href)").get()
            )

            if not product_url:
                self.products_skipped[category_path] += 1
                self.logger.info(
                    "[Category %s] Skipped card: product URL missing.",
                    category_path,
                )
                continue

            product_url = response.urljoin(product_url)

            # Avoid duplicate products within the same spider run.
            if product_url in self.seen_product_urls:
                continue

            name = self.extract_text(
                card.css(".right-block h4 a::text").getall()
            )

            if not name:
                name = (
                    card.css(
                        ".product-image-container img::attr(title)"
                    ).get()
                    or ""
                ).strip()

            current_price_text = self.extract_text(
                card.css(".price .price-new::text").getall()
            )

            original_price_text = self.extract_text(
                card.css(".price .price-old::text").getall()
            )

            discount_text = self.extract_text(
                card.css(".box-label .label-sale::text").getall()
            )

            current_price = self.parse_price(current_price_text)
            original_price = self.parse_price(original_price_text)
            discount = self.parse_discount(discount_text)

            # Validate all required fields before requesting the detail page.
            if current_price is None:
                self.products_skipped[category_path] += 1
                self.logger.info(
                    "[Category %s] Skipped '%s': invalid current price.",
                    category_path,
                    name or product_url,
                )
                continue

            if original_price is None:
                self.products_skipped[category_path] += 1
                self.logger.info(
                    "[Category %s] Skipped '%s': original price missing "
                    "or invalid.",
                    category_path,
                    name or product_url,
                )
                continue

            if discount is None or discount <= 0 or discount >= 100:
                self.products_skipped[category_path] += 1
                self.logger.info(
                    "[Category %s] Skipped '%s': invalid discount %r.",
                    category_path,
                    name or product_url,
                    discount_text,
                )
                continue

            if original_price <= current_price:
                self.products_skipped[category_path] += 1
                self.logger.info(
                    "[Category %s] Skipped '%s': original price "
                    "must exceed current price.",
                    category_path,
                    name or product_url,
                )
                continue

            image_url = (
                card.css(
                    ".product-image-container img::attr(data-src)"
                ).get()
                or card.css(
                    ".product-image-container img::attr(src)"
                ).get()
            )

            image_url = (
                response.urljoin(image_url) if image_url else None
            )

            self.seen_product_urls.add(product_url)
            self.products_scheduled[category_path] += 1

            self.logger.info(
                "[Category %s] Scheduling %s/%s: %s | "
                "Price: %s | Original: %s | Discount: %s%%",
                category_path,
                self.products_scheduled[category_path],
                self.MAX_PRODUCTS_PER_URL,
                name or product_url,
                current_price,
                original_price,
                discount,
            )

            yield scrapy.Request(
                url=product_url,
                callback=self.parse_product,
                errback=self.product_request_failed,
                meta={
                    "category_path": category_path,
                    "name": name,
                    "price": int(current_price),
                    "original_price": int(original_price),
                    "discount": int(discount),
                    "image_link": image_url,
                    "product_link": product_url,
                },
                dont_filter=True,
            )

        # Follow pagination for this category only.
        if (
            self.products_scheduled[category_path]
            < self.MAX_PRODUCTS_PER_URL
        ):
            pagination_links = response.css(
                "ul.pagination a::attr(href), "
                ".pagination a::attr(href)"
            ).getall()

            for href in pagination_links:
                next_url = response.urljoin(href)
                parsed = urlparse(next_url)
                query = parse_qs(parsed.query)

                if query.get("route", [""])[0] != "product/category":
                    continue

                next_path = query.get("path", [""])[0].strip().rstrip("+")

                if next_path != category_path:
                    continue

                if next_url in self.seen_category_urls:
                    continue

                yield scrapy.Request(
                    url=next_url,
                    callback=self.parse,
                    errback=self.category_request_failed,
                    meta={"category_path": category_path},
                )
                break

    def parse_product(self, response):
        category_path = response.meta["category_path"]

        description = self.extract_text(
            response.css(
                "#tab-description #collapse-description ::text"
            ).getall()
        )

        name = response.meta.get("name")

        if not name:
            name = self.extract_text(
                response.css("h1::text").getall()
            )

        item = {
            "name": name,
            "price": response.meta["price"],
            "currency": "INR",
            "original_price": response.meta["original_price"],
            "discount": response.meta["discount"],
            "ratings": None,
            "description": description,
            "image_link": response.meta.get("image_link"),
            "product_link": response.meta.get("product_link"),
            "organization_id": self.organization_name,
            "store_id": self.store_name,
            "categories_id": self.category_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "affiliate_url": None,
        }

        self.products_scraped[category_path] += 1

        self.logger.info(
            "[Category %s] Scraped %s/%s: %s",
            category_path,
            self.products_scraped[category_path],
            self.MAX_PRODUCTS_PER_URL,
            name,
        )

        yield item

    def product_request_failed(self, failure):
        category_path = failure.request.meta.get(
            "category_path", "unknown"
        )

        self.logger.error(
            "[Category %s] Product request failed: %s | %s",
            category_path,
            failure.request.url,
            failure.value,
        )

    def category_request_failed(self, failure):
        self.logger.error(
            "Category request failed: %s | %s",
            failure.request.url,
            failure.value,
        )

    def closed(self, reason):
        for category_path in self.products_scheduled:
            self.logger.info(
                "Pet Shop India category %s summary | "
                "Pages visited: %s | Products scheduled: %s | "
                "Products scraped: %s | Products skipped: %s",
                category_path,
                self.pages_visited[category_path],
                self.products_scheduled[category_path],
                self.products_scraped[category_path],
                self.products_skipped[category_path],
            )

        self.logger.info(
            "Pet Shop India spider closed. Reason: %s",
            reason,
        )
