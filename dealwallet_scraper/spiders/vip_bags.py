import html
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import scrapy
from bs4 import BeautifulSoup


class VipbagsSkybagsSpider(scrapy.Spider):
    name = "vipbags_skybags"
    allowed_domains = ["vipbags.com", "www.vipbags.com"]

    start_urls = [
        "https://vipbags.com/skybags/collections/skybags-hard-luggage",
        "https://vipbags.com/skybags/collections/skybags-soft-luggage",
        "https://vipbags.com/skybags/collections/printed-trolley-bags",
        "https://vipbags.com/skybags/collections/skybags-trolley-bag-sets",
        "https://vipbags.com/skybags/collections/skybags-backpacks",
        "https://vipbags.com/skybags/collections/skybags-duffles",
        "https://vipbags.com/skybags/collections/backpacks-from-999"
    ]

    BASE_URL = "https://vipbags.com"
    MAX_PAGES = 20

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1.0,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "RETRY_TIMES": 3,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        },
    }

    def __init__(self, max_pages=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if max_pages is not None:
            try:
                self.MAX_PAGES = max(1, int(max_pages))
            except (TypeError, ValueError):
                self.logger.warning(
                    "Invalid max_pages=%r; using %s", max_pages, self.MAX_PAGES
                )
        self.seen_handles = set()
        self.total_items = 0

    @staticmethod
    def clean_text(value):
        if value is None:
            return None
        value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", html.unescape(value)).strip()
        return value or None

    @staticmethod
    def money(value, cents=False):
        """Convert a Shopify money value to a number; Shopify JSON prices are paise/cents."""
        if value is None or value == "":
            return None
        try:
            amount = Decimal(str(value))
            if cents:
                amount = amount / Decimal("100")
            if not amount.is_finite() or amount < 0:
                return None
            return int(amount) if amount == amount.to_integral_value() else float(amount)
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def image_url(value):
        if not value:
            return None
        value = html.unescape(str(value)).strip()
        if value.startswith("data:"):
            return None
        if value.startswith("//"):
            return "https:" + value
        return urljoin("https://vipbags.com", value)

    @staticmethod
    def current_ist_timestamp():
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S")

    def parse(self, response):
        page_number = response.meta.get("page_number", 1)
        self.logger.info(
            "[LISTING PAGE] page=%s status=%s url=%s",
            page_number, response.status, response.url
        )

        cards = response.css(".ui-collection__products-grid a.ui-product-card")
        if not cards:
            # Fallback in case the collection-card class changes slightly.
            cards = response.css("[data-products-grid] a[data-product-handle]")

        self.logger.info("[PRODUCT CARDS] page=%s count=%s", page_number, len(cards))
        new_items_on_page = 0

        for card in cards:
            handle = (card.attrib.get("data-product-handle") or "").strip()
            if not handle:
                href = card.attrib.get("href", "")
                match = re.search(r"/products/([^/?#]+)", href)
                handle = match.group(1) if match else ""
            if not handle or handle in self.seen_handles:
                continue

            # The wishlist element may contain a full Shopify product JSON object.
            raw_json = card.css(".wishlist-icon-button::attr(data-product-json)").get()
            product_data = {}
            if raw_json:
                try:
                    product_data = json.loads(html.unescape(raw_json))
                except (json.JSONDecodeError, TypeError) as exc:
                    self.logger.warning(
                        "Could not parse embedded product JSON for %s: %s", handle, exc
                    )

            title = (
                product_data.get("title")
                or card.css(".ui-product-card__title::text").get()
                or card.css("img::attr(alt)").get()
            )
            title = self.clean_text(title)
            if not title:
                self.logger.warning("Skipping product with no title: handle=%s", handle)
                continue

            href = card.attrib.get("href")
            product_link = (
                urljoin(response.url, html.unescape(href))
                if href
                else urljoin(self.BASE_URL, f"/skybags/products/{handle}")
            )
            # Remove tracking and variant query parameters for a canonical URL.
            parsed_link = urlparse(product_link)
            product_link = f"{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}"

            current_price = self.money(product_data.get("price"), cents=True)
            if current_price is None:
                current_price = self.money(card.attrib.get("data-product-price"))
            if current_price is None:
                current_price = self.money(
                    card.css(".ui-product-card__price-current::text").get()
                )

            original_price = self.money(
                product_data.get("compare_at_price"), cents=True
            )
            if original_price is None:
                original_price = self.money(
                    card.css(".ui-product-card__price-compare::text").get()
                )

            if (
                original_price is not None
                and current_price is not None
                and original_price > current_price
                and original_price > 0
            ):
                discount = round(
                    (original_price - current_price) / original_price * 100
                )
            else:
                discount = 0

            if not discount:
                raw_discount = card.attrib.get("data-discount-percent", "")
                discount_match = re.search(r"\d+", raw_discount)
                if discount_match:
                    discount = int(discount_match.group())

            images = product_data.get("images") or []
            image_link = self.image_url(
                product_data.get("featured_image")
                or (images[0] if images else None)
                or card.css(".ui-product-card__image img::attr(src)").get()
                or card.css("img::attr(data-src)").get()
                or card.css("img::attr(srcset)").get()
            )

            description = self.clean_text(product_data.get("description"))

            # Keep the same 13-field schema used by the other DealWallet spiders.
            item = {
                "name": title,
                "price": current_price,
                "currency": "₹",
                "original_price": original_price,
                "discount": int(discount),
                "ratings": None,
                "description": description or title,
                "image_link": image_link,
                "product_link": product_link,
                "organization_id": "Dealwallet",
                "store_id": "VIP Bags",
                "categories_id": "Bags",
                "created_at": self.current_ist_timestamp(),
            }

            self.seen_handles.add(handle)
            self.total_items += 1
            new_items_on_page += 1

            # Do not access item["available"]: it is intentionally not part of this schema.
            self.logger.info(
                "[ITEM] %s | price=%s | original=%s | discount=%s%% | image=%s",
                item["name"],
                item["price"],
                item["original_price"],
                item["discount"],
                bool(item["image_link"]),
            )
            yield item

        self.logger.info(
            "[PAGE SUMMARY] page=%s cards=%s new_products=%s total_unique=%s",
            page_number, len(cards), new_items_on_page, self.total_items
        )

        if page_number < self.MAX_PAGES and cards and new_items_on_page:
            next_page = page_number + 1
            next_url = response.url.split("?")[0] + f"?page={next_page}"
            self.logger.info("[PAGINATION] Requesting page %s: %s", next_page, next_url)
            yield scrapy.Request(
                next_url,
                callback=self.parse,
                errback=self.page_error,
                meta={"page_number": next_page},
                dont_filter=True,
            )
        else:
            self.logger.info(
                "[PAGINATION STOP] page=%s max_pages=%s cards=%s new_products=%s",
                page_number, self.MAX_PAGES, len(cards), new_items_on_page
            )

    def page_error(self, failure):
        self.logger.error("[REQUEST FAILED] %s", failure.request.url)
        self.logger.error("Failure details: %s", failure.value)

    def closed(self, reason):
        self.logger.info("=" * 60)
        self.logger.info("VIPBAGS Skybags scraper finished: reason=%s", reason)
        self.logger.info("Unique products scraped: %s", self.total_items)
        self.logger.info("=" * 60)
