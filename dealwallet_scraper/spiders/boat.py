import asyncio
import csv
import json
import logging
import re
import sys
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin, urlparse, urlunparse
import scrapy
from bs4 import BeautifulSoup
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    JsonCssExtractionStrategy,
)


WATCH_URL = "https://www.boat-lifestyle.com/collections/smart-watches"
EARBUD_URL = "https://www.boat-lifestyle.com/collections/true-wireless-earbuds"
WATCH_LIMIT = 30
EARBUD_LIMIT = 30
CURRENCY = "₹"
ORGANIZATION_ID = "Dealwallet"
STORE_ID = "Boat"
OUTPUT_JSON = "boat_products.json"
OUTPUT_CSV = "boat_products.csv"


def clean_text(value):
   if not value:
      return ""
   value = str(value)
   value = value.replace("/", "")
   value = value.replace('"', "")
   value = value.replace("\\", "")
   value = re.sub(r"\s+", " ", value)
   return value.strip()



def price_to_int(value):
   if value is None:
      return None
   value = str(value).strip()
   if not value:
      return None
   value = value.replace(
      ",",
      ""
   )
   match = re.search(
      r"\d+(?:\.\d+)?",
      value
   )
   if not match:
      return None
   try:
      return int(
         float(
            match.group(0)
         )
      )
   except Exception:
      return None



def discount_to_int(value):
   if value is None:
      return None
   match = re.search(
      r"\d+(?:\.\d+)?",
      str(value)
   )
   if not match:
      return None
   try:
      return int(
         float(
            match.group(0)
         )
      )
   except Exception:
      return None




def clean_rating(value):
   if value is None:
      return None
   match = re.search(
      r"\d+(?:\.\d+)?",
      str(value)
   )
   if not match:
      return None
   try:
      rating = float(
         match.group(0)
      )
      if rating == 0:
         return None
      return round(
         rating,
         1
      )
   except Exception:
      return None



def normalize_product_link(
   link,
   base_url
):
   if not link:
      return ""
   link = urljoin(
      base_url,
      str(link).strip()
   )
   parsed = urlparse(
      link
   )
   parsed = parsed._replace(
      query="",
      fragment=""
   )
   return urlunparse(
      parsed
   ).rstrip("/")




def normalize_image_url(
   image_url,
   base_url
):
   if not image_url:
      return ""
   image_url = str(
      image_url
   ).strip()
   if image_url.startswith("//"):
      image_url = (
         "https:"
         + image_url
      )
   return urljoin(
      base_url,
      image_url
   )




