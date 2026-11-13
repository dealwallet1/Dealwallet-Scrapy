

import asyncio

import csv

import json

import logging

import re
import requests

import sys

from datetime import datetime, timezone

from concurrent.futures import ProcessPoolExecutor

from urllib.parse import urljoin, urlparse, urlunparse

import scrapy

from bs4 import BeautifulSoup

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

# ============================================================

# CONFIGURATION

# ============================================================

BASE_URL = "https://miniklub.in"

COLLECTIONS = [

     {

          "url": "https://miniklub.in/collections/newborn-boys-tops",

          "category": "Baby & Kids",

          "limit": 40,

     },

     {

          "url": "https://miniklub.in/collections/kids-boys-top-bottom-sets",

          "category": "Baby & kids",

          "limit": 40,

     },

     {

          "url": "https://miniklub.in/collections/baby-care",

          "category": "Baby & kids",

          "limit": 40,

     },

     {

          "url": "https://miniklub.in/collections/bedding",

          "category": "Baby & kids",

          "limit": 40,

     },

     {

          "url": "https://miniklub.in/collections/accessories",

          "category": "Baby & Kids",

          "limit": 40,

     },

]

CURRENCY = "₹"

ORGANIZATION_ID = "Dealwallet"

STORE_ID = "MiniKlub"

MAX_PRODUCTS = 200

MAX_PAGES_PER_COLLECTION = 30

MAX_RETRIES = 3
PRODUCT_CONCURRENCY = 5  # Number of product pages processed at the same time

OUTPUT_JSON = "miniklub_products.json"

OUTPUT_CSV = "miniklub_products.csv"

SPIDER_JSON = "scrape_miniklub.json"

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

     if value is None:

          return ""

     value = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)

     value = re.sub(r"\s+", " ", value).strip()

     return value

def price_to_int(value):

     if value is None or isinstance(value, bool):

          return None

     if isinstance(value, (int, float)):

          number = float(value)

     else:

          text = str(value).strip().replace(",", "")

          match = re.search(r"\d+(?:\.\d+)?", text)

          if not match:

               return None

          try:

               number = float(match.group(0))

          except (TypeError, ValueError):

               return None

     if number <= 0:

          return None

     return int(round(number))

def normalize_url(url, base_url=BASE_URL):

     if not url:

          return None

     url = str(url).strip()

     if url.startswith("//"):

          url = "https:" + url

     url = urljoin(base_url, url)

     parsed = urlparse(url)

     return urlunparse(

          (parsed.scheme, parsed.netloc, parsed.path, "", "", "")

     )

def calculate_discount(price, original_price):

     if (

          price is None

          or original_price is None

          or original_price <= 0

          or price >= original_price

     ):

          return 0

     return int(round(((original_price - price) / original_price) * 100))

def valid_rating(value):

     if value is None:

          return None

     try:

          rating = float(value)

     except (TypeError, ValueError):

          return None

     if not 0 < rating <= 5:

          return None

     return round(rating, 1)

def walk_json(value):

     if isinstance(value, dict):

          yield value

          for child in value.values():

               yield from walk_json(child)

     elif isinstance(value, list):

          for child in value:

               yield from walk_json(child)

def extract_rating(soup):

     selectors = (

          ".product__block--custom_liquid .custom-review-badge .custom-badge-text",

          ".custom-review-badge .custom-badge-text",

          ".custom-review-badge[data-average-rating]",

          ".custom-jdgm-badge[data-average-rating]",

          "[data-average-rating]",

          "[itemprop='ratingValue']",

          ".jdgm-prev-badge__text",

          ".jdgm-prev-badge",

          ".spr-badge-caption",

     )

     for selector in selectors:

          element = soup.select_one(selector)

          if not element:

               continue

          raw = (

               element.get("data-average-rating")

               or element.get("content")

               or element.get_text(" ", strip=True)

          )

          match = re.search(r"(?<!\d)([0-5](?:\.\d+)?)", str(raw))

          if match:

               rating = valid_rating(match.group(1))

               if rating is not None:

                    return rating

     for script in soup.select('script[type="application/ld+json"]'):

          try:

               data = json.loads(script.string or script.get_text())

          except (json.JSONDecodeError, TypeError):

               continue

          for item in walk_json(data):

               aggregate = (

                    item.get("aggregateRating")

                    if isinstance(item, dict)

                    else None

               )

               if isinstance(aggregate, dict):

                    rating = valid_rating(aggregate.get("ratingValue"))

                    if rating is not None:

                         return rating

     return None

