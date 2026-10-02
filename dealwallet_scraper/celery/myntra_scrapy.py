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

BASE_URL = "https://www.myntra.com"
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY are required in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

CATEGORY_URLS = {
    "Men T-Shirts": "https://www.myntra.com/men-tshirts",
    "Men Jackets": "https://www.myntra.com/men-jackets",
    "Men Casual Shoes": "https://www.myntra.com/men-casual-shoes"
}

def clean(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value if value else None

def normalize_url(url):
    if not url:
        return None
    return urljoin(BASE_URL, url).split("#")[0]

def parse_price(value):
    if not value:
        return None
    value = str(value).replace(",", "")
    match = re.search(r"(?:Rs\.?|₹)\s*(\d+(?:\.\d+)?)", value, re.I)
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if number.is_integer() else number

def parse_discount(value):
    if not value:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*OFF", str(value), re.I)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if number.is_integer() else number

def parse_rating(value):
    if not value:
        return None
    match = re.search(r"\b([0-5](?:\.\d+)?)\b", str(value))
    if match:
        r = float(match.group(1))
        if 0 <= r <= 5:
            return r
    return None

def upgrade_image_quality(url):
    if not url:
        return None
    url = url.replace("&amp;", "&").replace("&quot;", "").strip("\"' ")
    url = re.sub(r"/h_\d+,q_\d+,w_\d+", "/h_1200,q_100,w_1200", url)
    url = re.sub(r"/h_\d+,w_\d+,q_\d+", "/h_1200,w_1200,q_100", url)
    url = re.sub(r"/w_\d+,q_\d+", "/w_1200,q_100", url)
    url = re.sub(r"/w_\d+", "/w_1200", url)
    return url

def extract_products_from_category(html):
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen_links = set()

    # 1. Try parsing directly from Myntra's embedded JSON window.__myx state
    for script in soup.find_all("script"):
        if script.string and "window.__myx" in script.string:
            try:
                json_str = script.string.split("window.__myx = ", 1)[1].rsplit(";", 1)[0].strip()
                data = json.loads(json_str)
                raw_products = data.get("searchData", {}).get("results", {}).get("products", [])
                if raw_products:
                    print(f"Extracted {len(raw_products)} products directly from __myx JSON!")
                    for item in raw_products:
                        landing_page_url = item.get("landingPageUrl", "")
                        product_url = normalize_url(landing_page_url)
                        if not product_url or product_url in seen_links:
                            continue
                        seen_links.add(product_url)

                        price = item.get("price")
                        mrp = item.get("mrp")
                        discount = item.get("discount")
                        if mrp == price or (mrp and price and mrp < price):
                            mrp = None
                            discount = None

                        products.append({
                            "name": clean(f"{item.get('brand', '')} {item.get('product', '')}".strip() or item.get("productName")),
                            "price": price,
                            "currency": "₹",
                            "original_price": mrp,
                            "discount": discount,
                            "ratings": item.get("rating"),
                            "description": clean(item.get("productAdditionalInfo") or item.get("product")),
                            "image_link": upgrade_image_quality(item.get("searchImage")),
                            "product_link": product_url,
                            "organization_id": "DealWallet",
                            "store_id": "Myntra",
                            "categories_id": "Fashion & Lifestyle",
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        })
                    return products
            except Exception as e:
                print("JSON parse fallback:", e)

    # 2. Fallback to HTML Cards parsing directly from category listing
    cards = soup.select("li.product-base")
    print(f"Listing cards found: {len(cards)}")
    for card in cards:
        try:
            link_node = card.select_one("a[href]")
            if not link_node:
                continue
            product_url = normalize_url(link_node.get("href"))
            if not product_url or product_url in seen_links:
                continue

            brand_node = card.select_one(".product-brand")
            name_node = card.select_one(".product-product")
            price_node = card.select_one(".product-discountedPrice") or card.select_one(".product-price")
            strike_node = card.select_one(".product-strike")
            discount_node = card.select_one(".product-discountPercentage")
            rating_node = card.select_one(".product-ratingsContainer") or card.select_one("[class*='ratings']")
            image_node = card.select_one("img")

            brand = clean(brand_node.get_text(" ", strip=True) if brand_node else "")
            name = clean(name_node.get_text(" ", strip=True) if name_node else "")
            full_name = f"{brand} {name}".strip() if brand else name
            if not full_name:
                continue

            price = parse_price(price_node.get_text(" ", strip=True) if price_node else None)
            if price is None:
                continue

            original_price = parse_price(strike_node.get_text(" ", strip=True) if strike_node else None)
            discount = parse_discount(discount_node.get_text(" ", strip=True) if discount_node else None)
            rating = parse_rating(rating_node.get_text(" ", strip=True) if rating_node else None)

            image_link = None
            if image_node:
                image_link = (
                    image_node.get("src")
                    or image_node.get("data-src")
                    or image_node.get("data-original")
                    or image_node.get("data-lazy-src")
                )
            if image_link:
                image_link = upgrade_image_quality(image_link)

            seen_links.add(product_url)
            products.append({
                "name": full_name,
                "price": price,
                "currency": "₹",
                "original_price": original_price if (original_price and original_price > price) else None,
                "discount": discount if (original_price and original_price > price) else None,
                "ratings": rating,
                "description": full_name,
                "image_link": image_link,
                "product_link": product_url,
                "organization_id": "DealWallet",
                "store_id": "Myntra",
                "categories_id": "Fashion & Lifestyle",
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
        except Exception as error:
            print("CARD ERROR:", error)

    return products

async def crawl_category(crawler, url):
    scroll_js = """
    (async()=>{
        for(let i=0; i<6; i++){
            window.scrollBy(0, 1000);
            await new Promise(r => setTimeout(r, 600));
        }
        await new Promise(r => setTimeout(r, 1500));
    })();
    """
    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for="css:li.product-base",
        js_code=scroll_js,
        page_timeout=35000,
        delay_before_return_html=2
    )
    result = await crawler.arun(url=url, config=config)
    if not result.success:
        print("ERROR fetching category:", result.error_message)
        return None
    return result.html

def save_files(products):
    with open("myntra_products.json", "w", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=2)
    fields = [
        "name", "price", "currency", "original_price", "discount",
        "ratings", "description", "image_link", "product_link",
        "organization_id", "store_id", "categories_id", "updated_at"
    ]
    with open("myntra_products.csv", "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(products)

def save_to_supabase(products):
    if not products:
        print("NO PRODUCTS TO SAVE")
        return
    unique_products = {p["product_link"]: p for p in products if p.get("product_link")}
    products = list(unique_products.values())
    print("SUPABASE PRODUCTS TO UPLOAD:", len(products))
    try:
        batch_size = 100
        for i in range(0, len(products), batch_size):
            batch = products[i:i + batch_size]
            supabase.table("products").upsert(batch, on_conflict="product_link").execute()
            print(f"UPLOADED: {i + 1} - {i + len(batch)}")
        print("SUPABASE UPLOAD COMPLETED SUCCESSFULLY!")
    except Exception as error:
        print("SUPABASE ERROR:", error)

async def main():
    browser_config = BrowserConfig(
        headless=True,
        viewport_width=1920,
        viewport_height=1080
    )
    all_products = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for category, category_url in CATEGORY_URLS.items():
            print("=" * 70)
            print("FETCHING CATEGORY:", category)
            print("URL:", category_url)
            print("=" * 70)
            html = await crawl_category(crawler, category_url)
            if not html:
                print("CATEGORY PAGE FAILED:", category)
                continue
            cat_products = extract_products_from_category(html)
            print(f"Retrieved {len(cat_products)} valid products for {category}")
            for p in cat_products[:5]:
                print(f"  ✓ {p['name']} | ₹{p['price']} | MRP:{p['original_price']} | Rating:{p['ratings']}")
            all_products.extend(cat_products)

    unique_products = {p["product_link"]: p for p in all_products if p.get("product_link")}
    final_products = list(unique_products.values())
    print("=" * 70)
    print("TOTAL UNIQUE PRODUCTS SCRAPED:", len(final_products))
    print("=" * 70)
    save_files(final_products)
    save_to_supabase(final_products)
    print("=" * 70)
    print("COMPLETED IN RECORD TIME")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
