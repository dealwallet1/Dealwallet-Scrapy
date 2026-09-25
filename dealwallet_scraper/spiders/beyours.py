import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor
from urllib .parse import urljoin ,urlparse 

import scrapy
from bs4 import BeautifulSoup

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)






COLLECTIONS =[






{
"url":(
"https://www.beyours.in/collections/new-arrivals"
"?sort_by=created-descending"
"&filter.p.m.custom.product_category=Shirts"
),
"category":"Shirts",
},






{
"url":(
"https://www.beyours.in/collections/new-arrivals"
"?sort_by=created-descending"
"&filter.p.m.custom.product_category=Trousers"
),
"category":"Fashion & Lifestyle",
},

{
"url":(
"https://www.beyours.in/collections/new-arrivals"
"?sort_by=created-descending"
"&filter.p.m.custom.product_category=Cargos"
),
"category":"Fashion & Lifestyle",
},






{
"url":(
"https://www.beyours.in/collections/new-arrivals"
"?sort_by=created-descending"
"&filter.p.m.custom.product_category=Polos"
),
"category":"Shirts",
},
]









CATEGORY_LIMITS ={

"Fashion & Lifestyle":50 ,

"Shirts":50 ,
}






OUTPUT_JSON ="beyours_products.json"

ORGANIZATION_ID ="Dealwallet"

STORE_ID ="Beyours"

CURRENCY ="₹"

PAGE_TIMEOUT =180000 

PRODUCT_DELAY =1.0 






def clean_text (value ):

    if not value :
        return None 

    value =BeautifulSoup (
    str (value ),
    "html.parser"
    ).get_text (
    " ",
    strip =True 
    )

    value =re .sub (
    r"\s+",
    " ",
    value 
    ).strip ()

    return value if value else None 






def clean_description(value):
    if not value:
        return None
    value = str(value)
    value = re.sub(r'[/\\"]', '', value)
    return value.strip() or None


def to_int (value ):

    if value is None :
        return None 

    try :

        if isinstance (value ,int ):
            return value 

        if isinstance (value ,float ):
            return int (value )

        text =str (value )

        text =re .sub (
        r"[₹$€£,\s]",
        "",
        text 
        )

        match =re .search (
        r"\d+(?:\.\d+)?",
        text 
        )

        if not match :
            return None 

        return int (
        float (
        match .group (0 )
        )
        )

    except Exception :

        return None 






def mandatory_fields_present(product):
    if not product:
        return False
    required_fields = [
        "name",
        "price",
        "original_price",
        "description",
        "image_link",
        "product_link",
    ]
    for field in required_fields:
        value = product.get(field)
        if value is None or value == "":
            return False
    return True


def clean_rating (value ):

    if value is None :
        return None 

    try :

        rating =float (value )


        if rating ==0.0 :
            return None 

        return rating 

    except Exception :

        return None 






def get_handle (product_url ):

    try :

        path =urlparse (
        product_url 
        ).path .strip ("/")

        parts =path .split ("/")

        if "products"not in parts :
            return None 

        index =parts .index (
        "products"
        )

        if index +1 >=len (parts ):
            return None 

        return parts [index +1 ]

    except Exception :

        return None 






def normalize_product_url (href ):

    if not href :
        return None 

    href =href .strip ()

    if href .startswith ("//"):

        href ="https:"+href 

    elif href .startswith ("/"):

        href =urljoin (
        "https://www.beyours.in",
        href 
        )

    if not href .startswith ("http"):
        return None 

    href =href .split ("?")[0 ]

    if "/products/"not in href :
        return None 

    return href .rstrip ("/")






def extract_product_links (
html ,
max_links =None 
):

    soup =BeautifulSoup (
    html ,
    "html.parser"
    )

    links =[]

    seen =set ()

    for a in soup .select (
    'a[href*="/products/"]'
    ):

        href =a .get (
        "href"
        )

        product_url =normalize_product_url (
        href 
        )

        if not product_url :
            continue 

        if product_url in seen :
            continue 

        seen .add (
        product_url 
        )

        links .append (
        product_url 
        )

        if (
        max_links is not None 
        and len (links )>=max_links 
        ):
            break 

    return links 






