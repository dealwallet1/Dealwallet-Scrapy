import asyncio


import csv


import json


import logging


import re


import sys


from datetime import datetime, timezone


from concurrent.futures import ProcessPoolExecutor


from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode, urlsplit, urlunsplit


import scrapy


from bs4 import BeautifulSoup


from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode




BASE_URL = "https://thedecorkart.com"


COLLECTIONS = [


    {


        "url": "https://thedecorkart.com/collections/tables-storage",


        "category": "Home Decor",


    },


    {


        "url": "https://thedecorkart.com/collections/all-furniture",


        "category": "Furnitures",


    },


    {


        "url": "https://thedecorkart.com/collections/serveware",


        "category": "Home & Kitchen",


    },


]


CURRENCY = "₹"


ORGANIZATION_ID = "Dealwallet"


STORE_ID = "The Decor Kart"


MAX_PRODUCTS_PER_COLLECTION = 50


MAX_PRODUCTS = len(COLLECTIONS) * MAX_PRODUCTS_PER_COLLECTION


MAX_PAGES_PER_COLLECTION = 20


MAX_LOAD_MORE_CLICKS = 20


OUTPUT_JSON = "decorkart_products.json"


OUTPUT_CSV = "decorkart_products.csv"


SPIDER_JSON = "scrape_decorkart.json"


HEADERS = {


    "User-Agent": (


        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "


        "AppleWebKit/537.36 (KHTML, like Gecko) "


        "Chrome/130.0.0.0 Safari/537.36"


    ),


    "Accept-Language": "en-IN,en;q=0.9",


}


CSV_FIELDS = [


    "name", "price", "currency", "original_price", "discount", "ratings",


    "description", "image_link", "product_link", "organization_id", "store_id",


    "categories_id", "created_at",


]


def clean_text(value):


    if not value:


        return ""


    value = str(value).replace('"', "")



    value = re.sub(r"\*{2,}", "", value)

    value = re.sub(r"\\+", "", value)


    return re.sub(r"\s+", " ", value).strip()


def clean_description(text):


    """Remove unwanted check marks and normalize description whitespace."""


    if not text:


        return ""


    text = str(text).replace("✔", "")


    return re.sub(r"\s+", " ", text).strip()


def price_to_int(value):


    if value is None:


        return None


    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value).replace("₹", ""))


    if not match:


        return None


    try:


        return int(float(match.group(0).replace(",", "")))


    except (TypeError, ValueError):


        return None


def normalize_url(url, base_url=BASE_URL):


    if not url:


        return None


    url = str(url).strip()


    if url.startswith("//"):


        url = "https:" + url


    url = urljoin(base_url, url)


    parsed = urlparse(url)


    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def calculate_discount(price, original_price):


    if price is None or original_price is None or original_price <= 0 or price >= original_price:


        return 0


    return int(round(((original_price - price) / original_price) * 100))


def extract_rating(card_or_soup, product_page=False):


    selectors = (


        '[itemprop="ratingValue"]',


        '[data-rating]',


        ".spr-badge-starrating",


        ".jdgm-prev-badge__stars",


        ".rating",


        ".product-rating",


    )


    for selector in selectors:


        element = card_or_soup.select_one(selector)


        if not element:


            continue


        raw = (


            element.get("content")


            or element.get("data-rating")


            or element.get("aria-label")


            or element.get_text(" ", strip=True)


        )


        match = re.search(r"\d+(?:\.\d+)?", str(raw or ""))


        if match:


            try:


                rating = float(match.group(0))


                if 0 < rating <= 5:


                    return round(rating, 1)


            except ValueError:


                pass


    return None