def extract_listing_products(html, collection_url):

     soup = BeautifulSoup(html or "", "html.parser")

     cards = soup.select(

          ".f-column.product-column .product-card.mk-pcard"

     )

     if not cards:

          cards = soup.select(".product-card.mk-pcard, .product-card")

     products = []

     seen = set()

     for card in cards:

          link_el = (

               card.select_one(

                    "h3.product-card__title a.reversed-link"

               )

               or card.select_one(

                    ".product-card__image-wrapper a[href*='/products/']"

               )

               or card.select_one("a[href*='/products/']")

          )

          if not link_el:

               continue

          product_link = normalize_url(

               link_el.get("href"),

               collection_url

          )

          if (

               not product_link

               or product_link in seen

               or "/products/" not in product_link

          ):

               continue

          seen.add(product_link)

          name = clean_text(link_el.get_text(" ", strip=True))

          if not name:

               name = clean_text(link_el.get("aria-label", ""))

          if not name:

               continue

          image_el = (

               card.select_one(".product-card__image--main img")

               or card.select_one(

                    ".product-card__image-wrapper img"

               )

               or card.select_one("img")

          )

          image_url = None

          if image_el:

               image_url = (

                    image_el.get("src")

                    or image_el.get("data-src")

               )

               if not image_url and image_el.get("srcset"):

                    image_url = (

                         image_el.get("srcset")

                         .split(",")[0]

                         .strip()

                         .split()[0]

                    )

               if image_url and image_url.startswith("data:"):

                    image_url = image_el.get("data-src")

               image_url = normalize_url(

                    image_url,

                    collection_url

               )

          sale_el = card.select_one(

               ".f-price__sale .f-price-item--sale, "

               ".f-price-item--sale"

          )

          original_el = card.select_one(

               ".f-price__sale .f-price-item--regular s, "

               ".f-price__sale s, "

               ".f-price-item--regular s"

          )

          listing_price = (

               price_to_int(sale_el.get_text(" ", strip=True))

               if sale_el else None

          )

          listing_original = (

               price_to_int(original_el.get_text(" ", strip=True))

               if original_el else None

          )

          products.append({

               "name": name,

               "product_link": product_link,

               "image_link": image_url,

               "listing_rating": extract_rating(card),

               "listing_price": listing_price,

               "listing_original_price": listing_original,

          })

     return products

def extract_product_name(soup, fallback_name=""):

     title_el = (

          soup.select_one(

               ".product__block--title h1.product__title"

          )

          or soup.select_one(

               ".product__block--title a.product__title h2"

          )

          or soup.select_one("h1.product__title")

          or soup.select_one(".product__title h1")

          or soup.select_one("h1.product-title")

          or soup.select_one("h1")

     )

     name = (

          clean_text(title_el.get_text(" ", strip=True))

          if title_el else ""

     )

     return name or fallback_name

def extract_html_prices(soup):

     sale_containers = soup.select(".f-price__sale")

     for sale_container in sale_containers:

          sale = sale_container.select_one(

               ".f-price-item--sale"

          )

          original = sale_container.select_one(

               ".f-price-item--regular s"

          )

          price = (

               price_to_int(

                    sale.get("content")

                    or sale.get_text(" ", strip=True)

               )

               if sale else None

          )

          original_price = (

               price_to_int(

                    original.get("content")

                    or original.get_text(" ", strip=True)

               )

               if original else None

          )

          if price is not None and original_price is not None:

               return price, original_price

     roots = [

          soup.select_one(".product__info-container .f-price"),

          soup.select_one(".product__info .f-price"),

          soup.select_one(".product__price"),

          soup.select_one(".price"),

          soup,

     ]

     for root in roots:

          if not root:

               continue

          sale = root.select_one(

               ".f-price__sale .f-price-item--sale, "

               ".f-price-item--sale, "

               ".price-item--sale, "

               ".price__sale .price-item--sale"

          )

          original = root.select_one(

               ".f-price__sale .f-price-item--regular s, "

               ".f-price__sale s, "

               ".f-price-item--regular s, "

               ".price__sale s, "

               "s.price-item--regular, "

               "del .price-item"

          )

          price = (

               price_to_int(

                    sale.get("content")

                    or sale.get_text(" ", strip=True)

               )

               if sale else None

          )

          original_price = (

               price_to_int(

                    original.get("content")

                    or original.get_text(" ", strip=True)

               )

               if original else None

          )

          if price is not None and original_price is not None:

               return price, original_price

     return None, None


