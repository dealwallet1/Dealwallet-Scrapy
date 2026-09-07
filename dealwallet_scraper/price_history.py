import logging
import os
import sys
from urllib.parse import quote

import requests
from dotenv import load_dotenv


if sys.stdout:
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="replace",
    )


load_dotenv()


logger = logging.getLogger(__name__)


SUPABASE_URL = os.getenv("Dealwallet_supabase_url")
SUPABASE_KEY = os.getenv("Dealwallet_supabase_key")
SUPABASE_SCHEMA = os.getenv(
    "Dealwallet_SUPABASE_SCHEMA",
    "public",
)
SUPABASE_TABLE = os.getenv(
    "Dealwallet_SUPABASE_TABLE",
    "products",
)


# ============================================================
# EXPECTED PRODUCT FIELDS
# ============================================================

REQUIRED_FIELDS = [
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
    "timestamp",
]


# ============================================================
# AFFILIATE URL
# ============================================================

def generate_affiliate_url(
    product_link,
    cid="237728",
    subid="balu",
):
    """
    Generate affiliate URL from the original product URL.
    """

    if not product_link or product_link == "N/A":
        return None

    encoded_url = quote(
        product_link,
        safe="",
    )

    affiliate_url = (
        f"https://linksredirect.com/?cid={cid}"
        f"&subid={subid}"
        f"&subid2=&subid3=&subid4=&subid5="
        f"&source=api&url={encoded_url}"
    )

    return affiliate_url


# ============================================================
# FIELD VALIDATION
# ============================================================

def validate_product(product):
    """
    Validate scraped product data before sending it
    to Supabase.
    """

    if not isinstance(product, dict):
        logger.error(
            "Invalid product received. Expected dictionary."
        )
        return False

    missing_fields = []

    for field in REQUIRED_FIELDS:
        if field not in product:
            missing_fields.append(field)

    if missing_fields:
        logger.error(
            "Product is missing required fields: %s",
            ", ".join(missing_fields),
        )
        return False

    # --------------------------------------------------------
    # Validate important values
    # --------------------------------------------------------

    if not product.get("name"):
        logger.error("Product name is empty.")
        return False

    if product.get("price") is None:
        logger.error(
            "Price is missing for product: %s",
            product.get("name"),
        )
        return False

    if not product.get("product_link"):
        logger.error(
            "Product link is missing for product: %s",
            product.get("name"),
        )
        return False

    if not product.get("store_id"):
        logger.error(
            "Store ID is missing for product: %s",
            product.get("name"),
        )
        return False

    return True


# ============================================================
# PREPARE PRODUCT
# ============================================================

def prepare_product(product):
    """
    Validate and prepare scraped product data
    before inserting it into Supabase.
    """

    if not validate_product(product):
        return None

    product_data = {
        "name": product.get("name"),
        "price": product.get("price"),
        "currency": product.get("currency"),
        "original_price": product.get("original_price"),
        "discount": product.get("discount"),
        "ratings": product.get("ratings"),
        "description": product.get("description"),
        "image_link": product.get("image_link"),
        "product_link": product.get("product_link"),
        "affiliate_url": generate_affiliate_url(
            product.get("product_link")
        ),
        "organization_id": product.get(
            "organization_id"
        ),
        "store_id": product.get("store_id"),
        "categories_id": product.get(
            "categories_id"
        ),
        "timestamp": product.get("timestamp"),
    }

    return product_data


# ============================================================
# SEND ONE PRODUCT TO SUPABASE
# ============================================================

def send_to_database(product):
    """
    Validate, prepare and send one scraped product
    to the Supabase products table.
    """

    if not SUPABASE_URL:
        logger.error(
            "Dealwallet_supabase_url is not configured."
        )
        return False

    if not SUPABASE_KEY:
        logger.error(
            "Dealwallet_supabase_key is not configured."
        )
        return False

    product_data = prepare_product(product)

    if not product_data:
        logger.error(
            "Product validation failed. "
            "Data was not sent to database."
        )
        return False

    url = (
        f"{SUPABASE_URL}/rest/v1/"
        f"{SUPABASE_TABLE}"
    )

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
        "Content-Profile": SUPABASE_SCHEMA,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=product_data,
            timeout=30,
        )

        response.raise_for_status()

        logger.info(
            "Product sent successfully: %s | Store: %s",
            product_data["name"],
            product_data["store_id"],
        )

        return True

    except requests.exceptions.RequestException as exc:

        logger.error(
            "Failed to send product to Supabase: %s",
            exc,
        )

        if exc.response is not None:
            logger.error(
                "Supabase response: %s",
                exc.response.text,
            )

        return False


# ============================================================
# SEND MULTIPLE PRODUCTS
# ============================================================

def send_products_to_database(products):
    """
    Validate and send multiple scraped products
    to Supabase.
    """

    if not isinstance(products, list):
        logger.error(
            "Expected products to be a list."
        )
        return 0

    success_count = 0

    for product in products:

        if send_to_database(product):
            success_count += 1

    logger.info(
        "Database upload completed: %s/%s products sent.",
        success_count,
        len(products),
    )

    return success_count


# def send_to_database(product):
#     """
#     Test mode:
#     Validate and display product data,
#     but do NOT send it to Supabase.
#     """

#     product_data = prepare_product(product)

#     if not product_data:
#         print("Product validation failed.")
#         return False

#     print("\n" + "=" * 60)
#     print("PRODUCT READY FOR DATABASE")
#     print("=" * 60)

#     for key, value in product_data.items():
#         print(f"{key}: {value}")

#     print("=" * 60)
#     print("DATABASE SEND DISABLED - TEST MODE")
#     print("=" * 60 + "\n")

#     return True