def extract_listing_name (soup ,product_url ):
    for element in soup .select (
    "a.product-item-meta__title"
    ):
        href =normalize_product_url (
        element .get ("href")
        )

        if href ==product_url :
            name =clean_text (
            element .get_text (
            " ",
            strip =True 
            )
            )

            if name :
                return name 

    return None 








async def get_shopify_product_json (
crawler ,
product_url 
):

    handle =get_handle (
    product_url 
    )

    if not handle :
        return None 

    json_url =(
    "https://www.beyours.in"
    f"/products/{handle }.js"
    )

    try :

        result =await crawler .arun (

        url =json_url ,

        config =CrawlerRunConfig (

        cache_mode =CacheMode .BYPASS ,

        page_timeout =60000 ,

        wait_until ="commit",

        word_count_threshold =0 ,

        exclude_external_links =True ,

        remove_overlay_elements =True ,
        )
        )

        if not result .success :
            return None 

        raw =result .html 

        if not raw :
            return None 

        raw =raw .strip ()

        try :

            return json .loads (
            raw 
            )

        except Exception :

            soup =BeautifulSoup (
            raw ,
            "html.parser"
            )

            text =soup .get_text (
            " ",
            strip =True 
            )

            return json .loads (
            text 
            )

    except Exception as e :

        print (
        f"[PRICE JSON ERROR] "
        f"{product_url } -> {e }"
        )

        return None 






def extract_price_data (
product_json 
):

    if not product_json :

        return (
        None ,
        None ,
        None 
        )

    variants =product_json .get (
    "variants",
    []
    )

    if not variants :

        return (
        None ,
        None ,
        None 
        )





    selected_variant =None 

    for variant in variants :

        if variant .get (
        "available"
        ):

            selected_variant =variant 

            break 

    if selected_variant is None :

        selected_variant =variants [0 ]

    price_raw =selected_variant .get (
    "price"
    )

    original_raw =selected_variant .get (
    "compare_at_price"
    )

    price =None 

    original_price =None 


    if price_raw is not None :

        try :

            price =int (
            float (price_raw )/100 
            )

        except Exception :

            price =to_int (
            price_raw 
            )

    if original_raw is not None :

        try :

            original_price =int (
            float (original_raw )/100 
            )

        except Exception :

            original_price =to_int (
            original_raw 
            )





    discount =None 

    if (
    price is not None 
    and original_price is not None 
    and original_price >price 
    ):

        discount =int (
        round (
        (
        (
        original_price 
        -price 
        )
        /original_price 
        )
        *100 
        )
        )

    return (
    price ,
    original_price ,
    discount 
    )






def extract_name (
soup ,
product_json ,
listing_name =None 
):

    if listing_name :
        listing_name =clean_text (listing_name )
        if listing_name :
            return listing_name 


    selectors =[

    "h1.product__title",

    "h1.product-title",

    "h1.product__title-desk",

    "div.product__title h1",

    "div.product__title h2",

    "h1",
    ]

    for selector in selectors :

        element =soup .select_one (
        selector 
        )

        if element :

            name =clean_text (
            element .get_text (
            " ",
            strip =True 
            )
            )

            if name :
                return name 

    if product_json :

        title =product_json .get (
        "title"
        )

        if title :

            return clean_text (
            title 
            )

    return None 







def extract_description (
soup ,
product_json 
):

    selectors =[

    "div.product__description",

    ".product__description",

    "[class*='product__description']",

    ".rte",

    ".product-single__description",

    ".product-description",

    "[data-product-description]",
    ]

    for selector in selectors :

        element =soup .select_one (
        selector 
        )

        if element :

            text =clean_text (
            element 
            )

            if text :
                return clean_description (text )

    if product_json :

        body_html =product_json .get (
        "description"
        )

        if body_html :

            text =clean_text (
            body_html 
            )

            if text :
                return clean_description (text )

    return None 






