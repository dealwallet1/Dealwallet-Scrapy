import asyncio

import random

import json

import uuid

import re

from bs4 import BeautifulSoup

from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler

categories = [  

    "Fashion&Lifestyle",    

]

category_labels = {    

    "Fashion&Lifestyle": "Fashion & Lifestyle",    

}

def extract_int(text):

    if not text:

        return None

    text = str(text).replace(",", "")

    m = re.search(r"\d+", text)

    return int(m.group()) if m else None

def extract_rating_float(text):

    if not text:

        return None

    m = re.search(r"\d+(\.\d+)?", str(text))

    return float(m.group()) if m else None

def normalize_text(text):

    if not text:

        return ""

    return re.sub(r"\s+", " ", text.lower()).strip()

async def scrape_myntra_categories():

    user_agents = [

        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",

        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128.0.0.0",

        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",

    ]

    all_products = []

    seen_basic_keys = set()

    seen_descriptions = set()

    async with AsyncWebCrawler(verbose=True) as crawler:

        crawler.browser_config = {

            "headless": True,

            "javascript": True,

            "user_agent": random.choice(user_agents),

        }

        crawler.crawler_run_config = {

            "wait_until": "networkidle",

            "timeout": 60000,

            "delay": 2000,

            "wait_for": {"selector": "li.product-base", "timeout": 30000},

        }

        for category in categories:

            label = category_labels.get(category, category)

            base_url = "https://www.myntra.com/men-topwear"

            for page in range(1, 5):

                url = f"{base_url}?p={page}"

                print(f"[INFO] Scraping {label} - Page {page}")

                result = await crawler.arun(url=url)

                if not result.success:

                    continue

                soup = BeautifulSoup(result.html, "html.parser")

                product_divs = soup.find_all("li", class_="product-base")

                if not product_divs:

                    break

                for product in product_divs:

                    try:

                        brand = product.find("h3", class_="product-brand")

                        title = product.find("h4", class_="product-product")

                        if not brand or not title:

                            continue

                        name = normalize_text(f"{brand.text} {title.text}")

                        price_tag = product.find("span", class_="product-discountedPrice")

                        price = extract_int(price_tag.text) if price_tag else None

                        if price is None:

                            continue

                        original_price_tag = product.find("span", class_="product-strike")

                        original_price = extract_int(original_price_tag.text) if original_price_tag else None

                        discount_tag = product.find("span", class_="product-discountPercentage")

                        discount = extract_int(discount_tag.text) if discount_tag else None

                        image = product.select_one("picture img")

                        image_link = image.get("src") if image else None

                        if not image_link:

                            continue

                        link_tag = product.find("a", href=True)

                        product_url = (

                            urljoin("https://www.myntra.com", link_tag["href"])

                            if link_tag

                            else None

                        )

                        if not product_url:

                            continue

                        rating = None

                        rating_span = product.select_one("div.product-ratingsContainer span")

                        if rating_span:

                            rating = extract_rating_float(rating_span.text)

                        basic_key = (product_url, name, price)

                        if basic_key in seen_basic_keys:

                            continue

                        seen_basic_keys.add(basic_key)

                        all_products.append(

                            {

                                "name": name,

                                "price": price,

                                "currency": "₹",

                                "original_price": original_price,

                                "discount": discount,

                                "ratings": rating,

                                "description": None,

                                "image_link": image_link,

                                "product_link": product_url,

                                "organization_id": "Dealwallet",

                                "store_id": "Myntra",

                                "categories_id": label,

                            }

                        )

                    except Exception as e:

                        print(f"[ERROR] {e}")

        final_products = []

        for product in all_products:

            detail = await crawler.arun(url=product["product_link"])

            if not detail.success:

                continue

            soup = BeautifulSoup(detail.html, "html.parser")

            desc_tag = soup.select_one("p.pdp-product-description-content")

            description = normalize_text(

                desc_tag.get_text(" ", strip=True) if desc_tag else ""

            )

            if not description or len(description) < 30:

                continue

            if description in seen_descriptions:

                continue

            seen_descriptions.add(description)

            product["description"] = description

            final_products.append(product)

    with open("scrape_myntra_final2.json", "w", encoding="utf-8") as f:

        json.dump(final_products, f, indent=4, ensure_ascii=False)

    print(f"✅ Saved {len(final_products)} unique products")

    return final_products

if __name__ == "__main__":

    asyncio.run(scrape_myntra_categories())