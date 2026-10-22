import atexit
import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg2
import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

FAILED_PRODUCTS_DIR = PROJECT_ROOT / "failed_products"


def save_failed_product(product, reason="Unknown error"):
    """Save a failed database product and its reason for later retry/debugging."""
    if isinstance(product, dict):
        record = dict(product)
    else:
        record = {"product_data": str(product)}

    record["failure_reason"] = str(reason)
    record["failed_at"] = datetime.now().isoformat(timespec="seconds")

    store_value = (
        record.get("store_name")
        or record.get("store_id")
        or record.get("store")
        or "unknown_store"
    )
    store_slug = (
        re.sub(r"[^a-zA-Z0-9_-]+", "_", str(store_value))
        .strip("_")
        .lower()
        or "unknown_store"
    )

    # Separate process files prevent concurrent spiders from overwriting each other.
    output_file = (
        FAILED_PRODUCTS_DIR
        / f"{store_slug}_failed_products_{os.getpid()}.json"
    )

    try:
        FAILED_PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
        records = []

        if output_file.exists():
            try:
                with output_file.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, list):
                    records = loaded
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "Could not read previous failed-product file; "
                    "starting a new list: %s",
                    output_file,
                )

        records.append(record)

        temporary_file = output_file.with_suffix(
            output_file.suffix + ".tmp"
        )
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(
                records,
                file,
                ensure_ascii=False,
                indent=4,
                default=str,
            )

        os.replace(temporary_file, output_file)

        logger.warning(
            "FAILED PRODUCT SAVED | Product=%s | Reason=%s | File=%s",
            record.get("name", "Unknown"),
            reason,
            output_file,
        )

    except Exception:
        logger.exception(
            "FAILED TO SAVE FAILED PRODUCT | Product=%s",
            record.get("name", "Unknown"),
        )


# DATABASE CONFIGURATION

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
DB_PRODUCTS_TABLE = os.getenv("DB_PRODUCTS_TABLE", "products")
DB_PRICE_HISTORY_TABLE = os.getenv(
    "DB_PRICE_HISTORY_TABLE", "price_history"
)
DB_ORGANIZATION_TABLE = os.getenv(
    "DB_ORGANIZATION_TABLE", "organization"
)
DB_STORE_TABLE = os.getenv("DB_STORE_TABLE", "stores")
DB_CATEGORY_TABLE = os.getenv("DB_CATEGORY_TABLE", "categories")

# CUELINKS CONFIGURATION

CUELINKS_API_KEY = os.getenv("CUELINKS_API_KEY")
CUELINKS_CHANNEL_ID = os.getenv("CUELINKS_CHANNEL_ID")
CUELINKS_CONVERT_URL = (
    "https://developers.cuelinks.com/"
    "pub_api/v3/links/convert"
)
CUELINKS_TIMEOUT = int(os.getenv("CUELINKS_TIMEOUT", "30"))

NON_AFFILIATED_STORES = set()

# DATABASE PROCESSING COUNTERS

DATABASE_STATS = {
    "total": 0,
    "updated": 0,
    "unchanged": 0,
    "new": 0,
    "failed": 0,
}


def record_database_result(status, product_name="Unknown"):
    """Record and log the cumulative database processing result."""
    if status not in ("updated", "unchanged", "new", "failed"):
        return

    DATABASE_STATS[status] += 1
    DATABASE_STATS["total"] += 1

    logger.info(
        "DATABASE PROGRESS | Total: %s | Updated: %s | Unchanged: %s | "
        "New: %s | Failed: %s | Last: %s | Product: %s",
        DATABASE_STATS["total"],
        DATABASE_STATS["updated"],
        DATABASE_STATS["unchanged"],
        DATABASE_STATS["new"],
        DATABASE_STATS["failed"],
        status.upper(),
        product_name,
    )