def extract_image (
soup ,
product_json 
):

    selectors =[

    "div.product__media img",

    ".product__media img",

    ".product-single__media img",

    ".product__media-list img",

    "main img",
    ]

    for selector in selectors :

        img =soup .select_one (
        selector 
        )

        if not img :
            continue 

        src =(

        img .get ("src")

        or img .get ("data-src")

        or img .get ("data-original")
        )

        if src :

            src =src .strip ()

            if src .startswith ("//"):

                src ="https:"+src 

            elif src .startswith ("/"):

                src =urljoin (
                "https://www.beyours.in",
                src 
                )

            return src 

    if product_json :

        image =product_json .get (
        "featured_image"
        )

        if image :

            if image .startswith ("//"):

                image ="https:"+image 

            elif image .startswith ("/"):

                image =urljoin (
                "https://www.beyours.in",
                image 
                )

            return image 

        images =product_json .get (
        "images",
        []
        )

        if images :

            image =images [0 ]

            if image .startswith ("//"):

                image ="https:"+image 

            return image 

    return None 






def extract_rating (
soup 
):





    visible_rating =soup .select_one (
    ".bb-rev-display .bb-rev-rating"
    )

    if visible_rating :

        value =visible_rating .get_text (
        " ",
        strip =True 
        )

        rating =clean_rating (value )

        if rating is not None :
            return rating 

    selectors =[

    ".jdgm-prev-badge",

    "[data-average-rating]",

    ".rating",

    "[class*='rating']",
    ]

    for selector in selectors :

        element =soup .select_one (
        selector 
        )

        if not element :
            continue 





        rating =element .get (
        "data-average-rating"
        )

        if rating is not None :

            return clean_rating (
            rating 
            )





        rating =element .get (
        "data-score"
        )

        if rating is not None :

            return clean_rating (
            rating 
            )





        aria =element .get (
        "aria-label"
        )

        if aria :

            match =re .search (
            r"(\d+(?:\.\d+)?)",
            aria 
            )

            if match :

                return clean_rating (
                match .group (1 )
                )





        text =element .get_text (
        " ",
        strip =True 
        )

        match =re .search (
        r"(?<!\d)([0-5](?:\.\d+)?)(?!\d)",
        text 
        )

        if match :

            return clean_rating (
            match .group (1 )
            )

    return None 






async def scrape_product (
crawler ,
product_url ,
category ,
index ,
listing_name =None 
):

    print ()
    print (
    f"[PRODUCT {index }] "
    f"{product_url }"
    )

    try :





        result =await crawler .arun (

        url =product_url ,

        config =CrawlerRunConfig (

        cache_mode =CacheMode .BYPASS ,

        page_timeout =PAGE_TIMEOUT ,


        wait_until ="commit",

        word_count_threshold =0 ,

        exclude_external_links =True ,

        remove_overlay_elements =True ,
        )
        )

        if not result .success :

            print (
            "[PRODUCT FAILED]"
            )

            return None 

        html =result .html 

        if not html :

            return None 

        soup =BeautifulSoup (
        html ,
        "html.parser"
        )





        product_json =(
        await get_shopify_product_json (
        crawler ,
        product_url 
        )
        )





        (
        price ,
        original_price ,
        discount 
        )=extract_price_data (
        product_json 
        )





        if price is None :

            print (
            "[SKIP] Price missing"
            )

            return None 

        if original_price is None :

            print (
            "[SKIP] Original price missing"
            )

            return None 

        if discount is None :

            print (
            "[SKIP] Discount missing"
            )

            return None 





        image_link =extract_image (
        soup ,
        product_json 
        )

        if not image_link :

            print (
            "[SKIP] Image missing"
            )

            return None 





        if not product_url :

            print (
            "[SKIP] Product link missing"
            )

            return None 





        name =extract_name (
        soup ,
        product_json ,
        listing_name 
        )

        if not name :

            print (
            "[SKIP] Name missing"
            )

            return None 






        description =extract_description (
        soup ,
        product_json 
        )


        description =clean_description (description )





        ratings =extract_rating (
        soup 
        )

        if ratings ==0.0 :

            ratings =None 





        product ={

        "name":name ,

        "price":int (price ),

        "currency":CURRENCY ,

        "original_price":int (
        original_price 
        ),

        "discount":int (
        discount 
        ),

        "ratings":ratings ,

        "description":description ,

        "image_link":image_link ,

        "product_link":product_url ,

        "organization_id":ORGANIZATION_ID ,

        "store_id":STORE_ID ,

        "categories_id":category ,
        "created_at":datetime.now().isoformat(),
        }

        print (
        f"[VALID] {name }"
        )

        print (
        f"        Price={price }"
        )

        print (
        f"        Original={original_price }"
        )

        print (
        f"        Discount={discount }%"
        )

        print (
        f"        Rating={ratings }"
        )

        print (
        f"        Category={category }"
        )

        return product 

    except Exception as e :

        print (
        f"[PRODUCT ERROR] "
        f"{product_url } -> {e }"
        )

        return None 