def extract_listing_products(html, collection_url):


    soup = BeautifulSoup(html or "", "html.parser")


    


    cards = soup.select(".f-column.product-column .product-card")


    if not cards:


        cards = soup.select(


            ".product-card-style-card, .product-card, .product-card-wrapper, "


            ".card-wrapper, .grid__item, .product-item, .product-grid-item, "


            "li:has(a[href*=\"/products/\"]), article:has(a[href*=\"/products/\"])"


        )


    


    if not cards:


        fallback_cards = []


        seen_nodes = set()


        for product_anchor in soup.select("a[href*='/products/']"):


            node = product_anchor


            for _ in range(7):


                node = node.parent if node else None


                if not node or not getattr(node, "name", None):


                    break


                node_text = node.get_text(" ", strip=True)


                has_image = node.select_one("img") is not None


                has_price = bool(re.search(r"₹|\bRs?\.?\s*\d|\bINR\s*\d", node_text, re.I))


                product_links = node.select("a[href*='/products/']")


                if has_image and has_price and len(product_links) <= 3:


                    node_id = id(node)


                    if node_id not in seen_nodes:


                        seen_nodes.add(node_id)


                        fallback_cards.append(node)


                    break


        cards = fallback_cards


    products = []


    seen = set()


    for card in cards:


        # Product URL and name: .product-card__title a / .reversed-link__text


        title_link = (


            card.select_one(".product-card__title a[href*='/products/']")


            or card.select_one(".product-card__image-wrapper a[href*='/products/']")


            or card.select_one("a[href*='/products/']")


        )


        if not title_link:


            continue


        product_link = normalize_url(title_link.get("href"), collection_url)


        if not product_link or product_link in seen:


            continue


        seen.add(product_link)


        name_el = (


            card.select_one(".product-card__title .reversed-link__text")


            or card.select_one(".product-card__title")


            or card.select_one(".card__heading a, .card__heading, .product-title, .product-item__title")


            or title_link


        )


        name = clean_text(name_el.get_text(" ", strip=True) or title_link.get("aria-label", ""))


        if not name:


            continue


        # Image selector: .product-card__image-wrapper img; prefer main image.


        image_el = (


            card.select_one(".product-card__image-wrapper img.pi-product-card-slider__image")


            or card.select_one(".product-card__image-wrapper img")


            or card.select_one(".pi-product-card-slider img")


            or card.select_one("img")


        )


        image_url = None


        if image_el:


            image_url = (


                image_el.get("data-src")


                or image_el.get("data-lazy-src")


                or image_el.get("src")


            )


            if (not image_url or image_url.startswith("data:")) and image_el.get("srcset"):


                image_url = image_el.get("srcset").split(",")[-1].strip().split()[0]


            image_url = normalize_url(image_url, collection_url)


      


        sale_el = card.select_one(


            ".f-price-item--sale, .price__sale .price-item--sale, "


            ".price-item--sale, .price-item--last, [data-product-price]"


        )


        original_el = card.select_one(


            ".f-price-item--regular s, .f-price-item--regular, "


            ".price__sale .price-item--regular, .price-item--regular s, "


            ".price-item--regular, s.price-item, del .price, .compare-at-price"


        )


        listing_price = price_to_int(sale_el.get_text(" ", strip=True)) if sale_el else None


        listing_original_price = price_to_int(original_el.get_text(" ", strip=True)) if original_el else None


        badge_el = card.select_one(".f-badge--sale, .product-card__badge")


        badge_match = re.search(r"(\d+(?:\.\d+)?)\s*%", badge_el.get_text(" ", strip=True)) if badge_el else None


        listing_discount = (


            int(float(badge_match.group(1)))


            if badge_match


            else calculate_discount(listing_price, listing_original_price)


        )


        products.append({


            "name": name,


            "product_link": product_link,


            "image_link": image_url,


            "ratings": extract_rating(card),


            "listing_price": listing_price,


            "listing_original_price": listing_original_price,


            "listing_discount": listing_discount,


        })


    return products


