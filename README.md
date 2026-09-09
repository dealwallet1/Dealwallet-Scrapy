# Dealwallet-Scrapy

E-commerce web scraping project for extracting and standardizing product, pricing, seller, offers, and related data from online stores.

# DealWallet Scrapy

A Scrapy-based e-commerce scraping project for collecting product data from DealWallet stores and preparing scraped data for database storage.

## Project Structure

```text
Dealwallet-Scrapy/

├── dealwallet_scraper/
│   ├── spiders/
│   │   ├── __init__.py
│   │   ├── myntra.py
│   │   ├── soul_flower.py
│   │   └── wise_life.py
│   ├── __init__.py
│   ├── items.py
│   ├── middlewares.py
│   ├── pipelines.py
│   ├── price_history.py
│   └── settings.py
├── scheduler.py
├── scrapy.cfg
├── requirements.txt
├── .env
└── README.md
```

## Scrapers

### Myntra

The Myntra spider currently targets the **Men Topwear** category and collects:

* `name`
* `price`
* `currency`
* `original_price`
* `discount`
* `ratings`
* `description`
* `image_link`
* `product_link`
* `organization_id`
* `store_id`
* `categories_id`
* `created_at`

Crawl4AI is used for browser-based extraction.

### Soulflower

The Soulflower spider uses Playwright-based browser scraping and currently targets the configured categories.

It extracts product information including:

* Product name
* Price
* Original price
* Discount
* Rating
* Description
* Image URL
* Product URL
* Store information
* Category information
* Timestamp

### WiseLife

The WiseLife spider uses Crawl4AI-based browser scraping and currently targets the configured WiseLife collections.

It extracts:

* Product name
* Price
* Original price
* Discount
* Rating
* Description
* Image URL
* Product URL
* Store information
* Category information
* Timestamp

## Installation

Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

If Playwright browsers are required:

```powershell
playwright install chromium
```

## Environment Configuration

Create a `.env` file in the project root:

```env
DB_HOST=YOUR_DATABASE_HOST
DB_PORT=YOUR_DATABASE_PORT
DB_NAME=YOUR_DATABASE_NAME
DB_USER=YOUR_DATABASE_USER
DB_PASSWORD=YOUR_DATABASE_PASSWORD

DB_SCHEMA=public

DB_PRODUCTS_TABLE=products
DB_PRICE_HISTORY_TABLE=price_history

DB_ORGANIZATION_TABLE=organization
DB_STORE_TABLE=stores
DB_CATEGORY_TABLE=categories

SCRAPYD_URL=http://127.0.0.1:6800/schedule.json
SCRAPYD_PROJECT=dealwallet_scraper
SCHEDULE_INTERVAL_SECONDS=600
LOG_LEVEL=INFO
```

Do not commit `.env` or database credentials to source control.

## Running Spiders Locally

Run Myntra:

```powershell
scrapy crawl myntra
```

Run Soulflower:

```powershell
scrapy crawl soul_flower
```

Run WiseLife:

```powershell
scrapy crawl wiselife
```

## Scrapy Pipeline

Scraped products pass through the pipeline:

```text
Spider
  ↓
Scraped Product
  ↓
DealwalletScraperPipeline
  ↓
price_history.py
  ↓
Field Validation
  ↓
Affiliate URL Generation
  ↓
Product Lookup
  ↓
Price Comparison
  ↓
Product Insert / Update
  ↓
Price History Snapshot
```

The pipeline is enabled in `settings.py`:

```python
ITEM_PIPELINES = {
    "dealwallet_scraper.pipelines.DealwalletScraperPipeline": 300,
}
```

## price_history.py

`price_history.py` acts as the middle layer between the Scrapy pipeline and the PostgreSQL database.

Current responsibilities:

1. Receive scraped product data.
2. Validate required fields.
3. Prepare the database payload.
4. Generate an affiliate URL.
5. Check whether the product already exists.
6. Identify products using `store_id + product_link + name`.
7. Insert new products when they do not already exist.
8. Keep the same `products.id` for existing products.
9. Compare the current price with the stored price.
10. Update the existing product only when the price changes.
11. Insert a complete snapshot into `price_history` for a new product or a price change.
12. Do nothing when the product exists and the price has not changed.

### Required Fields

```text
name
price
currency
original_price
discount
ratings
description
image_link
product_link
organization_id
store_id
categories_id
created_at
```

### Affiliate URL

The affiliate URL is generated from `product_link`.

Current parameters:

```text
cid=237728
subid=balu
```

The original product URL is URL-encoded and included in the affiliate URL.

Example:

```text
https://linksredirect.com/?cid=237728&subid=balu&subid2=&subid3=&subid4=&subid5=&source=api&url=ENCODED_PRODUCT_URL
```