def make_page_loader_js(limit):
      return f"""
async () => {{
      const TARGET = {limit};
      const START_TIME = Date.now();
      const MAX_TIME = 360000;
      function getProductCount() {{
            const links = Array.from(
                  document.querySelectorAll("product-item a[href*='/products/']")
            );
            return new Set(links.map(link => link.href)).size;
      }}
      function getScrollHeight() {{
            return Math.max(
                  document.body.scrollHeight,
                  document.documentElement.scrollHeight
            );
      }}
      function findLoadMore() {{
            const elements = Array.from(
                  document.querySelectorAll(
                        "button, a, div[role='button'], span[role='button']"
                  )
            );
            for (const element of elements) {{
                  const text = (
                        element.innerText ||
                        element.textContent ||
                        ""
                  ).trim().toLowerCase();
                  const aria = (
                        element.getAttribute("aria-label") ||
                        ""
                  ).trim().toLowerCase();
                  const title = (
                        element.getAttribute("title") ||
                        ""
                  ).trim().toLowerCase();
                  const combined = text + " " + aria + " " + title;
                  if (
                        combined.includes("load more") ||
                        combined.includes("view more") ||
                        combined.includes("show more") ||
                        combined.includes("loadmore") ||
                        combined.includes("viewmore")
                  ) {{
                        return element;
                  }}
            }}
            return null;
      }}
      async function waitForNewProducts(previousCount, waitTime) {{
            const waitStart = Date.now();
            while (
                  Date.now() - waitStart < waitTime &&
                  Date.now() - START_TIME < MAX_TIME
            ) {{
                  const currentCount = getProductCount();
                  if (currentCount > previousCount) {{
                        return currentCount;
                  }}
                  await new Promise(resolve => setTimeout(resolve, 1000));
            }}
            return getProductCount();
      }}
      let noChangeRounds = 0;
      console.log(
            "BOAT LISTING LOADER START:",
            getProductCount(),
            "/",
            TARGET
      );
      while (Date.now() - START_TIME < MAX_TIME) {{
            const currentCount = getProductCount();
            console.log(
                  "BOAT LISTING COUNT:",
                  currentCount,
                  "/",
                  TARGET,
                  "| TIME:",
                  Math.round((Date.now() - START_TIME) / 1000),
                  "seconds"
            );
            if (currentCount >= TARGET) {{
                  console.log("BOAT TARGET REACHED:", currentCount);
                  break;
            }}
            const loadMore = findLoadMore();
            if (loadMore) {{
                  try {{
                        console.log("BOAT LOAD MORE BUTTON FOUND");
                        loadMore.scrollIntoView({{
                              behavior: "instant",
                              block: "center"
                        }});
                        await new Promise(resolve => setTimeout(resolve, 1000));
                        loadMore.click();
                        console.log("BOAT LOAD MORE CLICKED");
                        const afterClickCount = await waitForNewProducts(
                              currentCount,
                              15000
                        );
                        console.log(
                              "BOAT AFTER LOAD MORE:",
                              afterClickCount,
                              "/",
                              TARGET
                        );
                        if (afterClickCount > currentCount) {{
                              noChangeRounds = 0;
                              continue;
                        }}
                  }} catch (e) {{
                        console.log("BOAT LOAD MORE ERROR:", String(e));
                  }}
            }}
            console.log("BOAT SCROLLING LISTING PAGE");
            const beforeCount = getProductCount();
            const beforeHeight = getScrollHeight();
            window.scrollTo(0, beforeHeight);
            await new Promise(resolve => setTimeout(resolve, 2500));
            window.scrollTo(0, getScrollHeight());
            const afterCount = await waitForNewProducts(
                  beforeCount,
                  15000
            );
            const afterHeight = getScrollHeight();
            console.log(
                  "BOAT AFTER SCROLL:",
                  afterCount,
                  "/",
                  TARGET,
                  "| HEIGHT:",
                  afterHeight
            );
            if (
                  afterCount > beforeCount ||
                  afterHeight > beforeHeight
            ) {{
                  noChangeRounds = 0;
                  continue;
            }}
            noChangeRounds++;
            console.log(
                  "BOAT NO CHANGE:",
                  noChangeRounds,
                  "| COUNT:",
                  afterCount
            );
            if (noChangeRounds >= 2) {{
                  console.log("BOAT EXTRA LISTING WAIT");
                  await new Promise(resolve => setTimeout(resolve, 5000));
                  window.scrollTo(0, getScrollHeight());
                  const finalCount = await waitForNewProducts(
                        afterCount,
                        20000
                  );
                  if (finalCount > afterCount) {{
                        noChangeRounds = 0;
                        continue;
                  }}
            }}
            if (noChangeRounds >= 4) {{
                  console.log(
                        "BOAT LISTING NO MORE PRODUCTS:",
                        getProductCount()
                  );
                  break;
            }}
      }}
      window.scrollTo(0, 0);
      await new Promise(resolve => setTimeout(resolve, 1000));
      console.log(
            "BOAT LISTING LOADER FINISHED:",
            getProductCount(),
            "/",
            TARGET,
            "| TIME:",
            Math.round((Date.now() - START_TIME) / 1000),
            "seconds"
      );
      return true;
}}
"""



product_schema = {
   "name":
      "boat_product",
   "baseSelector":
      "body",
   "fields": [
      {
         "name":
            "description",
         "selector":
            "h2.pdp-title-extra-info small",
         "type":
            "text"
      },
      {
         "name":
            "product_image",
         "selector":
            "div.product__media-item[data-media-type='image'] div.product__media-image-wrapper img",
         "type":
            "attribute",
         "attribute":
            "src"
      }
   ]
}