def is_valid_description(value):

  """Reject empty text, heading-only labels and product-code-only values."""

  text = clean_text(value)

  if not text:

    return False

  normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

  heading_labels = {

    "why parents love it",

    "product details",

    "product detail",

    "description",

    "details",

    "features",

    "key features",

    "ingredients",

    "how to use",

    "how to use it",

    "specifications",

    "specification",

  }

  if normalized in heading_labels:

    return False

  # Reject product-code-only values such as 50NBCP1305.

  if re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{6,}", text):

    return False

  # Headings are often short; don't treat a short label as a description.

  if len(text) < 20 and not re.search(r"[.!?]", text):

    return False

  return True

def extract_description(soup, shopify_description=None):

  """Extract real descriptive paragraphs; skip accordion headings and SKU text."""

  primary_selectors = (

    ".product__description",

    ".product-description",

    ".product__accordion .accordion__content",

    ".product__info-container [itemprop='description']",

    "[itemprop='description']",

    ".product__details",

    'details.accordion-details[id^="Details-collapsible_tab"] .accordion-details__content.rte',

  )

  seen = set()

  for selector in primary_selectors:

    for element in soup.select(selector):

      paragraphs = []

      for paragraph in element.find_all("p"):

        if paragraph.find_parent(class_="metafield-rich_text_field"):

          continue

        value = clean_text(paragraph.get_text(" ", strip=True))

        key = value.lower()

        if value and key not in seen and is_valid_description(value):

          paragraphs.append(value)

          seen.add(key)

      if paragraphs:

        return " ".join(paragraphs).strip()

  # Try the MiniKlub brand/metafield section, but do not return heading-only text.

  brand_selectors = (

    "div.accordion-details__content.rte.text-subtext div.metafield-rich_text_field",

    "div.accordion-details__content.rte div.metafield-rich_text_field",

    "div.metafield-rich_text_field",

  )

  for selector in brand_selectors:

    brand_section = soup.select_one(selector)

    if not brand_section:

      continue

    paragraphs = []

    for paragraph in brand_section.find_all("p"):

      value = clean_text(paragraph.get_text(" ", strip=True))

      if value and is_valid_description(value):

        paragraphs.append(value)

      if len(paragraphs) >= 2:

        break

    if paragraphs:

      return " ".join(paragraphs).strip()

  # Shopify's product .js endpoint is a fallback when the visible HTML has

  # no usable description. Parse its HTML so heading labels/SKUs are filtered.

  if shopify_description:

    shopify_soup = BeautifulSoup(str(shopify_description), "html.parser")

    paragraphs = []

    for paragraph in shopify_soup.find_all("p"):

      value = clean_text(paragraph.get_text(" ", strip=True))

      if value and is_valid_description(value):

        paragraphs.append(value)

    if not paragraphs:

      value = clean_text(shopify_soup.get_text(" ", strip=True))

      if is_valid_description(value):

        paragraphs.append(value)

    if paragraphs:

      return " ".join(paragraphs).strip()

  return ""

def fetch_product_html_source(url):
     """Fetch the original product-page HTML response (not rendered DOM or .js JSON)."""
     response = requests.get(
          url,
          headers=HEADERS,
          timeout=45,
     )
     response.raise_for_status()
     return response.text


def choose_price_values(raw_product_html):
     """Extract both prices only from price elements in the raw product HTML."""
     soup = BeautifulSoup(raw_product_html or "", "html.parser")
     price, original_price = extract_html_prices(soup)

     if (
          price is not None
          and original_price is not None
          and price > 0
          and original_price > price
     ):
          return price, original_price, "product_html", "product_html"

     return None, None, None, None

