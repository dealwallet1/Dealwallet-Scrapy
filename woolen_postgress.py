import csv
import os
import re
from urllib.parse import quote

import psycopg2
import requests
from dotenv import load_dotenv


CSV_FILE = r"C:\Users\batti\Downloads\woollen_wear_kids_winter_products.csv"

STORE_ID = "461a7372-d98d-48a5-b6e4-6121ba85ebca"

ORGANIZATION_ID = "0195fae1-1e74-7f83-82db-b3031ddcbde1"

CATEGORIES_ID = "9dac2085-64ce-4093-88e2-683db30f6812"


BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

ENV_FILE = os.path.join(
    BASE_DIR,
    ".env"
)

load_dotenv(ENV_FILE)


PGHOST = os.getenv("PGHOST")
PGPORT = os.getenv("PGPORT", "5432")
PGDATABASE = os.getenv("PGDATABASE")
PGUSER = os.getenv("PGUSER")
PGPASSWORD = os.getenv("PGPASSWORD")

CUELINKS_API_KEY = os.getenv(
    "CUELINKS_API_KEY"
)

CUELINKS_API_URL = (
   "https://developers.cuelinks.com/pub_api/v3/links/convert"
)


if not PGHOST:
    raise ValueError("PGHOST is missing in .env")

if not PGDATABASE:
    raise ValueError("PGDATABASE is missing in .env")

if not PGUSER:
    raise ValueError("PGUSER is missing in .env")

if not PGPASSWORD:
    raise ValueError("PGPASSWORD is missing in .env")

if not CUELINKS_API_KEY:
    raise ValueError(
        "CUELINKS_API_KEY is missing in .env"
    )


def get_connection():

    return psycopg2.connect(
        host=PGHOST,
        port=PGPORT,
        database=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD
    )


def convert_to_cuelinks(
    product_url
):

    if not product_url or product_url in (
        "N/A",
        " "
    ):

        print(
            "[CUELINKS] Empty product URL"
        )

        return " "

    headers = {

        "Authorization":
            f"Token {CUELINKS_API_KEY}",

        "Content-Type":
            "application/json",

        "Accept":
            "application/json"
    }

    payload = {
        "url": product_url
    }

    try:

        print(
            f"[CUELINKS] Converting: {product_url}"
        )

        response = requests.post(
            CUELINKS_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        print(
            "[CUELINKS] Status:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                "[CUELINKS] API ERROR:"
            )

            print(
                response.text
            )

            return " "

        result = response.json()

        print(
            "[CUELINKS] Response:"
        )

        print(
            result
        )

        data = result.get(
            "data",
            {}
        )

        affiliated = data.get(
            "affiliated",
            False
        )

        if not affiliated:

            print(
                "[CUELINKS] URL is not affiliated"
            )

            return " "

        tracking_url = data.get(
            "tracking_url",
            ""
        )

        if not tracking_url:

            print(
                "[CUELINKS] No tracking URL returned"
            )

            return " "

        print(
            "[CUELINKS] Affiliate URL:"
        )

        print(
            tracking_url
        )

        return tracking_url

    except requests.exceptions.Timeout:

        print(
            "[CUELINKS] Request timed out"
        )

        return " "

    except requests.exceptions.RequestException as e:

        print(
            "[CUELINKS] Request error:",
            e
        )

        return " "

    except ValueError:

        print(
            "[CUELINKS] Invalid JSON response"
        )

        try:

            print(
                response.text
            )

        except Exception:

            pass

        return " "

    except Exception as e:

        print(
            "[CUELINKS] Unexpected error:",
            e
        )

        return " "


def clean_text(value):

    if value is None:
        return " "

    value = str(
        value
    ).strip()

    if not value:
        return " "

    return value


def clean_float(value):

    if value is None:
        return None

    value = str(
        value
    ).strip()

    if not value:
        return None

    if value.upper() in (
        "N/A",
        "NA",
        "NULL"
    ):
        return None

    value = value.replace(
        "₹",
        ""
    )

    value = value.replace(
        ",",
        ""
    )

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        value
    )

    if not match:
        return None

    try:

        return float(
            match.group()
        )

    except ValueError:

        return None