def log_database_summary():
    """Log the final cumulative database processing summary."""
    if DATABASE_STATS["total"] == 0:
        return

    logger.info("=" * 80)
    logger.info("FLIPKART DATABASE PROCESSING SUMMARY")
    logger.info("=" * 80)
    logger.info("Total processed : %s", DATABASE_STATS["total"])
    logger.info("Updated         : %s", DATABASE_STATS["updated"])
    logger.info("Unchanged       : %s", DATABASE_STATS["unchanged"])
    logger.info("New             : %s", DATABASE_STATS["new"])
    logger.info("Failed          : %s", DATABASE_STATS["failed"])
    logger.info("=" * 80)


atexit.register(log_database_summary)

# PRODUCT URL CLEANING

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


# CUELINKS AFFILIATE URL

def get_store_domain(product_link):
    """Return normalized merchant domain from a product URL."""
    if not product_link or product_link == "N/A":
        return None

    try:
        hostname = urlsplit(product_link).hostname

        if not hostname:
            return None

        hostname = hostname.lower().strip()

        if hostname.startswith("www."):
            hostname = hostname[4:]

        return hostname

    except Exception:
        return None


def generate_affiliate_url(product_link):
    """Convert the original merchant product URL through Cuelinks."""
    if not product_link or product_link == "N/A":
        logger.warning(
            "Product link is missing. Affiliate URL cannot be generated."
        )
        return None

    store_domain = get_store_domain(product_link)

    if store_domain in NON_AFFILIATED_STORES:
        logger.info(
            "CUELINKS SKIPPED | Store already confirmed as NOT AFFILIATED "
            "| Store: %s | URL: %s",
            store_domain,
            product_link,
        )
        return None

    if not CUELINKS_API_KEY:
        logger.error("CUELINKS_API_KEY is missing in .env.")
        return None

    headers = {
        "Authorization": f"Token {CUELINKS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {"url": product_link}

    if CUELINKS_CHANNEL_ID:
        try:
            payload["channel_id"] = int(CUELINKS_CHANNEL_ID)
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
        data = response_data.get("data", {})

        tracking_url = data.get("tracking_url")
        affiliated = data.get("affiliated")
        campaign = data.get("campaign")

        if affiliated is False and store_domain:
            NON_AFFILIATED_STORES.add(store_domain)
            logger.warning(
                "CUELINKS STORE MARKED NOT AFFILIATED | "
                "Store: %s | Future Cuelinks requests will be skipped.",
                store_domain,
            )

        if not tracking_url:
            logger.error(
                "CUELINKS CONVERSION FAILED | No tracking_url returned "
                "| Product URL: %s | Response: %s",
                product_link,
                response_data,
            )
            return None

        logger.info(
            "CUELINKS CONVERSION SUCCESS | Affiliated: %s",
            affiliated,
        )

        if campaign:
            logger.info(
                "CUELINKS CAMPAIGN | ID: %s | Name: %s",
                campaign.get("id"),
                campaign.get("name"),
            )

        if affiliated is False:
            logger.warning(
                "CUELINKS LINK NOT AFFILIATED | Product URL: %s",
                product_link,
            )
            return None

        return tracking_url

    except requests.exceptions.Timeout:
        logger.error("CUELINKS API TIMEOUT | URL: %s", product_link)
        return None

    except requests.exceptions.HTTPError as exc:
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text
            except Exception:
                response_text = ""

        logger.error(
            "CUELINKS API HTTP ERROR | URL: %s | Error: %s | Response: %s",
            product_link,
            exc,
            response_text,
        )
        return None

    except requests.exceptions.RequestException as exc:
        logger.error(
            "CUELINKS API REQUEST ERROR | URL: %s | Error: %s",
            product_link,
            exc,
        )
        return None

    except ValueError as exc:
        logger.error(
            "CUELINKS INVALID JSON RESPONSE | URL: %s | Error: %s",
            product_link,
            exc,
        )
        return None

    except Exception as exc:
        logger.exception(
            "CUELINKS CONVERSION ERROR | URL: %s | Error: %s",
            product_link,
            exc,
        )
        return None


# DATABASE CONNECTION

_SHARED_CONNECTION = None


def get_connection():
    """Return a reusable PostgreSQL connection."""
    global _SHARED_CONNECTION

    if _SHARED_CONNECTION is not None:
        try:
            if _SHARED_CONNECTION.closed == 0:
                return _SHARED_CONNECTION
        except Exception:
            pass

        try:
            _SHARED_CONNECTION.close()
        except Exception:
            pass

        _SHARED_CONNECTION = None

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

    logger.info("OPENING SHARED POSTGRESQL CONNECTION")

    _SHARED_CONNECTION = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )

    logger.info("SHARED POSTGRESQL CONNECTION READY")
    return _SHARED_CONNECTION