def extract_watch_products(
   html,
   base_url
):
   products = []
   if not html:
      return products
   soup = BeautifulSoup(
      html,
      "html.parser"
   )
   cards = soup.select(
      "product-item"
   )
   print(
      f"[WATCH PRODUCT CARDS] "
      f"{len(cards)}"
   )
   seen_links = set()
   for card in cards:
      if len(products) >= WATCH_LIMIT:
         break


    
      link_element = card.select_one(
         "a[href*='/products/']"
      )
      if not link_element:
         continue
      product_link = normalize_product_link(
         link_element.get("href"),
         base_url
      )
      if not product_link:
         continue
      duplicate_key = (
         product_link.lower()
      )
      if duplicate_key in seen_links:
         continue



  
      name_element = card.select_one(
         ".product-item-meta__title.tile-title"
      )
      if not name_element:
         name_element = card.select_one(
            ".product-item-meta__title, "
            ".tile-title, "
            ".product-title, "
            ".product-card__title, "
            ".card__heading, "
            "h2, h3"
         )
      name = clean_text(
         name_element.get_text(
            " ",
            strip=True
         )
         if name_element
         else ""
      )



    
      price_element = card.select_one(
         ".product-item-meta__price-list-container "
         ".price.price--highlight.product-card-price"
      )
      if not price_element:
         price_element = card.select_one(
            ".price--highlight, "
            ".product-card-price, "
            ".sale-price, "
            ".price"
         )
      price = (
         price_element.get_text(
            " ",
            strip=True
         )
         if price_element
         else ""
      )



  
      original_element = card.select_one(
         ".product-item-meta__price-list-container "
         ".price.price--compare.line-through"
      )
      if not original_element:
         original_element = card.select_one(
            ".price--compare, "
            ".compare-at-price, "
            ".product-item__compare-price, "
            "s, del"
         )
      original_price = (
         original_element.get_text(
            " ",
            strip=True
         )
         if original_element
         else ""
      )



  
      discount_element = card.select_one(
         ".product-item-meta__price-list-container "
         "p.m-0.off"
      )
      if not discount_element:
         discount_element = card.select_one(
            ".off, "
            ".discount, "
            ".discount-label, "
            "[class*='discount']"
         )
      discount = (
         discount_element.get_text(
            " ",
            strip=True
         )
         if discount_element
         else ""
      )



   
      rating_element = card.select_one(
         ".rating__stars, "
         ".rating, "
         "[data-rating]"
      )
      rating = ""
      if rating_element:
         rating = (
            rating_element.get(
               "data-rating"
            )
            or rating_element.get(
               "aria-label"
            )
            or rating_element.get_text(
               " ",
               strip=True
            )
         )


 
      products.append(
         {
            "name":
               name,
            "product_link":
               product_link,
            "price":
               price,
            "original_price":
               original_price,
            "discount":
               discount,
            "ratings":
               rating
         }
      )
      seen_links.add(
         duplicate_key
      )
      print(
         f"[WATCH {len(products)}/{WATCH_LIMIT}] "
         f"{name}"
      )
   return products




def extract_earbud_products(
   html,
   base_url
):
   products = []
   if not html:
      return products
   soup = BeautifulSoup(
      html,
      "html.parser"
   )
   cards = soup.select(
      "product-item"
   )
   print(
      f"[EARBUD PRODUCT CARDS] "
      f"{len(cards)}"
   )
   seen_links = set()
   for card in cards:
      if len(products) >= EARBUD_LIMIT:
         break



    
      link_element = card.select_one(
         "a[href*='/products/']"
      )
      if not link_element:
         continue
      product_link = normalize_product_link(
         link_element.get("href"),
         base_url
      )
      if not product_link:
         continue
      duplicate_key = (
         product_link.lower()
      )
      if duplicate_key in seen_links:
         continue



 
      name_element = card.select_one(
         ".product-item-meta__title, "
         ".tile-title, "
         ".product-title, "
         ".product-card__title, "
         ".card__heading, "
         "h2, h3"
      )
      name = clean_text(
         name_element.get_text(
            " ",
            strip=True
         )
         if name_element
         else link_element.get_text(
            " ",
            strip=True
         )
      )


  
      price_element = card.select_one(
         ".price--highlight, "
         ".product-card-price, "
         ".sale-price, "
         ".price, "
         ".product-item__price"
      )
      price = (
         price_element.get_text(
            " ",
            strip=True
         )
         if price_element
         else ""
      )


   
      original_element = card.select_one(
         ".price--compare, "
         ".compare-at-price, "
         ".product-item__compare-price, "
         "s, del"
      )
      original_price = (
         original_element.get_text(
            " ",
            strip=True
         )
         if original_element
         else ""
      )



      discount_element = card.select_one(
         ".off, "
         ".discount, "
         ".discount-label, "
         ".product-item__discount, "
         "[class*='discount']"
      )
      discount = (
         discount_element.get_text(
            " ",
            strip=True
         )
         if discount_element
         else ""
      )



      rating_element = card.select_one(
         ".rating__stars, "
         ".rating, "
         "[data-rating]"
      )
      rating = ""
      if rating_element:
         rating = (
            rating_element.get(
               "data-rating"
            )
            or rating_element.get(
               "aria-label"
            )
            or rating_element.get_text(
               " ",
               strip=True
            )
         )


   
      products.append(
         {
            "name":
               name,
            "product_link":
               product_link,
            "price":
               price,
            "original_price":
               original_price,
            "discount":
               discount,
            "ratings":
               rating
         }
      )
      seen_links.add(
         duplicate_key
      )
      print(
         f"[EARBUD {len(products)}/{EARBUD_LIMIT}] "
         f"{name}"
      )
   return products