def clean_int(value):

    if value is None:
        return None

    value = str(
        value
    ).strip()

    if not value:
        return None

    if value.upper() in (
        "N/A",
        "NA",
        "NULL"
    ):
        return None

    match = re.search(
        r"-?\d+",
        value
    )

    if not match:
        return None

    try:

        return int(
            match.group()
        )

    except ValueError:

        return None


def check_csv_file():

    print(
        "\n" + "=" * 100
    )

    print(
        "CHECKING CSV FILE"
    )

    print(
        "=" * 100
    )

    print(
        f"\nCSV file:\n{CSV_FILE}"
    )

    if not os.path.exists(
        CSV_FILE
    ):

        print(
            "\nERROR: CSV file does not exist."
        )

        print(
            f"Path: {CSV_FILE}"
        )

        return False

    if not os.path.isfile(
        CSV_FILE
    ):

        print(
            "\nERROR: CSV path is not a file."
        )

        return False

    print(
        "\nCSV file found successfully."
    )

    return True


def read_csv_safely():

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin-1"
    ]

    last_error = None

    for encoding in encodings:

        try:

            with open(
                CSV_FILE,
                "r",
                encoding=encoding,
                newline=""
            ) as file:

                reader = csv.DictReader(
                    file
                )

                if not reader.fieldnames:

                    raise ValueError(
                        "CSV has no columns."
                    )

                rows = list(
                    reader
                )

            print(
                f"\nCSV loaded successfully using encoding: {encoding}"
            )

            return reader.fieldnames, rows

        except UnicodeDecodeError as e:

            last_error = e

            print(
                f"Failed with encoding: {encoding}"
            )

        except Exception:

            raise

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        "Could not decode CSV using UTF-8-SIG, UTF-8, CP1252, or Latin-1."
    ) from last_error


def get_value(
    row,
    *names
):

    normalized = {}

    for key, value in row.items():

        if key is None:
            continue

        normalized[
            str(key).strip().lower()
        ] = value

    for name in names:

        key = str(
            name
        ).strip().lower()

        if key in normalized:

            return normalized[key]

    return None


def product_exists(
    cursor,
    product_link
):

    if not product_link:
        return False

    if product_link in (
        " ",
        "N/A"
    ):
        return False

    cursor.execute(
        """
        SELECT id
        FROM public.products
        WHERE store_id = %s
          AND product_link = %s
        LIMIT 1
        """,
        (
            STORE_ID,
            product_link
        )
    )

    result = cursor.fetchone()

    return result is not None


def insert_product(
    cursor,
    product
):

    query = """
        INSERT INTO public.products (
            name,
            ratings,
            image_link,
            affiliate_url,
            organization_id,
            store_id,
            categories_id,
            currency,
            product_link,
            price,
            original_price,
            discount,
            description
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
            %s
        )
    """

    cursor.execute(
        query,
        (
            product["name"],
            product["ratings"],
            product["image_link"],
            product["affiliate_url"],
            product["organization_id"],
            product["store_id"],
            product["categories_id"],
            product["currency"],
            product["product_link"],
            product["price"],
            product["original_price"],
            product["discount"],
            product["description"]
        )
    )


