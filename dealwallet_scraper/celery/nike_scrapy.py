import asyncio
import csv
import json
import os
import re
from urllib.parse import urljoin
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

load_dotenv()

BASE_URL = "https://www.nike.in"
MAX_CONCURRENT = 6
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY are required in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

CATEGORY_URLS = {
    "Running": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92586_",
        "category": "Shoes"
    },
    "Jordan": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92581_",
        "category": "Shoes"
    },
    "Tennis": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92588_",
        "category": "Shoes"
    },
    "Lifestyle": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92589_",
        "category": "Shoes"
    },
    "Basketball": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92583_",
        "category": "Shoes"
    },
    "Training and Gym": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92590_",
        "category": "Shoes"
    },
    "Skateboarding": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92591_",
        "category": "Shoes"
    },
    "Football": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92584_",
        "category": "Shoes"
    },
    "Walking": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92592_",
        "category": "Shoes"
    },
    "Athletics": {
        "url": "https://www.nike.in/men/men-s-shoes/c/92564?root=nav_3&ptype=listing%2Cmen%2Cshoes%2C1%2Call-shoes&f=category_filter%3D92582_",
        "category": "Shoes"
    },
    "Tops and T-Shirts": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Shirts"
    },
    "Shorts": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Fashion & Lifestyle"
    },
    "Trousers and Tights": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Fashion & Lifestyle"
    },
    "Jackets": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Fashion & Lifestyle"
    },
    "Hoodies and Sweatshirts": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Fashion & Lifestyle"
    },
    "Tracksuits": {
        "url": "https://www.nike.in/men/men-clothing/c/92567?root=nav_3&ptype=listing%2Cmen%2Cclothing%2C1%2Call-clothing",
        "category": "Fashion & Lifestyle"
    }
}

def clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None

def normalize_url(url):
    if not url:
        return None
    return urljoin(BASE_URL, url).split("#")[0]

def normalize_name(name):
    if not name:
        return None
    return re.sub(r"\s+", " ", str(name)).strip().casefold()

def parse_price(value):
    if not value:
        return None
    matches = re.findall(r"₹\s*([\d,]+)", str(value))
    if not matches:
        return None
    try:
        return int(matches[-1].replace(",", ""))
    except ValueError:
        return None

def parse_rating(value):
    if not value:
        return None
    for pattern in [
        r"★\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*\(\d+\)"
    ]:
        match = re.search(pattern, str(value))
        if match:
            rating = float(match.group(1))
            if 0 <= rating <= 5:
                return rating
    return None

def calculate_discount(original_price, price):
    if original_price is None or price is None:
        return None
    if original_price <= price:
        return 0
    return round((original_price - price) * 100 / original_price)

def get_highest_image(image_node):
    if not image_node:
        return None
    candidates = []
    srcset = image_node.get("srcset")
    if srcset:
        for item in srcset.split(","):
            parts = item.strip().split()
            if not parts:
                continue
            url = parts[0].replace("&amp;", "&")
            width = 0
            if len(parts) > 1:
                match = re.search(r"(\d+)(?:w|x)", parts[1])
                if match:
                    width = int(match.group(1))
            candidates.append((width, url))
    src = image_node.get("src")
    if src:
        src = src.replace("&amp;", "&")
        match = re.search(r"tr=w-(\d+)", src)
        width = int(match.group(1)) if match else 0
        candidates.append((width, src))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    image_url = candidates[0][1]
    image_url = re.sub(r"tr=w-\d+", "tr=w-1536", image_url)
    image_url = re.sub(r"([?&])width=\d+", r"\1width=1536", image_url)
    return image_url

