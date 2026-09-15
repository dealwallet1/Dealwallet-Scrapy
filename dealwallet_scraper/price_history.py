import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
import requests
from dotenv import load_dotenv


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# LOAD .ENV FROM PROJECT ROOT
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE, override=False)


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")

DB_PRODUCTS_TABLE = os.getenv(
    "DB_PRODUCTS_TABLE",
    "products",
)

DB_PRICE_HISTORY_TABLE = os.getenv(
    "DB_PRICE_HISTORY_TABLE",
    "price_history",
)

DB_ORGANIZATION_TABLE = os.getenv(
    "DB_ORGANIZATION_TABLE",
    "organization",
)

DB_STORE_TABLE = os.getenv(
    "DB_STORE_TABLE",
    "stores",
)

DB_CATEGORY_TABLE = os.getenv(
    "DB_CATEGORY_TABLE",
    "categories",
)


# ============================================================
# CUELINKS CONFIGURATION
# ============================================================

CUELINKS_API_KEY = os.getenv(
    "CUELINKS_API_KEY"
)

CUELINKS_CHANNEL_ID = os.getenv(
    "CUELINKS_CHANNEL_ID"
)

CUELINKS_CONVERT_URL = (
    "https://developers.cuelinks.com/"
    "pub_api/v3/links/convert"
)

CUELINKS_TIMEOUT = int(
    os.getenv(
        "CUELINKS_TIMEOUT",
        "30",
    )
)


# ============================================================
# PRODUCT URL CLEANING
# ============================================================

# Tracking/navigation parameters that should not affect
# product identity.
UNUSED_URL_PARAMS = {
    "_pos",
    "_fid",
    "_ss",
}


def clean_product_url(product_url):
    """Remove unused query parameters and reconstruct the URL."""

    if not product_url or product_url == "N/A":
        return product_url

    try:
        parsed = urlsplit(product_url)

        if not parsed.query:
            return product_url

        parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )

        cleaned_parameters = [
            (key, value)
            for key, value in parameters
            if key.lower() not in UNUSED_URL_PARAMS
        ]

        cleaned_query = urlencode(
            cleaned_parameters,
            doseq=True,
        )

        cleaned_url = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                cleaned_query,
                parsed.fragment,
            )
        )

        if cleaned_url != product_url:
            logger.info(
                "PRODUCT URL CLEANED | Original: %s | Clean: %s",
                product_url,
                cleaned_url,
            )

        return cleaned_url

    except Exception as exc:
        logger.warning(
            "PRODUCT URL CLEANING FAILED | URL: %s | Error: %s",
            product_url,
            exc,
        )
        return product_url


# ============================================================
# CUELINKS AFFILIATE URL
# ============================================================

def generate_affiliate_url(product_link):
    """
    Convert the original merchant product URL through
    the Cuelinks Convert URL API.

    The tracking_url returned by Cuelinks is stored
    as affiliate_url.
    """

    if not product_link or product_link == "N/A":
        logger.warning(
            "Product link is missing. "
            "Affiliate URL cannot be generated."
        )
        return None

    if not CUELINKS_API_KEY:
        logger.error(
            "CUELINKS_API_KEY is missing in .env."
        )
        return None

    headers = {
        "Authorization": f"Token {CUELINKS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "url": product_link,
    }

    if CUELINKS_CHANNEL_ID:
        try:
            payload["channel_id"] = int(
                CUELINKS_CHANNEL_ID
            )
        except ValueError:
            logger.warning(
                "Invalid CUELINKS_CHANNEL_ID: %s",
                CUELINKS_CHANNEL_ID,
            )

    try:

        logger.info(
            "CUELINKS CONVERSION REQUEST | URL: %s",
            product_link,
        )

        response = requests.post(
            CUELINKS_CONVERT_URL,
            headers=headers,
            json=payload,
            timeout=CUELINKS_TIMEOUT,
        )

        response.raise_for_status()

        response_data = response.json()

        data = response_data.get(
            "data",
            {},
        )

        tracking_url = data.get(
            "tracking_url"
        )

        affiliated = data.get(
            "affiliated"
        )

        campaign = data.get(
            "campaign"
        )

        if not tracking_url:

            logger.error(
                "CUELINKS CONVERSION FAILED | "
                "No tracking_url returned | "
                "Product URL: %s | Response: %s",
                product_link,
                response_data,
            )

            return None

        logger.info(
            "CUELINKS CONVERSION SUCCESS | "
            "Affiliated: %s",
            affiliated,
        )

        if campaign:

            logger.info(
                "CUELINKS CAMPAIGN | "
                "ID: %s | Name: %s",
                campaign.get("id"),
                campaign.get("name"),
            )

        if affiliated is False:

            logger.warning(
                "CUELINKS LINK NOT AFFILIATED | "
                "Product URL: %s",
                product_link,
            )

            return None

        return tracking_url

    except requests.exceptions.Timeout:

        logger.error(
            "CUELINKS API TIMEOUT | URL: %s",
            product_link,
        )

        return None

    except requests.exceptions.HTTPError as exc:

        response_text = ""

        if exc.response is not None:

            try:
                response_text = exc.response.text
            except Exception:
                response_text = ""

        logger.error(
            "CUELINKS API HTTP ERROR | "
            "URL: %s | Error: %s | Response: %s",
            product_link,
            exc,
            response_text,
        )

        return None

    except requests.exceptions.RequestException as exc:

        logger.error(
            "CUELINKS API REQUEST ERROR | "
            "URL: %s | Error: %s",
            product_link,
            exc,
        )

        return None

    except ValueError as exc:

        logger.error(
            "CUELINKS INVALID JSON RESPONSE | "
            "URL: %s | Error: %s",
            product_link,
            exc,
        )

        return None

    except Exception as exc:

        logger.exception(
            "CUELINKS CONVERSION ERROR | "
            "URL: %s | Error: %s",
            product_link,
            exc,
        )

        return None


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    """
    Create PostgreSQL database connection.
    """

    missing = []

    if not DB_HOST:
        missing.append("DB_HOST")

    if not DB_NAME:
        missing.append("DB_NAME")

    if not DB_USER:
        missing.append("DB_USER")

    if not DB_PASSWORD:
        missing.append("DB_PASSWORD")

    if missing:

        raise RuntimeError(
            "Missing database configuration in .env: "
            f"{', '.join(missing)}"
        )

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# ============================================================
# LOOKUP ORGANIZATION UUID
# ============================================================