def extract_product_prices(soup, listing_product):


  


    price_container = soup.select_one(


        ".price, .product__info-container, .product__info, .product-info, [itemprop='offers']"


    )


    search_root = price_container or soup


    sale_el = search_root.select_one(


        ".price__sale .price-item--sale, .price-item--sale, .f-price-item--sale, [data-product-price]"


    )


    original_el = search_root.select_one(


        ".price__sale .price-item--regular, .price-item--regular s, .price-item--regular, "


        ".f-price-item--regular s, .f-price-item--regular"


    )


   

    if not sale_el:


        meta_price = soup.select_one('meta[property="product:price:amount"], meta[itemprop="price"]')


        if meta_price:


            value = meta_price.get("content") or meta_price.get("value")


            if value:


                listing_product["page_meta_price"] = value


                price = price_to_int(value)


            else:


                price = None


        else:


            price = None


    else:


        price = price_to_int(sale_el.get("content") or sale_el.get_text(" ", strip=True))


    original_price = price_to_int(original_el.get_text(" ", strip=True)) if original_el else None


  

    if original_price is None:


        compare_el = soup.select_one(


            "s, del, [class*=compare-at], [class*=compare_at], "


            "[class*=compare-price], [class*=compare_price], "


            "[class*=price__regular], [class*=price-item--regular]"


        )


        if compare_el:


            candidate_original = price_to_int(compare_el.get_text(" ", strip=True))


            


            if candidate_original and (price is None or candidate_original > price):


                original_price = candidate_original


    if price is None:


        price = listing_product.get("listing_price")


    if original_price is None:


        original_price = listing_product.get("listing_original_price")


    discount = calculate_discount(price, original_price)


    if not discount:


        discount = listing_product.get("listing_discount") or 0





    if (


        original_price is None and price and price > 0


        and discount > 0 and discount < 100


    ):


        original_price = int(round(price / (1 - discount / 100)))


    


    if price and original_price and original_price > price:


        calculated = calculate_discount(price, original_price)


        if calculated:


            discount = calculated


    return price, original_price, int(discount or 0)


def extract_product_name(soup, fallback_name):


    title_el = (


        soup.select_one("h1.product__title")


        or soup.select_one(".product__title h1")


        or soup.select_one("h1.product-title")


        or soup.select_one("h1")


    )


    name = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""


    return name or fallback_name


def extract_description(soup, fallback_name=""):




    selectors = (


        ".product__description",


        "#ProductDescription",


        ".product-description",


        ".product__accordion .rte",


        ".product__info-container .rte",


        ".product__info .rte",


        ".rte[itemprop='description']",


        "[itemprop='description']",


        ".rte",


    )


    for selector in selectors:


        desc_el = soup.select_one(selector)


        if desc_el:


            # Remove scripts/styles and common non-description elements before text extraction.


            for unwanted in desc_el.select("script, style, button, form"):


                unwanted.decompose()


            description = clean_description(clean_text(desc_el.get_text(" ", strip=True)))


            if description:


                return description


    meta_description = soup.select_one('meta[name="description"]')


    if meta_description and meta_description.get("content"):


        return clean_description(clean_text(meta_description.get("content")))


    return ""


def is_valid_product_page(html, result, product_url):

    """Reject invalid product URLs and Shopify 404 pages."""

    parsed = urlparse(product_url or "")

    if parsed.netloc.lower() not in {"thedecorkart.com", "www.thedecorkart.com"} or not parsed.path.startswith("/products/"):

        print(f"[SKIPPED] Invalid product URL: {product_url}")

        return False

    status_code = getattr(result, "status_code", None)

    if status_code is not None and int(status_code) >= 400:

        print(f"[SKIPPED] HTTP {status_code}: {product_url}")

        return False

    soup = BeautifulSoup(html or "", "html.parser")

    title = clean_text(soup.title.get_text(" ", strip=True)).lower() if soup.title else ""

    page_text = clean_text(soup.get_text(" ", strip=True)).lower()

    if "404" in title or "page not found" in title or "page you requested does not exist" in page_text or soup.select_one(".template-404, .page-404, [class*=page-not-found]"):

        print(f"[SKIPPED] Product URL displays 404: {product_url}")

        return False

    if not soup.select_one("h1, [itemprop='name'], meta[property='og:title']"):

        print(f"[SKIPPED] Product title not found: {product_url}")

        return False

    return True