async def scrape_beyours_async():

    print ("="*70 )

    print (
    "BEYOURS MULTI-CATEGORY "
    "CRAWL4AI SCRAPER"
    )

    print ("="*70 )

    print (
    "Fashion & Lifestyle TOTAL: 50"
    )

    print (
    "Shirts TOTAL: 50"
    )

    print (
    "Overall TOTAL: 100"
    )

    print (
    "Price source: Shopify product.js"
    )

    print ("="*70 )





    browser_config =BrowserConfig (

    headless =True ,

    verbose =False ,

    extra_args =[

    "--disable-blink-features=AutomationControlled",

    "--disable-dev-shm-usage",

    "--no-sandbox",

    "--disable-gpu",

    "--disable-extensions",

    "--disable-background-networking",

    "--disable-background-timer-throttling",

    "--disable-renderer-backgrounding",

    "--disable-features=Translate,BackForwardCache",
    ],
    )





    all_products =[]













    category_counts ={

    "Fashion & Lifestyle":0 ,

    "Shirts":0 ,
    }





    seen_product_urls =set ()





    async with AsyncWebCrawler (
    config =browser_config 
    )as crawler :





        for collection_index ,collection in enumerate (
        COLLECTIONS ,
        start =1 
        ):

            collection_url =collection [
            "url"
            ]

            category =collection [
            "category"
            ]





            category_limit =CATEGORY_LIMITS .get (
            category ,
            0 
            )

            current_category_count =category_counts .get (
            category ,
            0 
            )

            remaining_category_products =(
            category_limit 
            -current_category_count 
            )





            if remaining_category_products <=0 :

                print ()
                print (
                f"[CATEGORY COMPLETE] "
                f"{category }: "
                f"{current_category_count }/"
                f"{category_limit }"
                )

                print (
                f"[SKIP URL] "
                f"{collection_url }"
                )

                continue 





            print ()
            print ("="*70 )

            print (
            f"[URL {collection_index }/"
            f"{len (COLLECTIONS )}]"
            )

            print (
            f"[COLLECTION URL] "
            f"{collection_url }"
            )

            print (
            f"[CATEGORY] "
            f"{category }"
            )

            print (
            f"[CATEGORY COUNT] "
            f"{current_category_count }/"
            f"{category_limit }"
            )

            print (
            f"[CATEGORY REMAINING] "
            f"{remaining_category_products }"
            )

            print ("="*70 )





            collection_config =CrawlerRunConfig (

            cache_mode =CacheMode .BYPASS ,

            page_timeout =300000 ,

            wait_until ="domcontentloaded",

            delay_before_return_html =8.0 ,

            remove_overlay_elements =True ,

            scan_full_page =False ,

            scroll_delay =0.5 ,
            )

            try :

                print (
                "[COLLECTION] Loading..."
                )

                result =await crawler .arun (

                url =collection_url ,

                config =collection_config ,
                )

            except Exception as e :

                print (
                "[COLLECTION ERROR]"
                )

                print (
                str (e )
                )

                continue 





            if not result .success :

                print (
                "[COLLECTION FAILED]"
                )

                print (
                getattr (
                result ,
                "error_message",
                None 
                )
                )

                continue 

            html =result .html 

            if not html :

                print (
                "[COLLECTION EMPTY]"
                )

                continue 

            print (
            f"[COLLECTION] "
            f"HTML loaded: "
            f"{len (html ):,} characters"
            )








            collection_soup =BeautifulSoup (
            html ,
            "html.parser"
            )

            product_links =extract_product_links (

            html ,

            max_links =remaining_category_products 
            )

            print (
            f"[COLLECTION] "
            f"Product links found: "
            f"{len (product_links )}"
            )

            if not product_links :

                print (
                "[COLLECTION] "
                "No product links found."
                )

                continue 





            collection_valid_count =0 

            for product_index ,product_url in enumerate (
            product_links ,
            start =1 
            ):






                if (
                category_counts [category ]
                >=category_limit 
                ):

                    print ()
                    print (
                    f"[CATEGORY COMPLETE] "
                    f"{category }: "
                    f"{category_counts [category ]}/"
                    f"{category_limit }"
                    )

                    break 





                if product_url in seen_product_urls :

                    print (
                    f"[SKIP DUPLICATE] "
                    f"{product_url }"
                    )

                    continue 





                listing_name =extract_listing_name (
                collection_soup ,
                product_url 
                )

                product =await scrape_product (
                crawler ,
                product_url ,
                category ,
                product_index ,
                listing_name 
                )

                if product is None :

                    continue 





                seen_product_urls .add (
                product_url 
                )





                all_products .append (
                product 
                )





                category_counts [
                category 
                ]+=1 

                collection_valid_count +=1 





                print (
                f"[CATEGORY TOTAL] "
                f"{category }: "
                f"{category_counts [category ]}/"
                f"{category_limit }"
                )

                print (
                f"[GLOBAL TOTAL] "
                f"{len (all_products )}/100"
                )





                await asyncio .sleep (
                PRODUCT_DELAY 
                )





            print ()
            print (
            f"[URL COMPLETE] "
            f"Valid products from this URL: "
            f"{collection_valid_count }"
            )

            print (
            f"[CATEGORY TOTAL] "
            f"{category }: "
            f"{category_counts [category ]}/"
            f"{category_limit }"
            )

            print (
            f"[GLOBAL TOTAL] "
            f"{len (all_products )}/100"
            )
    return all_products[:100]