def validate_product_values(

     price,

     original_price,

     rating,

     name,

     description,

     image_link,

     product_link,

):

     issues = []

     if price is None or price <= 0:

          issues.append("Missing or invalid selling price")

     if original_price is None or original_price <= 0:

          issues.append("Missing or invalid original price")

     if (

          price is not None

          and original_price is not None

          and original_price <= price

     ):

          issues.append(

               "Original price must be greater than selling price "

               "(not a discounted product)"

          )

     if rating is not None and valid_rating(rating) is None:

          issues.append("Rating is outside 0-5 range")

     if not clean_text(name):

          issues.append("Missing product name")

     if not clean_text(description):

          issues.append("Missing product description")

     if (

          not image_link

          or not str(image_link).startswith(

               ("http://", "https://")

          )

     ):

          issues.append("Missing or invalid image link")

     if (

          not product_link

          or not str(product_link).startswith(

               ("http://", "https://")

          )

          or "/products/" not in str(product_link)

     ):

          issues.append("Missing or invalid product link")

     return issues

def extract_product_details(

     html,

     listing_product,

     category,

     raw_product_html,

):

     soup = BeautifulSoup(html or "", "html.parser")

     name = extract_product_name(soup, "")

     if not name:

          name = listing_product.get("name", "")

     # Prices are extracted only from the original product-page HTML response.
     # No listing-page price, Shopify .js JSON, JSON-LD, or browser-rendered DOM fallback.
     price, original_price, price_source, original_source = (
          choose_price_values(raw_product_html)
     )

     rating = extract_rating(soup)

     if rating is None:

          rating = valid_rating(

               listing_product.get("listing_rating")

          )

     # Description comes from the HTML paragraph extraction only.

     # Shopify description is not appended because it can contain

     # trailing product codes or unrelated content.

     description = extract_description(soup)

     image_link = listing_product.get("image_link")

     if not image_link:

          image_el = soup.select_one(

               ".product__media img, "

               ".product__media-list img, "

               ".product-gallery img, "

               "meta[property='og:image']"

          )

          if image_el:

               image_value = (

                    image_el.get("content")

                    or image_el.get("src")

                    or image_el.get("data-src")

               )

               if not image_value and image_el.get("srcset"):

                    image_value = (

                         image_el.get("srcset")

                         .split(",")[0]

                         .strip()

                         .split()[0]

                    )

               image_link = normalize_url(

                    image_value,

                    listing_product["product_link"],

               )

     image_link = normalize_url(

          image_link,

          listing_product.get("product_link", BASE_URL),

     )

     product_link = normalize_url(

          listing_product.get("product_link")

     )

     issues = validate_product_values(

          price,

          original_price,

          rating,

          name,

          description,

          image_link,

          product_link,

     )

     if issues:

          return None, {

               "status": (

                    "MISMATCH"

                    if "disagree" in " ".join(issues).lower()

                    else "MISSING/INVALID"

               ),

               "issue": "; ".join(issues),

          }

     product = {

          "name": name,

          "price": int(price),

          "currency": CURRENCY,

          "original_price": int(original_price),

          "discount": calculate_discount(

               price,

               original_price,

          ),

          "ratings": rating,

          "description": description,

          "image_link": image_link,

          "product_link": product_link,

          "organization_id": ORGANIZATION_ID,

          "store_id": STORE_ID,

          "categories_id": category,

          "created_at": datetime.now(

               timezone.utc

          ).isoformat(),

     }

     print(

          f"[VALID] {name} | price={price} ({price_source}) | "

          f"original={original_price} ({original_source}) | "

          f"rating={rating}"

     )

     return product, None

def save_json(products):

     with open(

          OUTPUT_JSON,

          "w",

          encoding="utf-8",

     ) as f:

          json.dump(

               products,

               f,

               ensure_ascii=False,

               indent=2,

          )

     print(f"JSON generated: {OUTPUT_JSON}")

def save_csv(products):

     with open(

          OUTPUT_CSV,

          "w",

          newline="",

          encoding="utf-8-sig",

     ) as f:

          writer = csv.DictWriter(

               f,

               fieldnames=CSV_FIELDS,

               extrasaction="ignore",

          )

          writer.writeheader()

          writer.writerows(products)

     print(f"CSV generated: {OUTPUT_CSV}")