def is_product_available(soup):
    """Return False when the main product is explicitly sold out/unavailable.

    This deliberately avoids searching all page text, because recommendation
    cards and footer text can mention other sold-out products.
    """
    unavailable_values = {
        "https://schema.org/outofstock",
        "http://schema.org/outofstock",
        "https://schema.org/discontinued",
        "http://schema.org/discontinued",
    }


    def has_unavailable_offer(value):
        if isinstance(value, dict):
            availability = value.get("availability")
            if isinstance(availability, str):
                normalized = availability.strip().lower()
                if (normalized in unavailable_values
                        or normalized.endswith("/outofstock")
                        or normalized.endswith("/discontinued")):
                    return True
            return any(has_unavailable_offer(item) for item in value.values())
        if isinstance(value, list):
            return any(has_unavailable_offer(item) for item in value)
        return False

    for script in soup.select('script[type="application/ld+json"]'):
        raw_json = script.string or script.get_text(strip=True)
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if has_unavailable_offer(data):
            print("[SKIPPED] Product is marked out of stock in structured data")
            return False

    # Check the main Shopify product JSON when the theme exposes it.
    product_json_scripts = soup.select(
        'script[type="application/json"][data-product-json], '
        'script#ProductJson, script[id*="ProductJson"]'
    )
    for script in product_json_scripts:
        raw_json = script.string or script.get_text(strip=True)
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        variants = data.get("variants") if isinstance(data, dict) else None
        if isinstance(variants, list) and variants:
            availability = [
                variant.get("available")
                for variant in variants
                if isinstance(variant, dict) and "available" in variant
            ]
            if availability and not any(value is True for value in availability):
                print("[SKIPPED] All product variants are sold out")
                return False

    # Scope text/button checks to the product information area, not the whole page.
    product_info = soup.select_one(
        'main .product__info-container, main .product__info, '
        'main .product-info, main [itemprop="offers"], '
        '.product__info-container, .product__info, .product-info'
    ) or soup.select_one("main, #MainContent") or soup

    for selector in (
        '.product__inventory', '.product-inventory', '.inventory-status',
        '[data-product-availability]', '[itemprop="availability"]'
    ):
        for element in product_info.select(selector):
            status = " ".join((
                element.get("content", ""),
                element.get("data-product-availability", ""),
                element.get_text(" ", strip=True),
            )).strip().lower()
            if any(word in status for word in ("sold out", "out of stock", "unavailable")):
                print(f"[SKIPPED] Product availability status: {status[:100]}")
                return False

    # Check the product's own add-to-cart button.
    product_forms = product_info.select('form[action*="/cart/add"], form.product-form')
    buttons = []
    for form in product_forms:
        buttons.extend(form.select('button[name="add"], button[type="submit"], input[name="add"]'))
    if not buttons:
        buttons = product_info.select(
            'button[name="add"], .product-form__submit, '
            'button[data-add-to-cart], [data-add-to-cart]'
        )

    for button in buttons:
        button_text = " ".join((
            button.get_text(" ", strip=True),
            button.get("value", ""),
            button.get("aria-label", ""),
        )).strip().lower()
        unavailable_text = any(
            word in button_text for word in ("sold out", "unavailable", "out of stock")
        )
        if unavailable_text:
            print(f"[SKIPPED] Product add-to-cart button says: {button_text}")
            return False
        if button.has_attr("disabled") or button.get("aria-disabled", "").lower() == "true":
            print("[SKIPPED] Product add-to-cart button is disabled")
            return False

    # Explicit availability flags, if supplied by the theme.
    for element in product_info.select('[data-available], [data-product-available]'):
        for attribute in ("data-available", "data-product-available"):
            value = element.get(attribute)
            if value is not None and value.strip().lower() in {"false", "0", "no"}:
                print("[SKIPPED] Product availability attribute is false")
                return False

    return True