def get_organization_id(
    cursor,
    organization_name,
):
    """
    Find organization UUID using organization name.
    """

    if not organization_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_ORGANIZATION_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(
        query,
        (organization_name,),
    )

    result = cursor.fetchone()

    if not result:

        logger.error(
            "Organization not found: %s",
            organization_name,
        )

        return None

    return str(result[0])


# ============================================================
# LOOKUP STORE UUID
# ============================================================

def get_store_id(
    cursor,
    store_name,
):
    """
    Find store UUID using store name.
    """

    if not store_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_STORE_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(
        query,
        (store_name,),
    )

    result = cursor.fetchone()

    if not result:

        logger.error(
            "Store not found: %s",
            store_name,
        )

        return None

    return str(result[0])


# ============================================================
# LOOKUP CATEGORY UUID
# ============================================================

def get_category_id(
    cursor,
    category_name,
):
    """
    Find category UUID using category name.
    """

    if not category_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_CATEGORY_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(
        query,
        (category_name,),
    )

    result = cursor.fetchone()

    if not result:

        logger.error(
            "Category not found: %s",
            category_name,
        )

        return None

    return str(result[0])


# ============================================================
# PREPARE PRODUCT
# ============================================================

def prepare_product(product):       
    """
    Validate and prepare scraped product data.

    The original product_link is converted through
    Cuelinks and the returned tracking_url is stored
    as affiliate_url.
    """

    if not product:

        logger.warning(
            "Empty product received. Product skipped."
        )

        return None

    required_fields = [
        "name",
        "price",
        "product_link",
        "image_link",
        "discount",
        "organization_id",
        "store_id",
        "categories_id",
    ]

    for field in required_fields:

        value = product.get(field)

        if value is None or value == "":

            logger.warning(
                "Required field '%s' is missing. "
                "Product skipped: %s",
                field,
                product.get(
                    "name",
                    "Unknown",
                ),
            )

            return None

    # --------------------------------------------------------
    # PRODUCT URL CLEANING
    # --------------------------------------------------------

    original_product_link = product.get("product_link")

    cleaned_product_link = clean_product_url(
        original_product_link
    )

    # Use the reconstructed URL for product matching and DB storage.
    product["product_link"] = cleaned_product_link

    # --------------------------------------------------------
    # CUELINKS URL CONVERSION
    # --------------------------------------------------------

    # Keep the existing Cuelinks behavior by sending the
    # original scraped merchant URL to Cuelinks.
    affiliate_url = generate_affiliate_url(
        original_product_link
    )

    product["affiliate_url"] = affiliate_url

    if affiliate_url:
        logger.info(
            "AFFILIATE URL GENERATED | Product: %s",
            product.get("name"),
        )
    else:
        logger.info(
            "NO AFFILIATE URL | Product: %s",
            product.get("name"),
        )

    return product

