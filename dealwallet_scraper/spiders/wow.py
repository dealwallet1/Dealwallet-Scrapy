import asyncio
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin

import scrapy
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig


BASE_URLS = {
    "Beauty": [
        "https://www.buywow.in/collections/ultimate-combos",
        "https://www.buywow.in/collections/body-lotion",
        "https://www.buywow.in/collections/body-wash",
        "https://www.buywow.in/collections/hair-oil",
        "https://www.buywow.in/collections/hair-care-kit",
        "https://www.buywow.in/collections/hair-shampoo",
        "https://www.buywow.in/collections/hair-conditioner",
        "https://www.buywow.in/collections/hair-serum",
    ],
    "Makeup": [
        "https://www.buywow.in/collections/face-wash-men-and-women",
        "https://www.buywow.in/collections/face-scrub",
        "https://www.buywow.in/collections/face-serum",
        "https://www.buywow.in/collections/face-cream",
        "https://www.buywow.in/collections/face-mask-and-peels",
        "https://www.buywow.in/collections/lip-care",
        "https://www.buywow.in/collections/face-skin-toner",
        "https://www.buywow.in/collections/natural-kajal",
        "https://www.buywow.in/collections/skin-face-moisturizer",
        "https://www.buywow.in/collections/skin-care-combo",
        "https://www.buywow.in/collections/sunscreen",
    ],
}


MAX_PAGES = 1
BASE_DOMAIN = "https://www.buywow.in"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    value = value.strip()

    return value if value else None


def calculate_discount(price, original_price):
    if price is None or original_price is None:
        return None

    if original_price <= 0:
        return None

    if original_price <= price:
        return 0

    return round(
        ((original_price - price) / original_price) * 100,
        
    )


def parse_price_to_float(price_str):
    if not price_str:
        return None

    if price_str in ("N/A", "-", ""):
        return None

    clean = re.sub(r"[^\d.]", "", str(price_str))

    if not clean:
        return None

    try:
        value = float(clean)

        if value.is_integer():
            return int(value)

        return value

    except ValueError:
        return None


def parse_rating_to_float(rating_str):
    if not rating_str or rating_str == "N/A":
        return None

    match = re.search(r"\d+(?:\.\d+)?", str(rating_str))

    if not match:
        return None

    try:
        return float(match.group())
    except ValueError:
        return None


# ============================================================
# PRODUCT EXTRACTION
# ============================================================

def extract_product_name(card):
    product_links = card.select('a[href^="/products/"]')

    for link in product_links:
        text = clean_text(link.get_text(" ", strip=True))

        if text:
            return text

    return "N/A"


def extract_product_link(card):
    product_links = card.select('a[href^="/products/"]')

    if not product_links:
        return "N/A"

    href = product_links[0].get("href")

    if not href:
        return "N/A"

    return urljoin(BASE_DOMAIN, href)


def extract_prices(card):
    price_container = card.select_one(
        "div.flex.items-center.gap-2.mb-4"
    )

    if not price_container:
        return None, None

    price_spans = price_container.select("span")

    if not price_spans:
        return None, None

    current_price = parse_price_to_float(
        price_spans[0].get_text(" ", strip=True)
    )

    original_price = None

    for span in price_spans[1:]:
        classes = span.get("class", [])

        if "line-through" in classes:
            original_price = parse_price_to_float(
                span.get_text(" ", strip=True)
            )
            break

    return current_price, original_price


def extract_image(card):
    img_tag = card.select_one("picture img")

    if not img_tag:
        return "N/A"

    image_url = (
        img_tag.get("src")
        or img_tag.get("data-src")
    )

    if not image_url:
        srcset = img_tag.get("srcset")

        if srcset:
            image_url = srcset.split(",")[0].strip()
            image_url = image_url.split(" ")[0]

    if not image_url or image_url.startswith("data:"):
        return "N/A"

    return urljoin(BASE_DOMAIN, image_url)