def close_shared_connection():
    """Close the shared PostgreSQL connection at process shutdown."""
    global _SHARED_CONNECTION

    if _SHARED_CONNECTION is None:
        return

    try:
        if _SHARED_CONNECTION.closed == 0:
            _SHARED_CONNECTION.close()
            logger.info("SHARED POSTGRESQL CONNECTION CLOSED")
    except Exception as exc:
        logger.warning(
            "SHARED POSTGRESQL CONNECTION CLOSE FAILED | Error: %s",
            exc,
        )
    finally:
        _SHARED_CONNECTION = None


def reset_shared_connection():
    """Close and clear the shared connection after a connection failure."""
    global _SHARED_CONNECTION

    if _SHARED_CONNECTION is not None:
        try:
            _SHARED_CONNECTION.close()
        except Exception:
            pass

    _SHARED_CONNECTION = None


atexit.register(close_shared_connection)

# LOOKUP ORGANIZATION UUID

def get_organization_id(cursor, organization_name):
    """Find organization UUID using organization name."""
    if not organization_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_ORGANIZATION_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(query, (organization_name,))
    result = cursor.fetchone()

    if not result:
        logger.error("Organization not found: %s", organization_name)
        return None

    return str(result[0])


# LOOKUP STORE UUID

def get_store_id(cursor, store_name):
    """Find store UUID using store name."""
    if not store_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_STORE_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(query, (store_name,))
    result = cursor.fetchone()

    if not result:
        logger.error("Store not found: %s", store_name)
        return None

    return str(result[0])


# LOOKUP CATEGORY UUID

def get_category_id(cursor, category_name):
    """Find category UUID using category name."""
    if not category_name:
        return None

    query = f'''
        SELECT id
        FROM "{DB_SCHEMA}"."{DB_CATEGORY_TABLE}"
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
        LIMIT 1
    '''

    cursor.execute(query, (category_name,))
    result = cursor.fetchone()

    if not result:
        logger.error("Category not found: %s", category_name)
        return None

    return str(result[0])


# COMMON PRICE / DISCOUNT VALIDATION

DISCOUNT_TOLERANCE_PERCENT = Decimal("2")