# ============================================================
# FIND EXISTING PRODUCT
# ============================================================

def find_existing_product(
    cursor,
    store_id,
    product_link,
    product_name,
):
    """
    Find an existing product using:

        store_id + product_link + name

    Returns:
        (product_id, existing_price)

    or:
        None
    """

    query = f'''
        SELECT
            id,
            price
        FROM "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}"
        WHERE store_id = %s
          AND product_link = %s
          AND name = %s
        LIMIT 1
    '''

    cursor.execute(
        query,
        (
            store_id,
            product_link,
            product_name,
        ),
    )

    result = cursor.fetchone()

    if not result:
        return None

    return (
        str(result[0]),
        result[1],
    )


# ============================================================
# INSERT NEW PRODUCT
# ============================================================

def insert_product(
    cursor,
    product,
    organization_id,
    store_id,
    category_id,
):
    """
    Insert a new product.

    PostgreSQL automatically generates products.id.
    """

    query = f'''
        INSERT INTO "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}" (
            name,
            price,
            currency,
            original_price,
            discount,
            ratings,
            description,
            image_link,
            product_link,
            organization_id,
            store_id,
            categories_id,
            affiliate_url,
            created_at
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING id
    '''

    cursor.execute(
        query,
        (
            product.get("name"),
            product.get("price"),
            product.get("currency"),
            product.get("original_price"),
            product.get("discount"),
            product.get("ratings"),
            product.get("description"),
            product.get("image_link"),
            product.get("product_link"),
            organization_id,
            store_id,
            category_id,
            product.get("affiliate_url"),
            product.get("created_at"),
        ),
    )

    result = cursor.fetchone()

    if not result:

        raise RuntimeError(
            "Database did not return generated product UUID."
        )

    product_id = str(result[0])

    logger.info(
        "NEW PRODUCT INSERTED | Product: %s | UUID: %s",
        product.get("name"),
        product_id,
    )

    return product_id


# ============================================================
# UPDATE EXISTING PRODUCT
# ============================================================

def update_product(
    cursor,
    product_id,
    product,
    organization_id,
    store_id,
    category_id,
):
    """
    Update an existing product after its price changes.
    """

    query = f'''
        UPDATE "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}"
        SET
            name = %s,
            price = %s,
            currency = %s,
            original_price = %s,
            discount = %s,
            ratings = %s,
            description = %s,
            image_link = %s,
            product_link = %s,
            organization_id = %s,
            store_id = %s,
            categories_id = %s,
            affiliate_url = %s,
            created_at = %s
        WHERE id = %s
    '''

    cursor.execute(
        query,
        (
            product.get("name"),
            product.get("price"),
            product.get("currency"),
            product.get("original_price"),
            product.get("discount"),
            product.get("ratings"),
            product.get("description"),
            product.get("image_link"),
            product.get("product_link"),
            organization_id,
            store_id,
            category_id,
            product.get("affiliate_url"),
            product.get("created_at"),
            product_id,
        ),
    )

    if cursor.rowcount != 1:

        raise RuntimeError(
            f"Product update failed. "
            f"Product UUID: {product_id}"
        )

    logger.info(
        "EXISTING PRODUCT UPDATED | Product: %s | UUID: %s",
        product.get("name"),
        product_id,
    )


# ============================================================
# INSERT PRICE HISTORY
# ============================================================

def insert_price_history(
    cursor,
    product_id,
    product,
    organization_id,
    store_id,
    category_id,
):
    """
    Insert a complete snapshot of the product into price_history.
    """

    query = f'''
        INSERT INTO "{DB_SCHEMA}"."{DB_PRICE_HISTORY_TABLE}" (
            product_id,
            name,
            price,
            currency,
            original_price,
            discount,
            ratings,
            description,
            image_link,
            product_link,
            organization_id,
            store_id,
            categories_id,
            created_at,
            affiliate_url
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    '''

    cursor.execute(
        query,
        (
            product_id,
            product.get("name"),
            product.get("price"),
            product.get("currency"),
            product.get("original_price"),
            product.get("discount"),
            product.get("ratings"),
            product.get("description"),
            product.get("image_link"),
            product.get("product_link"),
            organization_id,
            store_id,
            category_id,
            product.get("created_at"),
            product.get("affiliate_url"),
        ),
    )

    logger.info(
        "PRICE HISTORY INSERTED | Product: %s | UUID: %s",
        product.get("name"),
        product_id,
    )


# ============================================================
# SEND PRODUCT TO DATABASE
# ============================================================