async def get_product_details(
   crawler,
   product_link,
   product_config
):
   if not product_link:
      return {"description": "", "product_image": "", "price": None, "original_price": None, "discount": None}
   try:
      result = await crawler.arun(url=product_link, config=product_config)
      if not result.success:
         return {"description": "", "product_image": "", "price": None, "original_price": None, "discount": None}
      html = result.html or ""
      if not html:
         return {"description": "", "product_image": "", "price": None, "original_price": None, "discount": None}
      soup = BeautifulSoup(html, "html.parser")
      description_element = soup.select_one("h2.pdp-title-extra-info small")
      description = description_element.get_text(" ", strip=True) if description_element else ""
      price_element = soup.select_one("span.price.price--highlight.price--large[data-variant-price]")
      if not price_element:
         price_element = soup.select_one("span.price.price--highlight.price--large")
      product_price = price_to_int(price_element.get_text(" ", strip=True) if price_element else None)
      original_price_element = soup.select_one(".strike-price span.price.price--compare.line-through")
      product_original_price = price_to_int(original_price_element.get_text(" ", strip=True) if original_price_element else None)
      discount_element = soup.select_one(".strike-price p.custom-saved-price")
      product_discount = discount_to_int(discount_element.get_text(" ", strip=True) if discount_element else None)
      product_image = ""
      gallery_selector = (
         "div.product__media-list "
         "div.product__media-item[data-media-type='image'] "
         "div.product__media-image-wrapper img"
      )
      media_images = soup.select(gallery_selector)
      valid_images = []
      product_name_words = [
         word.lower()
         for word in re.findall(r"[a-zA-Z0-9]+", product_link.split("/products/")[-1])
         if len(word) > 2
      ]
      for image in media_images:
         image_url = image.get("src") or image.get("data-src") or image.get("data-original") or image.get("data-lazy-src") or ""
         if not image_url:
            srcset = image.get("srcset", "")
            if srcset:
               image_url = srcset.split(",")[0].strip().split(" ")[0]
         if not image_url:
            continue
         lower_url = image_url.lower()
         alt_text = (image.get("alt") or "").lower()
         if any(x in lower_url for x in ["mask_group", "star-", "star_", "rating", "review-star", "reviews-star", "badge", "swatch"]):
            continue
         if "verified reviews" in alt_text or alt_text == "star":
            continue
         normalized_url = normalize_image_url(image_url, product_link)
         if normalized_url:
            valid_images.append((normalized_url, alt_text))
      if valid_images:
         matching_images = [image_url for image_url, alt_text in valid_images if product_name_words and any(word in alt_text for word in product_name_words)]
         product_image = matching_images[0] if matching_images else valid_images[0][0]
      if not product_image:
         print("[PRODUCT IMAGE NOT FOUND]", product_link)
      print(f"[PRODUCT PAGE PRICE] {product_price} | Original: {product_original_price} | Discount: {product_discount}")
      return {"description": clean_text(description), "product_image": product_image, "price": product_price, "original_price": product_original_price, "discount": product_discount}
   except Exception as e:
      print("[PRODUCT PAGE ERROR]", product_link, e)
      return {"description": "", "product_image": "", "price": None, "original_price": None, "discount": None}
