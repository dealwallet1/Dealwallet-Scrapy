import asyncio
import json
import re
from urllib.parse import urljoin
 
import aiohttp
from bs4 import BeautifulSoup
 
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)
 
 
MAX_PRODUCTS = 50
 
OUTPUT_FILE = "woollen_wear_winter_products.json"
 
START_URLS = [
    "https://woollen-wear.in/men/mens-winter-coats",
 
    "https://woollen-wear.in/wholesale/men-winter-wholesale/men-jacket-wholesale",
 
    "https://woollen-wear.in/wholesale/men-winter-wholesale/men-socks-wholesale",
 
    "https://woollen-wear.in/wholesale/men-winter-wholesale/gloves-wholesale-for-men",
 
    "https://woollen-wear.in/wholesale/men-winter-wholesale/men-muffler-wholesale",
 
    "https://woollen-wear.in/wholesale/women-winter-wholesale/women-gloves-wholesale",
 
    "https://woollen-wear.in/wholesale/women-winter-wholesale/women-jackets-wholesale",
 
    "https://woollen-wear.in/women/plus-size-winter-wear-womens/womens-plus-size-jackets",
]
 
 
def clean_text(value):
 
    if not value:
        return ""
 
    value = re.sub(
        r"\s+",
        " ",
        value
    )
 
    return value.strip()
 
 
def extract_price(value):
 
    if not value:
        return ""
 
    value = clean_text(value)
 
    match = re.search(
        r"[\d,]+(?:\.\d+)?",
        value
    )
 
    if not match:
        return ""
 
    number = match.group(0).replace(
        ",",
        ""
    )
 
    try:
 
        number = float(number)
 
        if number.is_integer():
            return int(number)
 
        return number
 
    except Exception:
 
        return ""
 
 
def calculate_discount(
    price,
    original_price
):
 
    if price == "" or original_price == "":
        return ""
 
    try:
 
        price = float(price)
        original_price = float(
            original_price
        )
 
        if original_price <= price:
            return ""
 
        discount = (
            (
                original_price - price
            )
            / original_price
        ) * 100
 
        return f"{round(discount)}% OFF"
 
    except Exception:
 
        return ""
 
 
def is_sold_out(element):
 
    if not element:
        return False
 
    text = clean_text(
        element.get_text(
            " ",
            strip=True
        )
    ).lower()
 
    sold_words = [
        "sold out",
        "soldout",
        "out of stock",
        "out-of-stock",
        "unavailable",
    ]
 
    for word in sold_words:
 
        if word in text:
            return True
 
    classes = " ".join(
        element.get(
            "class",
            []
        )
    ).lower()
 
    if (
        "sold" in classes
        and "out" in classes
    ):
        return True
 
    if "outofstock" in classes:
        return True
 
    return False
 
 
def normalize_image_url(
    image_url,
    base_url
):
 
    if not image_url:
        return ""
 
    image_url = image_url.strip()
 
    if image_url.startswith(
        "data:image"
    ):
        return ""
 
    if image_url.startswith("//"):
 
        image_url = (
            "https:"
            + image_url
        )
 
    image_url = urljoin(
        base_url,
        image_url
    )
 
    return image_url
 
 
def get_image_candidates(
    img,
    base_url
):
 
    candidates = []
 
    attributes = [
        "data-original",
        "data-src",
        "data-lazy-src",
        "data-full",
        "data-large",
        "data-zoom-image",
        "src",
    ]
 
    for attribute in attributes:
 
        value = img.get(
            attribute
        )
 
        if not value:
            continue
 
        value = normalize_image_url(
            value,
            base_url
        )
 
        if (
            value
            and value not in candidates
        ):
 
            candidates.append(
                value
            )
 
    srcset_attributes = [
        "data-srcset",
        "data-lazy-srcset",
        "srcset",
    ]
 
    for attribute in srcset_attributes:
 
        srcset = img.get(
            attribute
        )
 
        if not srcset:
            continue
 
        srcset_images = []
 
        for item in srcset.split(","):
 
            item = item.strip()
 
            if not item:
                continue
 
            parts = item.split()
 
            image_url = parts[0]
 
            width = 0
 
            if len(parts) > 1:
 
                match = re.search(
                    r"(\d+)w",
                    parts[1]
                )
 
                if match:
 
                    try:
                        width = int(
                            match.group(1)
                        )
                    except Exception:
                        width = 0
 
            image_url = normalize_image_url(
                image_url,
                base_url
            )
 
            if image_url:
 
                srcset_images.append(
                    (
                        width,
                        image_url
                    )
                )
 
        srcset_images.sort(
            key=lambda x: x[0],
            reverse=True
        )
 
        for _, image_url in srcset_images:
 
            if image_url not in candidates:
 
                candidates.append(
                    image_url
                )
 
    return candidates
 
 