def send_to_database(product):
    """
    Complete database flow for one scraped product.

    Flow:

    1. Validate product.
    2. Convert product URL through Cuelinks.
    3. Connect to PostgreSQL.
    4. Resolve organization.
    5. Resolve store.
    6. Resolve category.
    7. Check existing product.
    8. New product:
           INSERT products
           INSERT price_history
    9. Existing product:
           Compare price.
    10. Price changed:
           UPDATE products
           INSERT price_history
    11. Price unchanged:
           No update/history.
    12. Commit.
    """

    product = prepare_product(product)

    if not product:
        return False

    connection = None
    cursor = None

    product_name = product.get(
        "name",
        "Unknown",
    )

    try:

        # ----------------------------------------------------
        # CONNECT
        # ----------------------------------------------------

        connection = get_connection()
        cursor = connection.cursor()

        # ----------------------------------------------------
        # ORGANIZATION
        # ----------------------------------------------------

        organization_id = get_organization_id(
            cursor,
            product.get("organization_id"),
        )

        if not organization_id:

            logger.warning(
                "Product skipped because organization "
                "lookup failed | Product: %s",
                product_name,
            )

            connection.rollback()

            return False

        # ----------------------------------------------------
        # STORE
        # ----------------------------------------------------

        store_id = get_store_id(
            cursor,
            product.get("store_id"),
        )

        if not store_id:

            logger.warning(
                "Product skipped because store "
                "lookup failed | Product: %s",
                product_name,
            )

            connection.rollback()

            return False

        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        category_id = get_category_id(
            cursor,
            product.get("categories_id"),
        )

        if not category_id:

            logger.warning(
                "Product skipped because category "
                "lookup failed | Product: %s",
                product_name,
            )

            connection.rollback()

            return False

        # ----------------------------------------------------
        # FIND EXISTING PRODUCT
        # ----------------------------------------------------

        existing_product = find_existing_product(
            cursor,
            store_id,
            product.get("product_link"),
            product.get("name"),
        )

        # ====================================================
        # NEW PRODUCT
        # ====================================================

        if existing_product is None:

            logger.info(
                "NEW PRODUCT FOUND | Product: %s",
                product_name,
            )

            product_id = insert_product(
                cursor,
                product,
                organization_id,
                store_id,
                category_id,
            )

            insert_price_history(
                cursor,
                product_id,
                product,
                organization_id,
                store_id,
                category_id,
            )

            connection.commit()

            logger.info(
                "PRODUCT SAVED SUCCESSFULLY | "
                "NEW PRODUCT | Product: %s | UUID: %s",
                product_name,
                product_id,
            )

            return True

        # ====================================================
        # EXISTING PRODUCT
        # ====================================================

        existing_product_id, existing_price = (
            existing_product
        )

        current_price = product.get("price")

        logger.info(
            "EXISTING PRODUCT FOUND | "
            "Product: %s | UUID: %s | "
            "Existing Price: %s | Current Price: %s",
            product_name,
            existing_product_id,
            existing_price,
            current_price,
        )

        # ----------------------------------------------------
        # PRICE UNCHANGED
        # ----------------------------------------------------

        if existing_price == current_price:

            logger.info(
                "PRICE UNCHANGED | "
                "No product update or price history entry | "
                "Product: %s | UUID: %s | Price: %s",
                product_name,
                existing_product_id,
                current_price,
            )

            connection.rollback()

            return True

        # ----------------------------------------------------
        # PRICE CHANGED
        # ----------------------------------------------------

        logger.info(
            "PRICE CHANGED | "
            "Product: %s | Old Price: %s | New Price: %s",
            product_name,
            existing_price,
            current_price,
        )

        update_product(
            cursor,
            existing_product_id,
            product,
            organization_id,
            store_id,
            category_id,
        )

        insert_price_history(
            cursor,
            existing_product_id,
            product,
            organization_id,
            store_id,
            category_id,
        )

        connection.commit()

        logger.info(
            "PRODUCT UPDATED AND HISTORY SAVED | "
            "Product: %s | UUID: %s | "
            "Old Price: %s | New Price: %s",
            product_name,
            existing_product_id,
            existing_price,
            current_price,
        )

        return True

    except Exception as exc:

        if connection:

            try:

                connection.rollback()

                logger.warning(
                    "Transaction rolled back for product: %s",
                    product_name,
                )

            except Exception as rollback_error:

                logger.error(
                    "Rollback failed for product: %s | Error: %s",
                    product_name,
                    rollback_error,
                )

        logger.error(
            "PRODUCT SKIPPED | Product: %s | Error: %s",
            product_name,
            exc,
        )

        logger.exception(
            "Database error details"
        )

        return False

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if connection:

            try:
                connection.close()
            except Exception:
                pass