def extract_rating(card):
    rating_tag = card.select_one(
        "div.flex.items-center.gap-1 "
        "span.text-sm.font-medium.text-neutral-600"
    )

    if not rating_tag:
        return None

    return parse_rating_to_float(
        rating_tag.get_text(" ", strip=True)
    )


# ============================================================
# DETAIL PAGE DESCRIPTION
# ============================================================

async def extract_product_details(crawler, product_link):
    if not product_link or product_link == "N/A":
        return "N/A"

    detail_config = CrawlerRunConfig(
        wait_until="domcontentloaded",
        page_timeout=60000,
        delay_before_return_html=2.0,
    )

    try:
        print(f"[DETAIL] Opening: {product_link}")

        product_page = await crawler.arun(
            url=product_link,
            config=detail_config,
        )

        if not product_page.success:
            print(f"[DETAIL FAILED] {product_link}")
            return "N/A"

        if not product_page.html:
            print(f"[DETAIL EMPTY] {product_link}")
            return "N/A"

        psoup = BeautifulSoup(
            product_page.html,
            "html.parser",
        )

        desc = psoup.select_one(
            "p.text-sm.text-neutral-600.mb-3.line-clamp-2"
        )

        if not desc:
            print("[DESCRIPTION NOT FOUND]")
            return "N/A"

        description = clean_text(
            desc.get_text(" ", strip=True)
        )

        return description or "N/A"

    except Exception as exc:
        print(
            f"[DETAIL ERROR] {product_link} -> {exc}"
        )
        return "N/A"


# ============================================================
# PRODUCT CARD
# ============================================================

async def parse_product_card(crawler, card, category):
    try:
        name = extract_product_name(card)

        if not name or name == "N/A":
            return None

        if name.strip().lower() == "out of stock":
            print("   ⏭️ SKIPPED - Out of Stock")
            return None

        product_link = extract_product_link(card)

        price_val, original_val = extract_prices(card)

        print()
        print(f"[PRODUCT] {name}")
        print(f"   Price          : {price_val}")
        print(f"   Original Price : {original_val}")

        if price_val is None:
            print("   ⏭️ SKIPPED - current price missing")
            return None

        if original_val is None:
            print("   ⏭️ SKIPPED - no discount")
            return None

        if original_val <= price_val:
            print("   ⏭️ SKIPPED - no valid discount")
            return None

        discount = calculate_discount(
            price_val,
            original_val,
        )

        if discount is None or discount <= 0:
            print("   ⏭️ SKIPPED - discount is not valid")
            return None

        print(f"   Discount       : {discount}%")

        image_link = extract_image(card)
        rating = extract_rating(card)

        # Keep detail-page description scraping.
        description = "N/A"

        if product_link != "N/A":
            detail_description = await extract_product_details(
                crawler,
                product_link,
            )

            if detail_description and detail_description != "N/A":
                description = detail_description
        
            

        product = {
            "name": name,
            "price": price_val,
            "currency": "₹",
            "original_price": original_val,
            "discount": discount,
            "ratings": rating,
            "description": description,
            "image_link": image_link,
            "product_link": product_link,
            "organization_id": "Dealwallet",
            "store_id": "BuyWow",
            "categories_id": category,
        }

        print("   ✅ Product added")

        return product

    except Exception as exc:
        print(f"[PRODUCT ERROR] {exc}")
        return None


# ============================================================
# ASYNC SCRAPER
# ============================================================