def get_category_image(
    card,
    category_url
):
 
    images = card.select(
        ".img img.img-responsive"
    )
 
    if not images:
 
        images = card.select(
            "img"
        )
 
    if not images:
        return ""
 
    for img in images:
 
        candidates = get_image_candidates(
            img,
            category_url
        )
 
        if candidates:
 
            return candidates[0]
 
    return ""
 
 
async def verify_image_url(
    session,
    image_url
):
 
    if not image_url:
        return ""
 
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Referer": "https://woollen-wear.in/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
 
    try:
 
        async with session.get(
            image_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(
                total=20
            ),
            allow_redirects=True
        ) as response:
 
            if response.status != 200:
 
                return ""
 
            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()
 
            if "image" not in content_type:
 
                return ""
 
            return str(
                response.url
            )
 
    except Exception:
 
        return ""
 
 
async def find_valid_image(
    candidates
):
 
    if not candidates:
        return ""
 
    connector = aiohttp.TCPConnector(
        ssl=False
    )
 
    async with aiohttp.ClientSession(
        connector=connector
    ) as session:
 
        for image_url in candidates:
 
            print(
                f"     [IMAGE CHECK] "
                f"{image_url}"
            )
 
            valid_url = await verify_image_url(
                session,
                image_url
            )
 
            if valid_url:
 
                print(
                    f"     [IMAGE VALID]"
                )
 
                return valid_url
 
            print(
                f"     [IMAGE 404/INVALID]"
            )
 
    return ""
 
 
async def get_detail_image(
    html,
    product_url
):
 
    soup = BeautifulSoup(
        html,
        "html.parser"
    )
 
    candidates = []
 
    selectors = [
 
        ".product-image img",
 
        ".product-images img",
 
        ".product-gallery img",
 
        ".image-additional img",
 
        ".thumbnails img",
 
        ".product-thumb img",
 
        ".product-info img",
 
        ".gallery img",
 
        ".zoom img",
 
    ]
 
    for selector in selectors:
 
        images = soup.select(
            selector
        )
 
        for img in images:
 
            image_candidates = (
                get_image_candidates(
                    img,
                    product_url
                )
            )
 
            for image_url in image_candidates:
 
                if image_url not in candidates:
 
                    candidates.append(
                        image_url
                    )
 
    if not candidates:
 
        for img in soup.select(
            "img"
        ):
 
            image_candidates = (
                get_image_candidates(
                    img,
                    product_url
                )
            )
 
            for image_url in image_candidates:
 
                if image_url not in candidates:
 
                    candidates.append(
                        image_url
                    )
 
    return await find_valid_image(
        candidates
    )
 
 
