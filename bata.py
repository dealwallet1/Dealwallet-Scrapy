import asyncio
import os
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from dotenv import load_dotenv
from supabase import create_client




load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SUPABASE_SCHEMA = os.getenv(
    "SUPABASE_SCHEMA",
    "public"
)

PRODUCTS_TABLE = "products"

PRICE_HISTORY_TABLE = "price_history_2"




STORE_ID = "72d83dba-0425-41bb-9be9-898d1d38bdcc"

SOURCE = "bata"




if not SUPABASE_URL:
    raise ValueError(
        "SUPABASE_URL is missing in .env"
    )

if not SUPABASE_KEY:
    raise ValueError(
        "SUPABASE_KEY is missing in .env"
    )


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)




def clean_price(value):

    if value is None:
        return None

    if str(value).strip() == "--":
        return None

    text = str(value)

    text = (
        text
        .replace(",", "")
        .replace("₹", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .strip()
    )

    match = re.search(
        r"\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    number = float(
        match.group()
    )

    if number.is_integer():
        return int(number)

    return number




def clean_number(value):

    if value is None:
        return None

    value = float(value)

    if value.is_integer():
        return int(value)

    return value




def get_products():

    response = (
        supabase
        .schema(SUPABASE_SCHEMA)
        .from_(PRODUCTS_TABLE)
        .select(
            "id,name,product_link,price"
        )
        .eq(
            "store_id",
            STORE_ID
        )
        .not_.is_(
            "product_link",
            "null"
        )
        .neq(
            "product_link",
            ""
        )
        .execute()
    )

    return response.data or []




async def scrape_product_price(
    crawler,
    product
):

    product_link = product.get(
        "product_link"
    )

    if not product_link:
        return None

    try:

        result = await crawler.arun(
            url=product_link
        )

        if not result.success:

            print(
                "PAGE FAILED:",
                product_link
            )

            return None


        soup = BeautifulSoup(
            result.html,
            "html.parser"
        )


    

        selectors = [

            "span.cc-price",

            "[data-target='salePrice']",

            ".price-sales",

            ".sales .value",

            ".product-price .price"

        ]


        price_tag = None


        for selector in selectors:

            price_tag = soup.select_one(
                selector
            )

            if price_tag:
                break


        if not price_tag:

            print(
                "PRICE NOT FOUND:",
                product_link
            )

            return None


      

        content_price = price_tag.get(
            "content"
        )


        if content_price:

            scraped_price = clean_price(
                content_price
            )

            if scraped_price is not None:

                return scraped_price


      

        scraped_price = clean_price(
            price_tag.get_text(
                " ",
                strip=True
            )
        )


        if scraped_price is None:

            print(
                "INVALID PRICE:",
                product_link
            )

            return None


        return scraped_price


    except Exception as e:

        print(
            "SCRAPE ERROR:",
            product_link
        )

        print(
            str(e)
        )

        return None




def get_history_row(product_id):

    response = (
        supabase
        .schema(SUPABASE_SCHEMA)
        .from_(PRICE_HISTORY_TABLE)
        .select("*")
        .eq(
            "product_id",
            product_id
        )
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if rows:
        return rows[0]

    return None




def create_blank_data():

    return {

        "low_price": "--",

        "mid_price": "--",

        "high_price": "--"

    }




def has_real_prices(day_data):

    if not day_data:
        return False

    low = clean_price(
        day_data.get(
            "low_price"
        )
    )

    mid = clean_price(
        day_data.get(
            "mid_price"
        )
    )

    high = clean_price(
        day_data.get(
            "high_price"
        )
    )

    return (

        low is not None

        and

        mid is not None

        and

        high is not None

    )




def calculate_prices(
    today_data,
    new_price
):

    new_price = float(
        new_price
    )


   

    if not has_real_prices(
        today_data
    ):

        value = clean_number(
            new_price
        )

        return {

            "low_price":
                value,

            "mid_price":
                value,

            "high_price":
                value

        }


    low = float(
        clean_price(
            today_data[
                "low_price"
            ]
        )
    )

    mid = float(
        clean_price(
            today_data[
                "mid_price"
            ]
        )
    )

    high = float(
        clean_price(
            today_data[
                "high_price"
            ]
        )
    )


   

    if low == mid == high:

        first_price = mid

        return {

            "low_price":
                clean_number(
                    min(
                        first_price,
                        new_price
                    )
                ),

            "mid_price":
                clean_number(
                    first_price
                ),

            "high_price":
                clean_number(
                    max(
                        first_price,
                        new_price
                    )
                )

        }




    first_price = mid


    if first_price == low:

        second_price = high

    else:

        second_price = low


    values = sorted([

        first_price,

        second_price,

        new_price

    ])


    return {

        "low_price":
            clean_number(
                values[0]
            ),

        "mid_price":
            clean_number(
                values[1]
            ),

        "high_price":
            clean_number(
                values[2]
            )

    }




def calculate_monthly_high(
    price_data,
    current_date
):

    values = []


    for date_key, day_data in price_data.items():

        try:

            row_date = datetime.strptime(
                date_key,
                "%Y-%m-%d"
            ).date()


            if (

                row_date.year
                == current_date.year

                and

                row_date.month
                == current_date.month

            ):

                high = clean_price(
                    day_data.get(
                        "high_price"
                    )
                )


                if high is not None:

                    values.append(
                        high
                    )


        except Exception:

            continue


    if values:

        return clean_number(
            max(values)
        )


    return None




def calculate_yearly_high(
    price_data,
    current_date
):

    values = []


    for date_key, day_data in price_data.items():

        try:

            row_date = datetime.strptime(
                date_key,
                "%Y-%m-%d"
            ).date()


            if row_date.year == current_date.year:

                high = clean_price(
                    day_data.get(
                        "high_price"
                    )
                )


                if high is not None:

                    values.append(
                        high
                    )


        except Exception:

            continue


    if values:

        return clean_number(
            max(values)
        )


    return None




def create_history_row(
    product,
    price_data,
    today,
    current_prices
):

    now = datetime.now(
        timezone.utc
    ).isoformat()


    data = {

        "name":
            product.get(
                "name"
            ),

        "product_id":
            product[
                "id"
            ],

        "source":
            SOURCE,


   

        "low_price":
            current_prices.get(
                "low_price"
            ),

        "mid_price":
            current_prices.get(
                "mid_price"
            ),

        "high_price":
            current_prices.get(
                "high_price"
            ),


        "monthly_high_price":
            calculate_monthly_high(
                price_data,
                today
            ),

        "yearly_high_price":
            calculate_yearly_high(
                price_data,
                today
            ),


        "price_data":
            price_data,


        "created_at":
            now,

        "updated_at":
            now

    }


    (
        supabase
        .schema(SUPABASE_SCHEMA)
        .from_(PRICE_HISTORY_TABLE)
        .insert(
            data
        )
        .execute()
    )




def update_history_row(
    history_id,
    price_data,
    today,
    current_prices
):

    now = datetime.now(
        timezone.utc
    ).isoformat()


    update_data = {

        "price_data":
            price_data,


       

        "low_price":
            current_prices.get(
                "low_price"
            ),

        "mid_price":
            current_prices.get(
                "mid_price"
            ),

        "high_price":
            current_prices.get(
                "high_price"
            ),


        "monthly_high_price":
            calculate_monthly_high(
                price_data,
                today
            ),

        "yearly_high_price":
            calculate_yearly_high(
                price_data,
                today
            ),


        "updated_at":
            now

    }


    (
        supabase
        .schema(SUPABASE_SCHEMA)
        .from_(PRICE_HISTORY_TABLE)
        .update(
            update_data
        )
        .eq(
            "id",
            history_id
        )
        .execute()
    )




def save_price_history(
    product,
    scraped_price
):

    product_id = product[
        "id"
    ]


    product_price = clean_price(
        product.get(
            "price"
        )
    )


    today = datetime.now(
        timezone.utc
    ).date()


    today_key = today.isoformat()


   

    history_row = get_history_row(
        product_id
    )


   

    if product_price == scraped_price:


        blank_data = (
            create_blank_data()
        )


      

        if not history_row:


            price_data = {

                today_key:
                    blank_data

            }


            create_history_row(

                product,

                price_data,

                today,

                blank_data

            )


            print(
                "NEW ROW - PRICE SAME"
            )

            print(
                "LOW  = --"
            )

            print(
                "MID  = --"
            )

            print(
                "HIGH = --"
            )

            return



        price_data = (

            history_row.get(
                "price_data"
            )

            or

            {}

        )




        price_data[
            today_key
        ] = blank_data


        update_history_row(

            history_row[
                "id"
            ],

            price_data,

            today,

            blank_data

        )


        print(
            "PRICE SAME - -- INSERTED"
        )

        return


    

    if not history_row:


        current_prices = (

            calculate_prices(

                None,

                scraped_price

            )

        )


        price_data = {

            today_key:
                current_prices

        }


        create_history_row(

            product,

            price_data,

            today,

            current_prices

        )


        print(
            "NEW ROW - PRICE CHANGED"
        )

        return



    price_data = (

        history_row.get(
            "price_data"
        )

        or

        {}

    )


    existing_day = price_data.get(
        today_key
    )




    current_prices = (

        calculate_prices(

            existing_day,

            scraped_price

        )

    )


    price_data[
        today_key
    ] = current_prices


    

    update_history_row(

        history_row[
            "id"
        ],

        price_data,

        today,

        current_prices

    )


    print(
        "PRICE CHANGED - HISTORY UPDATED"
    )

    print(
        "LOW:",
        current_prices[
            "low_price"
        ]
    )

    print(
        "MID:",
        current_prices[
            "mid_price"
        ]
    )

    print(
        "HIGH:",
        current_prices[
            "high_price"
        ]
    )




async def main():

    print()

    print(
        "=" * 70
    )

    print(
        "BATA PRICE HISTORY SCRAPER"
    )

    print(
        "PRODUCTS TABLE IS READ ONLY"
    )

    print(
        "SAME PRICE = --"
    )

    print(
        "NO PRICES ARRAY IN PRICE_DATA"
    )

    print(
        "=" * 70
    )


    products = get_products()


    print(
        "TOTAL PRODUCTS:",
        len(products)
    )


    if not products:

        print(
            "NO PRODUCTS FOUND"
        )

        print(
            "CHECK BATA STORE_ID"
        )

        return


    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:


        for index, product in enumerate(

            products,

            start=1

        ):


            print()

            print(
                "-" * 70
            )

            print(
                f"PRODUCT {index}/{len(products)}"
            )

            print(
                "PRODUCT:",
                product.get(
                    "name"
                )
            )

            print(
                "PRODUCT ID:",
                product.get(
                    "id"
                )
            )

            print(
                "URL:",
                product.get(
                    "product_link"
                )
            )


      

            scraped_price = (

                await scrape_product_price(

                    crawler,

                    product

                )

            )


            if scraped_price is None:

                continue


            print(
                "PRODUCT TABLE PRICE:",
                clean_price(
                    product.get(
                        "price"
                    )
                )
            )


            print(
                "SCRAPED PRICE:",
                scraped_price
            )


     

            save_price_history(

                product,

                scraped_price

            )


    print()

    print(
        "=" * 70
    )

    print(
        "PROCESS COMPLETED"
    )

    print(
        "PRODUCTS TABLE WAS NOT MODIFIED"
    )

    print(
        "=" * 70
    )




if __name__ == "__main__":

    asyncio.run(
        main()
    )