def get_category_products(html):
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen_links = set()
    seen_names = set()
    cards = soup.select("div.prod-ctr > div.plp-prod")
    if not cards:
        cards = soup.select("div.plp-prod")
    if not cards:
        cards = soup.select("div[class*='plp-prod']")
    if not cards:
        cards = soup.select("a[href*='/p/']")
    for card in cards:
        link = card if card.name == "a" else card.select_one("a[href*='/p/']")
        if not link:
            continue
        product_url = normalize_url(link.get("href"))
        if not product_url or product_url in seen_links:
            continue
        name_node = card.select_one("div[id^='aria-label-'][id$='-1']")
        if not name_node:
            name_node = card.select_one("img[alt]")
        price_node = card.select_one("h3")
        image_node = card.select_one("img.plp-mweb-img")
        if not image_node:
            image_node = card.select_one("img")
        name = clean(
            name_node.get_text(" ", strip=True)
            if name_node else None
        )
        if not name and image_node:
            name = clean(image_node.get("alt"))
        name_key = normalize_name(name)
        if not name_key or name_key in seen_names:
            continue
        seen_links.add(product_url)
        seen_names.add(name_key)
        products.append({
            "name": name,
            "price": parse_price(
                price_node.get_text(" ", strip=True)
                if price_node else None
            ),
            "image_link": get_highest_image(image_node),
            "product_link": product_url
        })
    return products

def get_product_data(html):
    soup = BeautifulSoup(html, "html.parser")
    name_node = soup.select_one("#pdp_product_title")
    price_node = soup.select_one("[data-at='mrp-pdp']")
    description_node = soup.select_one(".css-56ccop .css-kawy5t")
    image_node = soup.select_one("img[data-at='pdp-product-image']")
    name = clean(
        name_node.get_text(" ", strip=True)
        if name_node else None
    )
    price = parse_price(
        price_node.get_text(" ", strip=True)
        if price_node else None
    )
    description = clean(
        description_node.get_text(" ", strip=True)
        if description_node else None
    )
    image_link = get_highest_image(image_node)
    original_price = None
    for selector in [
        "del",
        "[class*='mrp']",
        "[class*='strike']",
        "[class*='original']"
    ]:
        node = soup.select_one(selector)
        if node:
            candidate = parse_price(
                node.get_text(" ", strip=True)
            )
            if candidate is not None and candidate != price:
                original_price = candidate
                break
    rating = None
    for selector in [
        "[data-at*='rating']",
        "[class*='rating']",
        "[aria-label*='rating' i]"
    ]:
        node = soup.select_one(selector)
        if node:
            rating = parse_rating(
                node.get_text(" ", strip=True)
            )
            if rating is None:
                rating = parse_rating(
                    node.get("aria-label")
                )
            if rating is not None:
                break
    if rating is None:
        rating = parse_rating(
            soup.get_text(" ", strip=True)
        )
    return {
        "name": name,
        "price": price,
        "original_price": original_price,
        "discount": calculate_discount(
            original_price,
            price
        ),
        "ratings": rating,
        "description": description,
        "image_link": image_link
    }

async def crawl_page(crawler, url, scroll=False):
    js_code = None
    if scroll:
        js_code = """
        (async()=>{
            for(let i=0;i<4;i++){
                window.scrollTo(0,document.body.scrollHeight);
                await new Promise(resolve=>setTimeout(resolve,700));
            }
        })();
        """
    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for="css:body",
        js_code=js_code,
        delay_before_return_html=1,
        page_timeout=90000,
        scan_full_page=False,
        scroll_delay=0.5
    )
    result = await crawler.arun(
        url=url,
        config=config
    )
    if not result.success:
        print("ERROR:", result.error_message)
        return None
    return result.html

