import logging
import os
from pathlib import Path
from urllib.parse import quote

import psycopg2
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
        f"&source=api"
        f"&url={encoded_url}"
    )

    return affiliate_url


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
            f"Missing database configuration in .env: "
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

def get_organization_id(cursor, organization_name):
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

def get_store_id(cursor, store_name):
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

def get_category_id(cursor, category_name):
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
                product.get("name", "Unknown"),
            )
            return None

    # Generate affiliate URL.
    product["affiliate_url"] = generate_affiliate_url(
        product.get("product_link")
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

    The database automatically generates products.id.

    RETURNING id gets the generated UUID.
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
            f"Product update failed. Product UUID: {product_id}"
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

    price_history.id is automatically generated by PostgreSQL.

    price_history.product_id references products.id.
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
    2. Connect to PostgreSQL.
    3. Resolve organization name -> UUID.
    4. Resolve store name -> UUID.
    5. Resolve category name -> UUID.
    6. Check whether product already exists using:
           store_id + product_link + name
    7. If new:
           INSERT products
           DB generates products.id
           INSERT price_history
    8. If existing:
           Compare price.
           If price changed:
               UPDATE products
               INSERT price_history
           If price did not change:
               Do nothing.
    9. Commit.
    10. On error, rollback only this product.
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
        # RESOLVE ORGANIZATION
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
        # RESOLVE STORE
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
        # RESOLVE CATEGORY
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

        existing_product_id, existing_price = existing_product

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
        # PRICE HAS NOT CHANGED
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
        # PRICE HAS CHANGED
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