async def parse_category_products(
    html,
    category_url
):
 
    soup = BeautifulSoup(
        html,
        "html.parser"
    )
 
    cards = soup.select(
        "div.product-layout"
    )
 
    print(
        f"[INFO] Product cards found: "
        f"{len(cards)}"
    )
 
    products = []
 
    for index, card in enumerate(
        cards,
        1
    ):
 
        if is_sold_out(card):
 
            print(
                f"[SKIP {index}] SOLD OUT"
            )
 
            continue
 
        name_link = card.select_one(
            ".caption h4 a"
        )
 
        if not name_link:
 
            name_link = card.select_one(
                "h4 a"
            )
 
        if not name_link:
 
            print(
                f"[SKIP {index}] "
                f"Name not found"
            )
 
            continue
 
        product_name = clean_text(
            name_link.get_text(
                " ",
                strip=True
            )
        )
 
        product_link = name_link.get(
            "href",
            ""
        )
 
        if not product_link:
 
            print(
                f"[SKIP {index}] "
                f"Product URL not found"
            )
 
            continue
 
        product_link = urljoin(
            category_url,
            product_link
        )
 
        price = ""
 
        price_element = card.select_one(
            ".caption .price"
        )
 
        if price_element:
 
            price = extract_price(
                price_element.get_text(
                    " ",
                    strip=True
                )
            )
 
        original_price = ""
 
        original_selectors = [
            ".caption .price-old",
            ".caption .old-price",
            ".caption del",
            ".caption s",
            ".price del",
            ".price s",
        ]
 
        for selector in original_selectors:
 
            element = card.select_one(
                selector
            )
 
            if element:
 
                value = extract_price(
                    element.get_text(
                        " ",
                        strip=True
                    )
                )
 
                if value != "":
 
                    original_price = value
                    break
 
        discount = calculate_discount(
            price,
            original_price
        )
 
        ratings = ""
 
        rating_element = card.select_one(
            ".caption .rating"
        )
 
        if rating_element:
 
            rating_text = clean_text(
                rating_element.get_text(
                    " ",
                    strip=True
                )
            )
 
            match = re.search(
                r"(\d+(?:\.\d+)?)",
                rating_text
            )
 
            if match:
 
                try:
 
                    rating_value = float(
                        match.group(1)
                    )
 
                    if (
                        rating_value > 0
                        and rating_value <= 5
                    ):
 
                        ratings = round(
                            rating_value,
                            1
                        )
 
                except Exception:
                    pass
 
        description = ""
 
        tagline = card.select_one(
            ".caption .tagline"
        )
 
        if tagline:
 
            description = clean_text(
                tagline.get_text(
                    " ",
                    strip=True
                )
            )
 
        sku = ""
 
        model = card.select_one(
            ".caption .model"
        )
 
        if model:
 
            sku = clean_text(
                model.get_text(
                    " ",
                    strip=True
                )
            )
 
        category_image = (
            get_category_image(
                card,
                category_url
            )
        )
 
        if not description:
 
            if (
                product_name
                and price != ""
            ):
 
                description = (
                    f"{product_name} ₹{price}"
                )
 
            else:
 
                description = product_name
 
        product = {
 
            "name": product_name,
 
            "price": price,
 
            "original_price": original_price,
 
            "discount": discount,
 
            "ratings": ratings,
 
            "description": description,
 
            "image_link": category_image,
 
            "product_link": product_link,
 
            "currency": "INR",
 
            "sku": sku,
 
        }
 
        products.append(
            product
        )
 
        print()
        print(
            f"[{index:02d}] "
            f"{product_name}"
        )
 
        print(
            f"     Price     : ₹{price}"
        )
 
        print(
            f"     SKU       : {sku}"
        )
 
        print(
            f"     Image     : "
            f"{category_image}"
        )
 
        print(
            f"     URL       : "
            f"{product_link}"
        )
 
    return products
 
 
def update_detail_data(
    html,
    product
):
 
    soup = BeautifulSoup(
        html,
        "html.parser"
    )
 
    page_text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    ).lower()
 
    sold_words = [
        "sold out",
        "soldout",
        "out of stock",
        "out-of-stock",
        "unavailable",
    ]
 
    for word in sold_words:
 
        if word in page_text:
 
            product["_sold_out"] = True
 
            return product
 
    product["_sold_out"] = False
 
    name_selectors = [
        "h1",
        ".product-title",
        ".product-name",
        ".caption h1",
    ]
 
    for selector in name_selectors:
 
        element = soup.select_one(
            selector
        )
 
        if element:
 
            value = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )
 
            if value:
 
                product["name"] = value
                break
 
    detail_price = ""
 
    price_selectors = [
        ".product-price",
        ".product-info-price",
        ".product-info .product-price",
        ".product-details .product-price",
        ".product-summary .product-price",
    ]
 
    for selector in price_selectors:
 
        elements = soup.select(selector)
 
        for element in elements:
 
            value = extract_price(
                element.get_text(
                    " ",
                    strip=True
                )
            )
 
            if value != "":
 
                detail_price = value
                break
 
        if detail_price != "":
            break
 
    if detail_price == "":
 
        for script in soup.select(
            'script[type="application/ld+json"]'
        ):
 
            raw_json = script.string or script.get_text(
                strip=True
            )
 
            if not raw_json:
                continue
 
            try:
 
                data = json.loads(raw_json)
 
                objects = []
 
                if isinstance(data, dict):
                    objects.append(data)
 
                    graph = data.get("@graph")
 
                    if isinstance(graph, list):
                        objects.extend(graph)
 
                elif isinstance(data, list):
                    objects.extend(data)
 
                for obj in objects:
 
                    if not isinstance(obj, dict):
                        continue
 
                    obj_type = obj.get("@type", "")
 
                    if isinstance(obj_type, list):
                        is_product = "Product" in obj_type
                    else:
                        is_product = obj_type == "Product"
 
                    if not is_product:
                        continue
 
                    offers = obj.get("offers", [])
 
                    if isinstance(offers, dict):
                        offers = [offers]
 
                    if not isinstance(offers, list):
                        continue
 
                    for offer in offers:
 
                        if not isinstance(offer, dict):
                            continue
 
                        offer_price = offer.get("price")
 
                        if offer_price is None:
                            continue
 
                        value = extract_price(
                            str(offer_price)
                        )
 
                        if value != "":
 
                            detail_price = value
                            break
 
                    if detail_price != "":
                        break
 
                if detail_price != "":
                    break
 
            except Exception:
                continue
 
    if detail_price != "":
 
        print(
            f"[PRICE] Reliable detail price found: "
            f"₹{detail_price}"
        )
 
        product["price"] = detail_price
 
    else:
 
        print(
            f"[PRICE] No reliable detail price found. "
            f"Keeping category price: ₹{product.get('price', '')}"
        )
 
    original_selectors = [
        ".price-old",
        ".old-price",
        ".product-price .price-old",
        ".product-price .old-price",
        ".product-info-price .price-old",
        ".product-info-price .old-price",
    ]
 
    for selector in original_selectors:
 
        elements = soup.select(selector)
 
        for element in elements:
 
            value = extract_price(
                element.get_text(
                    " ",
                    strip=True
                )
            )
 
            if value != "":
 
                product[
                    "original_price"
                ] = value
 
                break
 
        if product.get(
            "original_price",
            ""
        ) != "":
 
            break
 
    product["discount"] = (
        calculate_discount(
            product.get("price"),
            product.get(
                "original_price"
            )
        )
    )
 
    description_selectors = [
        ".product-description",
        "#description",
        ".description",
        ".tab-content",
    ]
 
    for selector in description_selectors:
 
        element = soup.select_one(
            selector
        )
 
        if element:
 
            value = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )
 
            if len(value) > 20:
 
                product[
                    "description"
                ] = value
 
                break
 
    return product
 
 