async def crawl_product(crawler, semaphore, product, category):
    async with semaphore:
        print("OPEN:", product["product_link"])
        html = await crawl_page(
            crawler,
            product["product_link"]
        )
        if not html:
            return None
        data = get_product_data(html)
        price = (
            data["price"]
            if data["price"] is not None
            else product["price"]
        )
        image_link = (
            data["image_link"]
            or product["image_link"]
        )
        return {
            "name": data["name"] or product["name"],
            "price": price,
            "currency": "₹" if price is not None else None,
            "original_price": data["original_price"],
            "discount": data["discount"],
            "ratings": data["ratings"],
            "description": data["description"],
            "image_link": image_link,
            "product_link": product["product_link"],
            "organization_id": "DealWallet",
            "store_id": "Nike",
            "categories_id": category,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

def save_to_supabase(products):
    if not products:
        print("NO PRODUCTS TO SAVE")
        return
    unique_products = {}
    for product in products:
        link = product.get("product_link")
        if link:
            unique_products[link] = product
    products = list(unique_products.values())
    print("=" * 70)
    print("SUPABASE UPLOAD")
    print("=" * 70)
    print("Products to upload:", len(products))
    try:
        batch_size = 100
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            supabase.table("products").upsert(
                batch,
                on_conflict="product_link"
            ).execute()
            print(
                f"Uploaded batch {i + 1}-{i + len(batch)}"
            )
        print("SUPABASE UPLOAD COMPLETED")
    except Exception as error:
        print("SUPABASE ERROR:", error)

async def main():
    browser_config = BrowserConfig(
        headless=True,
        viewport_width=1920,
        viewport_height=1080
    )
    all_products = []
    global_seen_links = set()
    global_seen_names = set()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with AsyncWebCrawler(
        config=browser_config
    ) as crawler:
        for subcategory, category_data in CATEGORY_URLS.items():
            category_url = category_data["url"]
            category = category_data["category"]
            print("=" * 70)
            print("CATEGORY:", category)
            print("SUBCATEGORY:", subcategory)
            print("=" * 70)
            html = await crawl_page(
                crawler,
                category_url,
                True
            )
            if not html:
                continue
            products = get_category_products(html)
            print(
                "Unique listing models:",
                len(products)
            )
            tasks = []
            for product in products:
                product_url = normalize_url(
                    product["product_link"]
                )
                product_name = clean(
                    product["name"]
                )
                product_name_key = normalize_name(
                    product_name
                )
                if not product_url or not product_name_key:
                    continue
                if product_url in global_seen_links:
                    print(
                        "SKIP DUPLICATE LINK:",
                        product_url
                    )
                    continue
                if product_name_key in global_seen_names:
                    print(
                        "SKIP EXACT SAME MODEL:",
                        product_name
                    )
                    continue
                global_seen_links.add(product_url)
                global_seen_names.add(product_name_key)
                tasks.append(
                    asyncio.create_task(
                        crawl_product(
                            crawler,
                            semaphore,
                            product,
                            category
                        )
                    )
                )
            results = await asyncio.gather(
                *tasks,
                return_exceptions=True
            )
            for result in results:
                if isinstance(result, Exception):
                    print(
                        "PRODUCT ERROR:",
                        result
                    )
                    continue
                if result is None:
                    continue
                final_name = clean(result["name"])
                if not final_name:
                    continue
                all_products.append(result)
                print(
                    "✓",
                    final_name,
                    "| ₹",
                    result["price"],
                    "| rating:",
                    result["ratings"]
                )
    final_products = []
    final_links = set()
    final_names = set()
    for product in all_products:
        link = normalize_url(
            product.get("product_link")
        )
        name_key = normalize_name(
            product.get("name")
        )
        if link and link in final_links:
            continue
        if name_key and name_key in final_names:
            continue
        if link:
            final_links.add(link)
        if name_key:
            final_names.add(name_key)
        final_products.append(product)
    with open(
        "nike_products.json",
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            final_products,
            f,
            ensure_ascii=False,
            indent=2
        )
    csv_fields = [
        "name",
        "price",
        "currency",
        "original_price",
        "discount",
        "ratings",
        "description",
        "image_link",
        "product_link",
        "organization_id",
        "store_id",
        "categories_id",
        "updated_at"
    ]
    with open(
        "nike_products.csv",
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=csv_fields,
            extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(final_products)
    print("=" * 70)
    print("UNIQUE PRODUCTS:", len(final_products))
    print("SAVED: nike_products.json")
    print("SAVED: nike_products.csv")
    print("=" * 70)
    save_to_supabase(final_products)

if __name__ == "__main__":
    asyncio.run(main())