async def crawl_with_retry(

     crawler,

     url,

     config,

     label,

):

     for attempt in range(1, MAX_RETRIES + 1):

          try:

               result = await crawler.arun(

                    url=url,

                    config=config,

               )

               if result.success and result.html:

                    return result

               error = getattr(

                    result,

                    "error_message",

                    "Empty page HTML",

               )

               print(

                    f"[{label} RETRY {attempt}/{MAX_RETRIES}] "

                    f"{url}: {error}"

               )

          except Exception as exc:

               print(

                    f"[{label} RETRY {attempt}/{MAX_RETRIES}] "

                    f"{url}: {exc}"

               )

          if attempt < MAX_RETRIES:

               await asyncio.sleep(attempt * 2)

     return None

async def scrape_collection_pages(

     crawler,

     collection,

     global_seen,

     validation_issues,

):

     collection_url = collection["url"]

     category = collection["category"]

     limit = collection["limit"]

     products = []

     page = 1

     # Wait for the body, not a product-link selector.

     # This avoids long selector waits on blocked listing pages.

     listing_config = CrawlerRunConfig(

          cache_mode=CacheMode.BYPASS,

          wait_for="css:body",

          page_timeout=180000,

     )

     product_config = CrawlerRunConfig(

          cache_mode=CacheMode.BYPASS,

          wait_for="css:body",

          page_timeout=180000,

     )


     while (

          len(products) < limit

          and page <= MAX_PAGES_PER_COLLECTION

     ):

          page_url = (

               collection_url

               if page == 1

               else f"{collection_url}?page={page}"

          )

          print(

               f"\n[COLLECTION PAGE] {page} | {page_url}"

          )

          result = await crawl_with_retry(

               crawler,

               page_url,

               listing_config,

               "LISTING",

          )

          if not result:

               print(

                    f"[STOP] Could not load listing page: {page_url}"

               )

               break

          candidates = extract_listing_products(

               result.html,

               collection_url,

          )

          print(f"[LISTING PRODUCTS] {len(candidates)}")

          if not candidates:

               print(

                    "[NO PRODUCTS] No product cards found; "

                    "stopping this collection."

               )

               break

          new_candidates = [

               p for p in candidates

               if p["product_link"] not in global_seen

          ]

          if not new_candidates:

               print(

                    "[NO NEW PRODUCTS] Stopping pagination."

               )

               break

          # Process product pages concurrently in small batches.
          # This keeps requests controlled instead of opening every page at once.
          semaphore = asyncio.Semaphore(PRODUCT_CONCURRENCY)

          async def process_one_product(item, position):
               link = item["product_link"]
               async with semaphore:
                    print(
                         f"[PRODUCT {position}/{limit}] {link}"
                    )

                    product_result = await crawl_with_retry(
                         crawler,
                         link,
                         product_config,
                         "PRODUCT",
                    )

                    if not product_result:
                         return None, {
                              "status": "RETRY_FAILED",
                              "issue": "Product page failed after retries",
                         }

                    try:
                         raw_product_html = await asyncio.to_thread(
                              fetch_product_html_source,
                              link,
                         )
                    except Exception as exc:
                         print(f"[RAW HTML FAILED] {link}: {exc}")
                         return None, {
                              "status": "HTML_FETCH_FAILED",
                              "issue": str(exc),
                         }

                    return extract_product_details(
                         product_result.html,
                         item,
                         category,
                         raw_product_html,
                    )

          # Run up to PRODUCT_CONCURRENCY product pages at once.
          for batch_start in range(0, len(new_candidates), PRODUCT_CONCURRENCY):
               if len(products) >= limit:
                    break

               batch = new_candidates[
                    batch_start:batch_start + PRODUCT_CONCURRENCY
               ]

               # Mark links before starting tasks to avoid duplicate processing.
               for item in batch:
                    global_seen.add(item["product_link"])

               results = await asyncio.gather(
                    *[
                         process_one_product(
                              item,
                              len(products) + batch_start + index + 1,
                         )
                         for index, item in enumerate(batch)
                    ],
                    return_exceptions=True,
               )

               for item, result in zip(batch, results):
                    if len(products) >= limit:
                         break

                    if isinstance(result, Exception):
                         validation_issues.append({
                              "status": "PRODUCT_TASK_FAILED",
                              "issue": str(result),
                         })
                         print(f"[PRODUCT TASK FAILED] {item['product_link']}: {result}")
                         continue

                    product, issue = result

                    if issue:
                         validation_issues.append(issue)
                         print(
                              f"[NOT INSERTED - {issue['status']}] "
                              f"{item.get('name')}: {issue['issue']}"
                         )
                         continue

                    if product:
                         products.append(product)
                         print(
                              f"[SAVED] {product['name']} | "
                              f"₹{product['price']} | "
                              f"MRP ₹{product['original_price']} | "
                              f"{product['discount']}% OFF | "
                              f"Rating: {product['ratings']}"
                         )

          print(

               f"[COLLECTION VALID TOTAL] "

               f"{len(products)}/{limit}"

          )

          page += 1

     return products