def _to_decimal(value):
    """Convert a scraped numeric value to Decimal."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    text_value = str(value).strip()

    if not text_value or text_value.upper() in {
        "N/A", "NA", "NONE", "NULL", "-"
    }:
        return None

    text_value = (
        text_value.replace(",", "")
        .replace("₹", "")
        .replace("Rs.", "")
        .replace("Rs", "")
        .strip()
    )
    text_value = text_value.replace("%", "").strip()
    text_value = re.sub(r"[^0-9.\\-]", "", text_value)

    if not text_value or text_value in {"-", ".", "-."}:
        return None

    try:
        return Decimal(text_value)
    except InvalidOperation:
        return None


def validate_product_price(product):
    """Validate price and discount consistency before database operations."""
    if not isinstance(product, dict):
        return False, "Product is not a dictionary"

    name = product.get("name", "Unknown")
    current_price = _to_decimal(product.get("price"))
    original_price = _to_decimal(product.get("original_price"))
    discount_raw = product.get("discount")
    scraped_discount = _to_decimal(discount_raw)

    if current_price is None:
        return False, "Current price is missing or not numeric"

    if current_price <= 0:
        return False, (
            f"Current price must be greater than zero "
            f"(received {current_price})"
        )

    if discount_raw is not None and str(discount_raw).strip() != "":
        if scraped_discount is None:
            return False, f"Discount is not numeric (received {discount_raw})"

        if scraped_discount < 0 or scraped_discount > 100:
            return False, (
                f"Discount is outside 0-100% (received {scraped_discount}%)"
            )

    if original_price is None:
        logger.info(
            "PRICE VALIDATION | Original price unavailable; MRP-dependent "
            "checks skipped | Product: %s",
            name,
        )
        return True, None

    if original_price <= 0:
        return False, (
            f"Original price must be greater than zero "
            f"(received {original_price})"
        )

    if current_price > original_price:
        return False, (
            f"Current price {current_price} is greater than "
            f"original price {original_price}"
        )

    if (
        current_price == original_price
        and scraped_discount is not None
        and scraped_discount > 0
    ):
        return False, (
            f"Current price equals MRP but scraped discount is "
            f"{scraped_discount}%"
        )

    if scraped_discount is not None:
        calculated_discount = (
            (original_price - current_price) / original_price
        ) * Decimal("100")
        difference = abs(calculated_discount - scraped_discount)

        if difference > DISCOUNT_TOLERANCE_PERCENT:
            return False, (
                "Calculated discount mismatch | "
                f"Calculated: {calculated_discount:.4f}% | "
                f"Scraped: {scraped_discount}% | "
                f"Difference: {difference:.4f} percentage points "
                f"(allowed: {DISCOUNT_TOLERANCE_PERCENT})"
            )

    return True, None
# PREPARE PRODUCT

def prepare_product(product):
    """Validate and prepare scraped product data."""
    if not product:
        logger.warning("Empty product received. Product skipped.")
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
                "Required field '%s' is missing. Product skipped: %s",
                field,
                product.get("name", "Unknown"),
            )
            return None

    original_product_link = product.get("product_link")
    cleaned_product_link = clean_product_url(original_product_link)
    product["product_link"] = cleaned_product_link

    affiliate_url = generate_affiliate_url(original_product_link)
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


# FIND EXISTING PRODUCT

def find_existing_product(cursor, store_id, product_name):
    """
    Find an existing product using store_id + name.
    Returns (product_id, existing_price), or None.
    """
    query = f'''
        SELECT id, price
        FROM "{DB_SCHEMA}"."{DB_PRODUCTS_TABLE}"
        WHERE store_id = %s
          AND name = %s
        LIMIT 1
    '''

    cursor.execute(query, (store_id, product_name))
    result = cursor.fetchone()

    if not result:
        return None

    return str(result[0]), result[1]


# INSERT NEW PRODUCT

def insert_product(
    cursor,
    product,
    organization_id,
    store_id,
    category_id,
):
    """Insert a new product. PostgreSQL generates the product UUID."""
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
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
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
        raise RuntimeError("Database did not return generated product UUID.")

    product_id = str(result[0])

    logger.info(
        "NEW PRODUCT INSERTED | Product: %s | UUID: %s",
        product.get("name"),
        product_id,
    )

    return product_id


# UPDATE EXISTING PRODUCT

def update_product(
    cursor,
    product_id,
    product,
    organization_id,
    store_id,
    category_id,
):
    """Update an existing product after its price changes."""
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


# INSERT PRICE HISTORY

def insert_price_history(
    cursor,
    product_id,
    product,
    organization_id,
    store_id,
    category_id,
):
    """Insert a complete snapshot of the product into price_history."""
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
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s
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
    # SEND PRODUCT TO DATABASE

def send_to_database(product):
    """
    Validate and prepare a product, then insert or update it in PostgreSQL.
    Failed products are saved to the failed_products directory.
    """
    original_product = (
        dict(product) if isinstance(product, dict) else product
    )

    # Validate before preparation because preparation may call Cuelinks.
    is_valid, validation_error = validate_product_price(product)

    if not is_valid:
        product_name = (
            product.get("name", "Unknown")
            if isinstance(product, dict)
            else "Unknown"
        )

        logger.warning(
            "PRODUCT SKIPPED BY PRICE VALIDATION | Product: %s | Reason: %s",
            product_name,
            validation_error,
        )

        save_failed_product(original_product, validation_error)
        record_database_result("failed", product_name)
        return False

    product = prepare_product(product)

    if not product:
        save_failed_product(
            original_product,
            "prepare_product() returned no product",
        )
        failed_name = (
            original_product.get("name", "Unknown")
            if isinstance(original_product, dict)
            else "Unknown"
        )
        record_database_result("failed", failed_name)
        return False

    connection = None
    cursor = None
    product_name = product.get("name", "Unknown")

    try:
        # CONNECT
        connection = get_connection()
        cursor = connection.cursor()

        # ORGANIZATION
        organization_id = get_organization_id(
            cursor,
            product.get("organization_id"),
        )

        if not organization_id:
            logger.warning(
                "Product skipped because organization lookup failed "
                "| Product: %s",
                product_name,
            )
            connection.rollback()
            save_failed_product(product, "Organization lookup failed")
            record_database_result("failed", product_name)
            return False

        # STORE
        store_id = get_store_id(
            cursor,
            product.get("store_id"),
        )

        if not store_id:
            logger.warning(
                "Product skipped because store lookup failed "
                "| Product: %s",
                product_name,
            )
            connection.rollback()
            save_failed_product(product, "Store lookup failed")
            record_database_result("failed", product_name)
            return False

        # CATEGORY
        category_id = get_category_id(
            cursor,
            product.get("categories_id"),
        )

        if not category_id:
            logger.warning(
                "Product skipped because category lookup failed "
                "| Product: %s",
                product_name,
            )
            connection.rollback()
            save_failed_product(product, "Category lookup failed")
            record_database_result("failed", product_name)
            return False

        # FIND EXISTING PRODUCT
        existing_product = find_existing_product(
            cursor,
            store_id,
            product.get("name"),
        )

        # NEW PRODUCT
        if existing_product is None:
            logger.info("NEW PRODUCT FOUND | Product: %s", product_name)

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
                "PRODUCT SAVED SUCCESSFULLY | NEW PRODUCT "
                "| Product: %s | UUID: %s",
                product_name,
                product_id,
            )

            record_database_result("new", product_name)
            return True

        # EXISTING PRODUCT
        existing_product_id, existing_price = existing_product
        current_price = product.get("price")

        logger.info(
            "EXISTING PRODUCT FOUND | Product: %s | UUID: %s "
            "| Existing Price: %s | Current Price: %s",
            product_name,
            existing_product_id,
            existing_price,
            current_price,
        )

        # PRICE UNCHANGED
        if existing_price == current_price:
            logger.info(
                "PRICE UNCHANGED | No product update or price history entry "
                "| Product: %s | UUID: %s | Price: %s",
                product_name,
                existing_product_id,
                current_price,
            )

            connection.rollback()
            record_database_result("unchanged", product_name)
            return True

        # PRICE CHANGED
        logger.info(
            "PRICE CHANGED | Product: %s | Old Price: %s | New Price: %s",
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
            "PRODUCT UPDATED AND HISTORY SAVED | Product: %s | UUID: %s "
            "| Old Price: %s | New Price: %s",
            product_name,
            existing_product_id,
            existing_price,
            current_price,
        )

        record_database_result("updated", product_name)
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
                reset_shared_connection()

        logger.error(
            "PRODUCT SKIPPED | Product: %s | Error: %s",
            product_name,
            exc,
        )
        logger.exception("Database error details")

        if isinstance(exc, psycopg2.OperationalError):
            reset_shared_connection()

        save_failed_product(product, exc)
        record_database_result("failed", product_name)
        return False

    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        # Keep the shared PostgreSQL connection open for the next product.
        # It is closed once at process shutdown.
        pass