def extract_product_details(html, listing_product, category):


    soup = BeautifulSoup(html or "", "html.parser")


    name = extract_product_name(soup, listing_product.get("name", ""))


    price, original_price, discount = extract_product_prices(soup, listing_product)


    # Mandatory fields: name, current price, original price, discount and product URL.

    if (

        not name

        or price is None or price <= 0

        or original_price is None or original_price <= price

        or discount <= 0

        or not listing_product.get("product_link")

    ):

        print(

            f"[SKIPPED] Missing/invalid mandatory name, prices, discount or product URL: {name} "

            f"(price={price}, original_price={original_price}, discount={discount})"

        )

        return None


    description = extract_description(soup, name)


    image_link = listing_product.get("image_link")


    if not image_link:


        image_el = (


            soup.select_one(".product__media img")


            or soup.select_one(".product__media-list img")


            or soup.select_one("img[alt]")


        )


        if image_el:


            image_link = normalize_url(


                image_el.get("data-src") or image_el.get("src") or image_el.get("data-zoom"),


                listing_product["product_link"],


            )


    product_link = normalize_url(listing_product.get("product_link"))

    image_link = normalize_url(image_link) if image_link else None

    if not description:

        print(f"[SKIPPED] Mandatory description missing: {name}")

        return None

    if not image_link or not image_link.startswith(("https://", "http://")):

        print(f"[SKIPPED] Mandatory image URL missing/invalid: {name}")

        return None

    product_parts = urlparse(product_link or "")

    if (

        not product_link

        or product_parts.netloc.lower() not in {"thedecorkart.com", "www.thedecorkart.com"}

        or not product_parts.path.startswith("/products/")

    ):

        print(f"[SKIPPED] Mandatory product URL missing/invalid: {name}")

        return None


    rating = extract_rating(soup, product_page=True)


    if rating is None:


        rating = listing_product.get("ratings")


    return {


        "name": name,


        "price": int(price),


        "currency": CURRENCY,


        "original_price": int(original_price),


        "discount": int(discount),


        "ratings": rating,


        "description": description,


        "image_link": image_link or "",


        "product_link": product_link,


        "organization_id": ORGANIZATION_ID,


        "store_id": STORE_ID,


        "categories_id": category,


        "created_at": datetime.now(timezone.utc).isoformat(),


    }


def save_json(products):


    with open(OUTPUT_JSON, "w", encoding="utf-8") as file:


        json.dump(products, file, ensure_ascii=False, indent=2)


    print(f"JSON generated: {OUTPUT_JSON}")


def save_csv(products):


    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as file:


        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")


        writer.writeheader()


        writer.writerows(products)


    print(f"CSV generated: {OUTPUT_CSV}")


def add_page_parameter(url, page_number):


    """Build Shopify collection pagination URL with ?page=N."""


    parts = urlsplit(url)


    query = parse_qs(parts.query, keep_blank_values=True)


    query["page"] = [str(page_number)]


    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