async def main():

     browser_config = BrowserConfig(

          headless=True,

          verbose=False,

          headers=HEADERS,

     )

     global_seen = set()

     all_products = []

     validation_issues = []

     async with AsyncWebCrawler(

          config=browser_config

     ) as crawler:

          for collection in COLLECTIONS:

               if len(all_products) >= MAX_PRODUCTS:

                    break

               print("\n==================================================")

               print(

                    f"SCRAPING CATEGORY: {collection['category']}"

               )

               print(

                    f"COLLECTION URL: {collection['url']}"

               )

               print(

                    f"TARGET: {collection['limit']} valid products"

               )

               print("==================================================")

               collection_products = (

                    await scrape_collection_pages(

                         crawler,

                         collection,

                         global_seen,

                         validation_issues,

                    )

               )

               all_products.extend(collection_products)

               print(

                    f"[GLOBAL VALID TOTAL] "

                    f"{len(all_products)}/{MAX_PRODUCTS}"

               )

     save_json(all_products)

     save_csv(all_products)

     print("\n========================================")

     print("MINIKLUB SCRAPING COMPLETED")

     print("========================================")

     print(f"Total valid products: {len(all_products)}")

     print(

          f"Products rejected by validation: "

          f"{len(validation_issues)}"

     )

     print(f"JSON: {OUTPUT_JSON}")

     print(f"CSV: {OUTPUT_CSV}")

     return all_products

def run_miniklub_scraper():

     logging.basicConfig(

          level=logging.INFO,

          format="%(asctime)s [%(levelname)s] %(message)s",

          stream=sys.stdout,

          force=True,

     )

     if sys.platform.startswith("win"):

          asyncio.set_event_loop_policy(

               asyncio.WindowsProactorEventLoopPolicy()

          )

     logging.info("=" * 60)

     logging.info("MiniKlub browser process started.")

     logging.info("=" * 60)

     try:

          products = asyncio.run(main())

          logging.info(

               "MiniKlub browser process finished. "

               "Products returned: %s",

               len(products),

          )

          return products

     except Exception:

          logging.exception(

               "MiniKlub browser process failed."

          )

          raise

class MiniKlubSpider(scrapy.Spider):

     name = "miniklub"

     allowed_domains = [

          "miniklub.in",

          "ww&#x77;*\\\\.*&#x6D;iniklub.in",

     ]

     async def start(self):

          self.logger.info(

               "Starting MiniKlub scraper through Scrapy..."

          )

          loop = asyncio.get_running_loop()

          with ProcessPoolExecutor(max_workers=1) as executor:

               products = await loop.run_in_executor(

                    executor,

                    run_miniklub_scraper,

               )

          with open(

               SPIDER_JSON,

               "w",

               encoding="utf-8",

          ) as f:

               json.dump(

                    products,

                    f,

                    ensure_ascii=False,

                    indent=2,

               )

          self.logger.info(

               "MiniKlub scraper returned %s products",

               len(products),

          )

          for product in products:

               if not isinstance(product, dict):

                    continue

               if (

                    not product.get("image_link")

                    or not product.get("product_link")

               ):

                    continue

               yield product

          self.logger.info("MiniKlub spider completed")

if __name__ == "__main__":

     logging.basicConfig(

          level=logging.INFO,

          format="%(asctime)s [%(levelname)s] %(message)s",

          stream=sys.stdout,

          force=True,

     )

     data = run_miniklub_scraper()

     with open(

          SPIDER_JSON,

          "w",

          encoding="utf-8",

     ) as f:

          json.dump(

               data,

               f,

               ensure_ascii=False,

               indent=2,

          )

     print(f"Saved in {SPIDER_JSON}")

     print("Total products:", len(data))