def run_beyours_scraper():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )

    logging.info("Beyours Crawl4AI worker started.")
    logging.info("Python: %s", sys.version)
    logging.info("Platform: %s", sys.platform)

    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(
                asyncio.WindowsProactorEventLoopPolicy()
            )
        except AttributeError:
            pass

    try:
        products = asyncio.run(scrape_beyours_async())
        logging.info("Beyours worker finished | Products=%s", len(products))
        return products
    except Exception:
        logging.exception("Beyours Crawl4AI worker failed.")
        raise


def save_json(products):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as file:
        json.dump(products, file, ensure_ascii=False, indent=4)

    logging.info("JSON generated: %s", OUTPUT_JSON)
    logging.info("Products written: %s", len(products))


class BeyoursSpider(scrapy.Spider):
    name = "beyours"
    allowed_domains = [
        "beyours.in",
        "www.beyours.in",
    ]

    async def start(self):
        self.logger.info("=" * 70)
        self.logger.info("Starting Beyours Crawl4AI scraper...")
        self.logger.info("=" * 70)

        loop = asyncio.get_running_loop()

        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                products = await loop.run_in_executor(
                    executor,
                    run_beyours_scraper,
                )
        except Exception as exc:
            self.logger.exception(
                "Beyours Crawl4AI worker failed: %s",
                exc,
            )
            return

        self.logger.info(
            "Crawl4AI returned %s products.",
            len(products),
        )

        valid_products = []
        skipped = 0

        for product in products:
            if not mandatory_fields_present(product):
                skipped += 1
                self.logger.warning(
                    "Skipping Beyours product because mandatory field is missing | name=%r",
                    product.get("name"),
                )
                continue

            valid_products.append(product)
            yield product

        save_json(valid_products)

        self.logger.info("=" * 70)
        self.logger.info("Beyours spider completed.")
        self.logger.info("Valid products: %s", len(valid_products))
        self.logger.info("Skipped products: %s", skipped)
        self.logger.info("Output: %s", OUTPUT_JSON)
        self.logger.info("=" * 70)


if __name__ == "__main__":
    data = run_beyours_scraper()
    valid_products = [
        product
        for product in data
        if mandatory_fields_present(product)
    ]
    save_json(valid_products)
    print("=" * 70)
    print("BEYOURS SCRAPING COMPLETED")
    print("Valid products:", len(valid_products))
    print("Skipped products:", len(data) - len(valid_products))
    print("Output:", OUTPUT_JSON)
    print("=" * 70)