async def scrape_collection_pages(crawler, collection_url, category, remaining_total):


    products = []

    # Hard limit: at most 50 successfully saved products in this category.

    category_limit = min(MAX_PRODUCTS_PER_COLLECTION, remaining_total)


    page = 1


    next_page_url = collection_url


    # Click a visible load-more button if this collection theme uses one.


    load_more_js = rf"""


    (async () => {{


        const maxClicks = {MAX_LOAD_MORE_CLICKS};


        const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));


        const isVisible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));


        for (let i = 0; i < maxClicks; i++) {{


            const candidates = Array.from(document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button]'));


            const button = candidates.find(el => {{


                if (!isVisible(el) || el.disabled || el.getAttribute('aria-disabled') === 'true') return false;


                const text = ((el.innerText || el.value || el.getAttribute('aria-label') || '') + ' ' + (el.className || '') + ' ' + (el.id || '')).toLowerCase();


                return /load\s*more|show\s*more|more\s*products|view\s*more/.test(text);


            }});


            if (!button) break;


            button.click();


            await pause(1600);


        }}


    }})();


    """


    listing_config = CrawlerRunConfig(


        cache_mode=CacheMode.BYPASS,


        # Do not wait for a CSS selector. Some requests time out even on body.


        # Crawl4AI waits briefly before returning rendered HTML instead.


        page_timeout=180000,


        delay_before_return_html=3.0,


        js_code=load_more_js,


    )


    product_config = CrawlerRunConfig(


        cache_mode=CacheMode.BYPASS,


        # Avoid wait_for="css:body": it caused 2-second selector timeouts.


        page_timeout=180000,


        delay_before_return_html=3.0,


    )


    while len(products) < category_limit and page <= MAX_PAGES_PER_COLLECTION and next_page_url:


        page_url = next_page_url


        print(f"\n[COLLECTION PAGE] {page} | {page_url}")


        try:


            result = await crawler.arun(url=page_url, config=listing_config)


        except Exception as exc:


            print(f"[PAGE ERROR] {page_url}: {exc}")


            break


        if not result.success:


            print(f"[PAGE ERROR] {page_url}: {result.error_message}")


            break


        html = result.html or ""


        print(f"[PAGE HTML LENGTH] {len(html)}")


        if len(html) < 1000:


            print("[WARNING] Very short HTML returned; the website may have shown an anti-bot/challenge page.")


        soup = BeautifulSoup(html, "html.parser")


        candidates = extract_listing_products(html, collection_url)


        print(f"[LISTING PRODUCTS FOUND] {len(candidates)}")


        if not candidates:


            print("[DEBUG] Product links in HTML:", len(soup.select("a[href*='/products/']")))


            print("[DEBUG] Card selector matches:", {


                ".product-card": len(soup.select(".product-card")),


                ".product-card-wrapper": len(soup.select(".product-card-wrapper")),


                ".grid__item": len(soup.select(".grid__item")),


                ".product-item": len(soup.select(".product-item")),


            })


        if not candidates:


            print("[NO PRODUCTS FOUND] Stopping pagination")


            break


        for item in candidates:


            if len(products) >= category_limit:


                break


            link = item["product_link"]


            print(f"[PRODUCT {len(products) + 1}/{category_limit}] {item['name']}")


            try:


                product_result = await crawler.arun(url=link, config=product_config)


            except Exception as exc:


                print(f"[PRODUCT ERROR] {link}: {exc}")


                continue


            if not product_result.success:


                print(f"[PRODUCT ERROR] {link}: {product_result.error_message}")


                continue


            if not is_valid_product_page(product_result.html or "", product_result, link):

                continue

            product_soup = BeautifulSoup(product_result.html or "", "html.parser")
            if not is_product_available(product_soup):
                continue

            product = extract_product_details(product_result.html or "", item, category)


            if not product:


                continue


            print(f"[DESCRIPTION OK] {product['name']}: {product['description'][:100]}")


            products.append(product)


            print(


                f"[SAVED] {product['name']} | ₹{product['price']} | "


                f"MRP ₹{product['original_price']} | {product['discount']}% OFF"


            )


            if len(products) >= category_limit:

                print(f"[CATEGORY LIMIT REACHED] {category}: {len(products)}/{category_limit}")

                break


        # Follow the collection's next-page link when available; otherwise try Shopify ?page=N.


        next_el = soup.select_one(


            'a[rel="next"], .pagination__item--next a, a.pagination__next, .pagination a[aria-label*="Next"]'


        )


        next_href = normalize_url(next_el.get("href"), page_url) if next_el else None


        if next_href and next_href != page_url:


            next_page_url = next_href


        elif len(candidates) >= 1 and page < MAX_PAGES_PER_COLLECTION:


            candidate_url = add_page_parameter(collection_url, page + 1)


            next_page_url = candidate_url if candidate_url != page_url else None


        else:


            next_page_url = None


        print(f"[COLLECTION VALID TOTAL] {len(products)}/{category_limit}")


        page += 1


    return products