async def process_products(
   crawler,
   data,
   category,
   limit,
   product_config,
   global_seen,
   base_url
):
   products = []
   if not data:
      return products
   if not isinstance(
      data,
      list
   ):
      return products
   for item in data:
      if len(products) >= limit:
         break
      if not isinstance(
         item,
         dict
      ):
         continue


   
      product_link = normalize_product_link(
         item.get(
            "product_link"
         ),
         base_url
      )
      if not product_link:
         continue
      duplicate_key = (
         product_link.lower()
      )
      if duplicate_key in global_seen:
         continue


      name = clean_text(
         item.get(
            "name"
         )
      )
      if not name:
         parsed = urlparse(
            product_link
         )
         slug = (
            parsed.path
            .rstrip("/")
            .split("/")[-1]
         )
         name = clean_text(
            slug.replace(
               "-",
               " "
            )
         )



      price = price_to_int(
         item.get(
            "price"
         )
      )
      original_price = price_to_int(
         item.get(
            "original_price"
         )
      )
      discount = discount_to_int(
         item.get(
            "discount"
         )
      )
      ratings = clean_rating(
         item.get(
            "ratings"
         )
      )


   
      details = await get_product_details(
         crawler,
         product_link,
         product_config
      )
      description = details.get("description", "")
      product_image = details.get("product_image", "")
      product_page_price = details.get("price")
      product_page_original_price = details.get("original_price")
      product_page_discount = details.get("discount")
      if product_page_price is not None:
         price = product_page_price
      if product_page_original_price is not None:
         original_price = product_page_original_price
      if product_page_discount is not None:
         discount = product_page_discount
      # Skip products with 0% discount or no discount
      if discount is None or discount <= 0:
         print(f"[SKIPPED] 0% DISCOUNT: {name}")
         continue
      if original_price is not None and price is not None and original_price < price:
         print(f"[INVALID ORIGINAL PRICE] {name} | Price: {price} | Original: {original_price} | Using listing original price")
         original_price = price_to_int(item.get("original_price"))


 
      check_text = (
         name
         + " "
         + description
      ).lower()
      if "restocking soon" in check_text:
         print(
            "[SKIPPED] Restocking Soon:",
            name
         )
         continue


   
      if not description:
         description = name
         if price is not None:
            description = (
               f"{name} {price}"
            )


   
      if not product_image:
         print(
            "[WARNING] Product image not found:",
            product_link
         )


   
      product = {
         "name": name,
         "price": int(price) if price is not None else None,
         "currency": CURRENCY,
         "original_price": int(original_price) if original_price is not None else None,
         "discount": int(discount) if discount is not None else None,
         "ratings": ratings,
         "description": description,
         "image_link": product_image,
         "product_link": product_link,
         "organization_id": ORGANIZATION_ID,
         "store_id": STORE_ID,
         "categories_id": category,
         "created_at": datetime.now().isoformat()
      }
      products.append(
         product
      )
      global_seen.add(
         duplicate_key
      )
      print(
         f"[PRODUCT "
         f"{len(products)}/{limit}] "
         f"{name} | "
         f"Price: {price} | "
         f"Original: {original_price} | "
         f"Discount: {discount} | "
         f"Image: "
         f"{'YES' if product_image else 'NO'}"
      )



def save_json(products):
   with open(
      OUTPUT_JSON,
      "w",
      encoding="utf-8"
   ) as f:
      json.dump(
         products,
         f,
         indent=4,
         ensure_ascii=False
      )
   print(
      f"JSON generated: "
      f"{OUTPUT_JSON}"
   )



def save_csv(products):
   fieldnames = [
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
      "created_at"
   ]
   with open(
      OUTPUT_CSV,
      "w",
      newline="",
      encoding="utf-8-sig"
   ) as f:
      writer = csv.DictWriter(
         f,
         fieldnames=fieldnames
      )
      writer.writeheader()
      writer.writerows(
         products
      )
   print(
      f"CSV generated: "
      f"{OUTPUT_CSV}"
   )