async def scrape_buywow_async():
    results = []

    seen_names = set()
    seen_links = set()

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        user_agent=USER_AGENT,
        verbose=True,
    )

    print()
    print("=" * 60)
    print("BUYWOW SCRAPER STARTED")
    print("=" * 60)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for category, urls in BASE_URLS.items():
            print()
            print("=" * 60)
            print(f"CATEGORY: {category}")
            print("=" * 60)

            for base_url in urls:
                page_num = 1

                while page_num <= MAX_PAGES:
                    if page_num == 1:
                        page_url = base_url
                    else:
                        separator = "&" if "?" in base_url else "?"
                        page_url = (
                            f"{base_url}"
                            f"{separator}"
                            f"page={page_num}"
                        )

                    print()
                    print(f"[PAGE] {page_url}")

                    run_config = CrawlerRunConfig(
                        wait_until="domcontentloaded",
                        page_timeout=60000,
                        delay_before_return_html=2.0,
                    )

                    try:
                        page = await crawler.arun(
                            url=page_url,
                            config=run_config,
                        )
                    except Exception as exc:
                        print(
                            f"[CRAWL ERROR] "
                            f"{page_url} -> {exc}"
                        )
                        break

                    if not page.success:
                        print(f"[CRAWL FAILED] {page_url}")

                        if getattr(page, "error_message", None):
                            print(page.error_message)

                        break

                    if not page.html:
                        print("[ERROR] Empty HTML")
                        break

                    soup = BeautifulSoup(
                        page.html,
                        "html.parser",
                    )

                    product_cards = soup.select(
                        "div.group.bg-white.border.border-neutral-200.rounded-xl"
                    )

                    print(
                        f"[PRODUCT CARDS FOUND] "
                        f"{len(product_cards)}"
                    )

                    if not product_cards:
                        print("[NO PRODUCTS FOUND]")
                        break

                    page_product_count = 0

                    for index, card in enumerate(
                        product_cards,
                        start=1,
                    ):
                        print()
                        print(
                            f"--- Product "
                            f"{index}/{len(product_cards)} ---"
                        )

                        product = await parse_product_card(
                            crawler,
                            card,
                            category,
                        )

                        if not product:
                            continue

                        name = product["name"]
                        product_link = product["product_link"]

                        name_key = name.lower().strip()

                        if name_key in seen_names:
                            print("   ⏭️ Duplicate name")
                            continue

                        if (
                            product_link != "N/A"
                            and product_link in seen_links
                        ):
                            print("   ⏭️ Duplicate link")
                            continue

                        seen_names.add(name_key)

                        if product_link != "N/A":
                            seen_links.add(product_link)

                        results.append(product)
                        page_product_count += 1

                        print(
                            f"   Price: ₹{product['price']} | "
                            f"Original: ₹{product['original_price']} | "
                            f"Discount: {product['discount']}% | "
                            f"Rating: {product['ratings']}"
                        )

                        print(
                            f"   Description: "
                            f"{product['description']}"
                        )

                    print()
                    print(
                        f"[PAGE PRODUCTS ADDED] "
                        f"{page_product_count}"
                    )

                    page_num += 1

    print()
    print("=" * 60)
    print("BUYWOW SCRAPING COMPLETED")
    print("=" * 60)
    print(f"Total products: {len(results)}")
    print("=" * 60)

    return results


# ============================================================
# WINDOWS / LINUX PROCESS WORKER
# ============================================================

def run_buywow_process():
    """Run Crawl4AI in a separate process for Scrapy compatibility."""

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return asyncio.run(scrape_buywow_async())


# ============================================================
# SCRAPY SPIDER
# ============================================================

class BuywowSpider(scrapy.Spider):
    name = "buywow"
    allowed_domains = ["buywow.in", "www.buywow.in"]

    custom_settings = {
        "ROBOTSTXT_OBEY": False,
        "CONCURRENT_REQUESTS": 1,
        "LOG_LEVEL": "INFO",
    }

    async def start(self):
        """
        Crawl4AI is executed in a separate process so Playwright/Crawl4AI
        can use the required Windows Proactor event loop without conflicting
        with Scrapy's reactor.
        """

        loop = asyncio.get_running_loop()

        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(
                executor,
                run_buywow_process,
            )

        for product in products:
            yield product