async def main():
 
    print()
    print("=" * 80)
    print(
        "WOOLLEN WEAR - 8 URL WINTER SCRAPER"
    )
    print(
        "IMAGE URL VALIDATION ENABLED"
    )
    print("=" * 80)
 
    print(
        f"[TARGET] Maximum products: "
        f"{MAX_PRODUCTS}"
    )
 
    print(
        f"[URL COUNT] "
        f"{len(START_URLS)}"
    )
 
    browser_config = BrowserConfig(
        headless=True
    )
 
    crawler = AsyncWebCrawler(
        config=browser_config
    )
 
    await crawler.start()
 
    all_products = []
 
    try:
 
        for url_number, category_url in enumerate(
            START_URLS,
            1
        ):
 
            print()
            print("=" * 80)
 
            print(
                f"[URL {url_number}/"
                f"{len(START_URLS)}] OPENING"
            )
 
            print(
                category_url
            )
 
            print("=" * 80)
 
            try:
 
                config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=180000,
                )
 
                result = await crawler.arun(
                    url=category_url,
                    config=config
                )
 
                if not result.success:
 
                    print(
                        f"[FAILED] URL "
                        f"{url_number}/8"
                    )
 
                    print(
                        result.error_message
                    )
 
                    continue
 
                print(
                    f"[SUCCESS] URL "
                    f"{url_number}/8 OPENED"
                )
 
                html = result.html or ""
 
                print(
                    f"[HTML SIZE] "
                    f"{len(html):,}"
                )
 
                page_products = (
                    await parse_category_products(
                        html,
                        category_url
                    )
                )
 
                all_products.extend(
                    page_products
                )
 
                print()
                print(
                    f"[URL {url_number}/8] "
                    f"FOUND: "
                    f"{len(page_products)}"
                )
 
                print(
                    f"[TOTAL SO FAR] "
                    f"{len(all_products)}"
                )
 
            except Exception as e:
 
                print(
                    f"[ERROR] URL "
                    f"{url_number}/8"
                )
 
                print(
                    str(e)
                )
 
    finally:
 
        await crawler.close()
 
    print()
    print("=" * 80)
    print("DEDUPLICATING")
    print("=" * 80)
 
    unique_products = []
 
    seen_urls = set()
 
    for product in all_products:
 
        product_url = product.get(
            "product_link",
            ""
        )
 
        if not product_url:
            continue
 
        if product_url in seen_urls:
            continue
 
        seen_urls.add(
            product_url
        )
 
        unique_products.append(
            product
        )
 
    print(
        f"[BEFORE] "
        f"{len(all_products)}"
    )
 
    print(
        f"[AFTER] "
        f"{len(unique_products)}"
    )
 
    unique_products = unique_products[
        :MAX_PRODUCTS
    ]
 
    print()
    print("=" * 80)
    print("OPENING PRODUCT DETAIL PAGES")
    print("=" * 80)
 
    browser_config = BrowserConfig(
        headless=True
    )
 
    crawler = AsyncWebCrawler(
        config=browser_config
    )
 
    await crawler.start()
 
    final_products = []
 
    try:
 
        for index, product in enumerate(
            unique_products,
            1
        ):
 
            print()
            print("-" * 80)
 
            print(
                f"[DETAIL {index}/"
                f"{len(unique_products)}]"
            )
 
            print(
                f"[PRODUCT] "
                f"{product.get('name', '')}"
            )
 
            product_url = product.get(
                "product_link",
                ""
            )
 
            print(
                f"[URL] "
                f"{product_url}"
            )
 
            try:
 
                config = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=180000,
                )
 
                result = await crawler.arun(
                    url=product_url,
                    config=config
                )
 
                if result.success:
 
                    product = update_detail_data(
                        result.html or "",
                        product
                    )
 
                    if product.get(
                        "_sold_out",
                        False
                    ):
 
                        print(
                            "[SKIP] SOLD OUT"
                        )
 
                        continue
 
                    print(
                        "[IMAGE] Searching "
                        "detail page images..."
                    )
 
                    detail_image = (
                        await get_detail_image(
                            result.html or "",
                            product_url
                        )
                    )
 
                    if detail_image:
 
                        product[
                            "image_link"
                        ] = detail_image
 
                        print(
                            "[IMAGE SELECTED]"
                        )
 
                        print(
                            detail_image
                        )
 
                    else:
 
                        print(
                            "[IMAGE] No valid "
                            "detail image found"
                        )
 
                        category_image = product.get(
                            "image_link",
                            ""
                        )
 
                        if category_image:
 
                            print(
                                "[IMAGE] Checking "
                                "category image..."
                            )
 
                            valid_category_image = (
                                await find_valid_image(
                                    [
                                        category_image
                                    ]
                                )
                            )
 
                            if valid_category_image:
 
                                product[
                                    "image_link"
                                ] = (
                                    valid_category_image
                                )
 
                            else:
 
                                product[
                                    "image_link"
                                ] = ""
 
                                print(
                                    "[IMAGE] No valid "
                                    "image available"
                                )
 
                    product.pop(
                        "_sold_out",
                        None
                    )
 
                else:
 
                    print(
                        "[DETAIL FAILED] "
                        "Keeping category data"
                    )
 
                    category_image = product.get(
                        "image_link",
                        ""
                    )
 
                    if category_image:
 
                        valid_image = (
                            await find_valid_image(
                                [
                                    category_image
                                ]
                            )
                        )
 
                        product[
                            "image_link"
                        ] = valid_image
 
                final_products.append(
                    product
                )
 
            except Exception as e:
 
                print(
                    f"[DETAIL ERROR] "
                    f"{e}"
                )
 
                category_image = product.get(
                    "image_link",
                    ""
                )
 
                if category_image:
 
                    try:
 
                        valid_image = (
                            await find_valid_image(
                                [
                                    category_image
                                ]
                            )
                        )
 
                        product[
                            "image_link"
                        ] = valid_image
 
                    except Exception:
 
                        product[
                            "image_link"
                        ] = ""
 
                final_products.append(
                    product
                )
 
    finally:
 
        await crawler.close()
 
    final_unique = []
 
    seen_urls = set()
 
    for product in final_products:
 
        product_url = product.get(
            "product_link",
            ""
        )
 
        if not product_url:
            continue
 
        if product_url in seen_urls:
            continue
 
        seen_urls.add(
            product_url
        )
 
        final_unique.append(
            product
        )
 
    final_unique = final_unique[
        :MAX_PRODUCTS
    ]
 
    for product in final_unique:
 
        product.pop(
            "_sold_out",
            None
        )
 
    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
 
        json.dump(
            final_unique,
            file,
            ensure_ascii=False,
            indent=4
        )
 
    print()
    print("=" * 80)
    print("SCRAPING COMPLETED")
    print("=" * 80)
 
    print(
        f"[TOTAL PRODUCTS] "
        f"{len(final_unique)}"
    )
 
    valid_images = sum(
        1
        for product in final_unique
        if product.get(
            "image_link"
        )
    )
 
    missing_images = (
        len(final_unique)
        - valid_images
    )
 
    print(
        f"[VALID IMAGES] "
        f"{valid_images}"
    )
 
    print(
        f"[MISSING IMAGES] "
        f"{missing_images}"
    )
 
    print(
        f"[OUTPUT FILE] "
        f"{OUTPUT_FILE}"
    )
 
    print("=" * 80)
 
 
if __name__ == "__main__":
 
    asyncio.run(
        main()
    )
 