def process_csv():

    if not check_csv_file():
        return

    inserted = 0
    skipped = 0
    failed = 0

    current_run_links = set()

    connection = None
    cursor = None

    try:

        print(
            "\n" + "=" * 100
        )

        print(
            "CONNECTING TO POSTGRESQL"
        )

        print(
            "=" * 100
        )

        connection = get_connection()

        cursor = connection.cursor()

        print(
            "\nPostgreSQL connection successful."
        )

        print(
            "\n" + "=" * 100
        )

        print(
            "READING CSV"
        )

        print(
            "=" * 100
        )

        try:

            fieldnames, rows = read_csv_safely()

        except PermissionError:

            print(
                "\nERROR: Permission denied while opening CSV."
            )

            return

        except UnicodeDecodeError:

            print(
                "\nERROR: Could not decode CSV."
            )

            print(
                "Tried: UTF-8-SIG, UTF-8, CP1252, and Latin-1."
            )

            return

        except Exception as e:

            print(
                "\nERROR while reading CSV:"
            )

            print(e)

            return

        print(
            "\nCSV columns:"
        )

        for column in fieldnames:

            print(
                f"  - {column}"
            )

        print(
            f"\nTotal CSV rows: {len(rows)}"
        )

        print(
            "\n" + "=" * 100
        )

        print(
            "INSERTING PRODUCTS"
        )

        print(
            "=" * 100
        )

        for row_number, row in enumerate(
            rows,
            start=2
        ):

            name = clean_text(
                get_value(
                    row,
                    "name"
                )
            )

            price = clean_float(
                get_value(
                    row,
                    "price"
                )
            )

            original_price = clean_float(
                get_value(
                    row,
                    "original_price"
                )
            )

            discount = clean_int(
                get_value(
                    row,
                    "discount"
                )
            )

            currency = "₹"

            ratings = clean_text(
                get_value(
                    row,
                    "ratings"
                )
            )

            description = clean_text(
                get_value(
                    row,
                    "description"
                )
            )

            image_link = clean_text(
                get_value(
                    row,
                    "image_link"
                )
            )

            product_link = clean_text(
                get_value(
                    row,
                    "product_link"
                )
            )

            affiliate_url = convert_to_cuelinks(
                product_link
            )

            product = {

                "name": name,

                "ratings": ratings,

                "image_link": image_link,

                "affiliate_url": affiliate_url,

                "organization_id": ORGANIZATION_ID,

                "store_id": STORE_ID,

                "categories_id": CATEGORIES_ID,

                "currency": currency,

                "product_link": product_link,

                "price": price,

                "original_price": original_price,

                "discount": discount,

                "description": description
            }

            if (
                product_link
                and product_link not in (
                    " ",
                    "N/A"
                )
            ):

                if product_link in current_run_links:

                    skipped += 1

                    print(
                        f"[{row_number}] SKIPPED "
                        f"(duplicate in CSV) → {name}"
                    )

                    continue

                try:

                    exists = product_exists(
                        cursor,
                        product_link
                    )

                except Exception as e:

                    print(
                        "\nERROR DURING DUPLICATE CHECK:"
                    )

                    print(e)

                    print(
                        "\nIMPORT STOPPED."
                    )

                    print(
                        "Duplicate filter failed."
                    )

                    print(
                        "No more products will be inserted."
                    )

                    connection.rollback()

                    return

                if exists:

                    skipped += 1

                    print(
                        f"[{row_number}] SKIPPED "
                        f"(already exists) → {name}"
                    )

                    continue

            try:

                insert_product(
                    cursor,
                    product
                )

                connection.commit()

                inserted += 1

                print(
                    f"[{row_number}] INSERTED → "
                    f"{name}"
                )

                if (
                    product_link
                    and product_link not in (
                        " ",
                        "N/A"
                    )
                ):

                    current_run_links.add(
                        product_link
                    )

            except Exception as e:

                connection.rollback()

                failed += 1

                print(
                    f"[{row_number}] FAILED → "
                    f"{name}"
                )

                print(
                    f"Error: {e}"
                )

    except psycopg2.Error as e:

        print(
            "\nPOSTGRESQL ERROR:"
        )

        print(e)

    except Exception as e:

        print(
            "\nERROR:"
        )

        print(e)

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()

        print(
            "\nPostgreSQL connection closed."
        )

    print(
        "\n" + "=" * 100
    )

    print(
        "FINAL RESULT"
    )

    print(
        "=" * 100
    )

    print(
        f"\nInserted : {inserted}"
    )

    print(
        f"Skipped  : {skipped}"
    )

    print(
        f"Failed   : {failed}"
    )

    print(
        "\nMissing text fields : \" \""
    )

    print(
        "Missing numeric     : NULL"
    )

    print(
        "Currency            : ₹"
    )

    print(
        "Description         : CSV value; if missing -> \" \""
    )

    print(
        "ID/UUID             : Database generated"
    )

    print(
        "\nProcess completed."
    )


if __name__ == "__main__":
    process_csv()