async def main():


    browser_config = BrowserConfig(headless=True, verbose=False, headers=HEADERS)


    all_products = []


    async with AsyncWebCrawler(config=browser_config) as crawler:


        for collection in COLLECTIONS:


            if len(all_products) >= MAX_PRODUCTS:


                break


            collection_url = collection["url"]


            category = collection["category"]


            print("\n==================================================")


            print(f"SCRAPING CATEGORY: {category}")


            print(f"COLLECTION URL: {collection_url}")


            print(f"GLOBAL TOTAL: {len(all_products)}/{MAX_PRODUCTS}")


            print("==================================================")


            remaining = min(MAX_PRODUCTS_PER_COLLECTION, MAX_PRODUCTS - len(all_products))


            collection_products = await scrape_collection_pages(


                crawler, collection_url, category, remaining


            )


            all_products.extend(collection_products)


            print(f"[CATEGORY RUN TOTAL] {len(all_products)}/{MAX_PRODUCTS}")


    save_json(all_products)


    save_csv(all_products)


    print("\n========================================")


    print("THE DECOR KART SCRAPING COMPLETED")


    print("========================================")


    print(f"Total valid products: {len(all_products)}")


    print(f"JSON: {OUTPUT_JSON}")


    print(f"CSV : {OUTPUT_CSV}")


    return all_products


def run_decorkart_scraper():


    logging.basicConfig(


        level=logging.INFO,


        format="%(asctime)s [%(levelname)s] %(message)s",


        stream=sys.stdout,


        force=True,


    )


    if sys.platform.startswith("win"):


        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


    logging.info("=" * 60)


    logging.info("The Decor Kart browser process started.")


    logging.info("=" * 60)


    try:


        products = asyncio.run(main())


        logging.info("The Decor Kart browser process finished. Products returned: %s", len(products))


        return products


    except Exception:


        logging.exception("The Decor Kart browser process failed.")


        raise


class DecorKartSpider(scrapy.Spider):


    name = "decorkart"


    allowed_domains = ["thedecorkart.com", "www.thedecorkart.com"]


    async def start(self):


        """Run the Crawl4AI browser workflow in a separate process."""


        self.logger.info("Starting The Decor Kart scraper through Scrapy...")


        loop = asyncio.get_running_loop()


        with ProcessPoolExecutor(max_workers=1) as executor:


            products = await loop.run_in_executor(executor, run_decorkart_scraper)


        with open(SPIDER_JSON, "w", encoding="utf-8") as file:


            json.dump(products, file, ensure_ascii=False, indent=2)


        self.logger.info("The Decor Kart scraper returned %s products", len(products))


        for product in products:


            if not isinstance(product, dict):


                continue


            if not product.get("image_link") or not product.get("product_link"):


                continue


            yield product


        self.logger.info("The Decor Kart spider completed")


if __name__ == "__main__":


    logging.basicConfig(


        level=logging.INFO,


        format="%(asctime)s [%(levelname)s] %(message)s",


        stream=sys.stdout,


        force=True,


    )


    data = run_decorkart_scraper()


    with open(SPIDER_JSON, "w", encoding="utf-8") as file:


        json.dump(data, file, ensure_ascii=False, indent=2)


    print(f"Saved in {SPIDER_JSON}")


    print("Total products:", len(data))