### Product and Price History Logic

The existing `products.id` UUID is used as the stable product identifier.

Product identity is based on:

```text
store_id + product_link + name
```

For a new product:

```text
Scraped Product
    ↓
Product not found
    ↓
Insert into products
    ↓
Database generates products.id
    ↓
Insert complete snapshot into price_history
    ↓
price_history.product_id = products.id
```

For an existing product:

```text
Scraped Product
    ↓
Product found
    ↓
Compare current price with stored price
    ↓
 ┌───────────────────────┐
 │                       │
Price unchanged       Price changed
 │                       │
No product update       Update products
No new history             ↓
                      Insert new full
                      snapshot into
                      price_history
```

The existing `products.id` is preserved when the product price changes.

Each `price_history` record has its own UUID `id`.

Only a **price change** triggers a product update and a new price-history snapshot.

## Database Status

PostgreSQL integration and price history logic have been implemented.

The `price_history` table has been created and stores historical product snapshots linked to the stable `products.id`.

**Current status: TEST MODE**

Database insertion is currently disabled in the Scrapy pipeline while the scraping and scheduled flow are being verified.

Current flow:

```text
Scraped data
    ↓
Field validation
    ↓
Affiliate URL generation
    ↓
Product lookup
    ↓
Price comparison
    ↓
Product insert / update
    ↓
Price history snapshot
```

After verification, database insertion can be enabled in the pipeline.

## Scrapyd

Scrapyd is used to run and manage the Scrapy spiders.

Start Scrapyd:

```powershell
scrapyd
```

Local Scrapyd:

```text
http://127.0.0.1:6800/
```

### Deploy

From the project root:

```powershell
scrapyd-deploy local
```

Verify spiders:

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:6800/listspiders.json?project=dealwallet_scraper"
```

Current spiders:

```text
myntra
soul_flower
wiselife
```

## Scheduling

The project uses `scheduler.py` to communicate with Scrapyd.

The scheduler:

1. Connects to Scrapyd.
2. Schedules the configured spiders.
3. Waits for the configured interval.
4. Schedules them again.
5. Continues until stopped.

Run:

```powershell
python scheduler.py
```

Default interval:

```text
600 seconds (10 minutes)
```

Change it in `.env`:

```env
SCHEDULE_INTERVAL_SECONDS=600
```

The scheduler does not run spiders directly. It sends scheduling requests to Scrapyd.

## Complete Data Flow

```text
scheduler.py
      ↓
Scrapyd
      ↓
DealWallet Scrapy Project
      ↓
Myntra / Soulflower / WiseLife Spider
      ↓
Scraped Products
      ↓
Scrapy Pipeline
      ↓
price_history.py
      ↓
Field Validation
      ↓
Affiliate URL Generation
      ↓
Product Lookup
      ↓
Price Comparison
      ↓
Product Insert / Update
      ↓
Price History Snapshot
      ↓
PostgreSQL Database
```

## Useful Commands

Check Scrapy version:

```powershell
scrapy version
```

List spiders:

```powershell
scrapy list
```

Run Myntra:

```powershell
scrapy crawl myntra
```

Run Soulflower:

```powershell
scrapy crawl soul_flower
```

Run WiseLife:

```powershell
scrapy crawl wiselife
```

Start Scrapyd:

```powershell
scrapyd
```

Deploy project:

```powershell
scrapyd-deploy local
```

Check Scrapyd spiders:

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:6800/listspiders.json?project=dealwallet_scraper"
```

## Current Implementation Status

| Component | Status |
| ------------------------------ | ----------------------- |
| Scrapy project | Completed |
| Myntra scraper | Completed |
| Soulflower scraper | Completed |
| WiseLife scraper | Completed |
| Product field extraction | Completed |
| Scrapy pipeline | Completed |
| Field validation | Completed |
| Affiliate URL generation | Completed |
| PostgreSQL configuration | Prepared |
| Price history table | Created |
| Price comparison logic | Implemented |
| Product insert/update logic | Implemented |
| Price history snapshot logic | Implemented |
| Scrapyd setup | Completed |
| Scrapyd deployment | Completed |
| Python scheduler | Completed |
| Database insertion | Test mode / Temporarily disabled |

## Notes

* Keep database credentials in `.env`.
* Do not commit `.env` to Git.
* Scrapyd must be running before the scheduler can schedule jobs.
* Deploy the latest project version to Scrapyd after code changes.
* `price_history.py` handles validation, affiliate URL generation, product lookup, price comparison, product insert/update, and price history snapshot processing.
* The existing `products.id` UUID remains stable for an existing product.
* Each `price_history` record has a separate UUID.
* Only price changes create a new price history record.