async def scrape_collection_pages(
   crawler,
   base_url,
   category,
   limit,
   extractor,
   product_config,
   global_seen
):
   products = []
   page = 1
   listing_config = CrawlerRunConfig(
      cache_mode=CacheMode.BYPASS,
      wait_for="css:product-item",
      page_timeout=120000
   )
   while len(products) < limit:
      page_url = base_url if page == 1 else f"{base_url}?page={page}"
      print(f"[COLLECTION PAGE] {page}")
      print(f"[URL] {page_url}")
      result = await crawler.arun(
         url=page_url,
         config=listing_config
      )
      if not result.success:
         print(f"[PAGE ERROR] {page_url}")
         print(result.error_message)
         break
      html = result.html or ""
      print(f"[PAGE HTML LENGTH] {len(html)}")
      page_data = extractor(html, base_url)
      print(f"[PAGE PRODUCTS] {len(page_data)}")
      if not page_data:
         print("[NO PRODUCTS ON PAGE] Stopping")
         break
      before_valid = len(products)
      page_candidates = []
      for item in page_data:
         if not isinstance(item, dict):
            continue
         name = clean_text(item.get("name"))
         description = clean_text(item.get("description"))
         listing_discount = discount_to_int(item.get("discount"))
         check_text = (name + " " + description).lower()
         if "restocking soon" in check_text:
            print(f"[SKIPPED LISTING] Restocking Soon: {name}")
            continue
         if listing_discount is not None and listing_discount <= 0:
            print(f"[SKIPPED LISTING] 0% DISCOUNT: {name}")
            continue
         page_candidates.append(item)
      if not page_candidates:
         print("[NO VALID LISTING PRODUCTS] Moving to next page")
      remaining = limit - len(products)
      valid_products = await process_products(
         crawler,
         page_candidates,
         category,
         remaining,
         product_config,
         global_seen,
         base_url
      )
      products.extend(valid_products)
      print(f"[VALID PRODUCTS TOTAL] {len(products)}/{limit}")
      if len(products) >= limit:
         break
      if len(products) == before_valid and page > 1 and not page_candidates:
         print("[NO NEW VALID PRODUCTS] Moving to next page")
      page += 1
      if page > 20:
         print("[PAGE LIMIT REACHED]")
         break
   return products
async def main():
   browser_config = BrowserConfig(
      headless=True
   )
   global_seen = set()
   all_products = []
   async with AsyncWebCrawler(
      config=browser_config
   ) as crawler:
      product_config = CrawlerRunConfig(
         cache_mode=CacheMode.BYPASS,
         wait_for="css:body",
         page_timeout=120000
      )
      print("\n========================================")
      print("SCRAPING SMART WATCHES")
      print("========================================")
      watch_products = await scrape_collection_pages(
         crawler,
         WATCH_URL,
         "Others",
         WATCH_LIMIT,
         extract_watch_products,
         product_config,
         global_seen
      )
      all_products.extend(watch_products)
      print(f"\nWATCHES TOTAL: {len(watch_products)}/{WATCH_LIMIT}")
      print("\n========================================")
      print("SCRAPING TRUE WIRELESS EARBUDS")
      print("========================================")
      earbud_products = await scrape_collection_pages(
         crawler,
         EARBUD_URL,
         "Others",
         EARBUD_LIMIT,
         extract_earbud_products,
         product_config,
         global_seen
      )
      all_products.extend(earbud_products)
      print(f"\nEARBUDS TOTAL: {len(earbud_products)}/{EARBUD_LIMIT}")
   save_json(all_products)
   save_csv(all_products)
   print("\n========================================")
   print("SCRAPING COMPLETED")
   print("========================================")
   print(f"Total products: {len(all_products)}")
   print(f"JSON: {OUTPUT_JSON}")
   print(f"CSV : {OUTPUT_CSV}")
   return all_products
def run_boat_scraper():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout, force=True)
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    logging.info("=" * 60)
    logging.info("boAt browser process started.")
    logging.info("=" * 60)
    try:
        products = asyncio.run(main())
        logging.info("boAt browser process finished. Products returned: %s", len(products))
        return products
    except Exception:
        logging.exception("boAt browser process failed.")
        raise
class BoatSpider(scrapy.Spider):
    name = "boat"
    allowed_domains = ["boat-lifestyle.com", "www.boat-lifestyle.com"]
    async def start(self):
        self.logger.info("=" * 60)
        self.logger.info("Starting boAt Crawl4AI scraper...")
        self.logger.info("=" * 60)
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=1) as executor:
            products = await loop.run_in_executor(executor, run_boat_scraper)
        with open("scrape_boat.json", "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        self.logger.info("boAt scraper returned %s products.", len(products))
        for product in products:
            if not product.get("image_link") or not product.get("product_link"):
                continue
            yield product
        self.logger.info("boAt spider completed.")
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout, force=True)
    data = run_boat_scraper()
    with open("scrape_boat.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("Saved in scrape_boat.json")
    print("Total products:", len(data))


