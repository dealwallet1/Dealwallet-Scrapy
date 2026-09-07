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
│   │   └── soul_flower.py
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
* `timestamp`

Crawl4AI is used for browser-based extraction.

### Soulflower

The Soulflower spider uses Playwright-based browser scraping and currently targets the configured Essential Oils category.

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
Dealwallet_supabase_url=YOUR_SUPABASE_URL
Dealwallet_supabase_key=YOUR_SUPABASE_KEY
Dealwallet_SUPABASE_SCHEMA=public
Dealwallet_SUPABASE_TABLE=products

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
Database Payload
```

The pipeline is enabled in `settings.py`:

```python
ITEM_PIPELINES = {
    "dealwallet_scraper.pipelines.DealwalletScraperPipeline": 300,
}
```

## price_history.py

`price_history.py` acts as the middle layer between the Scrapy pipeline and the database.

Current responsibilities:

1. Receive scraped product data.
2. Validate required fields.
3. Prepare the database payload.
4. Generate an affiliate URL.
5. Prepare the product for database insertion.

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
timestamp
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

## Database Status

Supabase integration has been prepared using the Supabase REST API.

**Current status: TEST MODE**

The current flow is:

```text
Scraped data
    ↓
Validation
    ↓
Affiliate URL generation
    ↓
Database payload displayed
    ↓
Database insertion disabled
```

Example:

```text
PRODUCT READY FOR DATABASE
name: Men Printed T-shirt
price: 354
currency: ₹
original_price: 1999
discount: 82
affiliate_url: https://linksredirect.com/...
...
DATABASE SEND DISABLED - TEST MODE
```

Database insertion will be enabled after the payload and database table integration are verified.

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
Myntra / Soulflower Spider
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
Supabase Database
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

| Component                      | Status                  |
| ------------------------------ | ----------------------- |
| Scrapy project                 | Completed               |
| Myntra scraper                 | Completed               |
| Soulflower scraper             | Completed               |
| Product field extraction       | Completed               |
| Scrapy pipeline                | Completed               |
| Field validation               | Completed               |
| Affiliate URL generation       | Completed               |
| Scrapyd setup                  | Completed               |
| Scrapyd deployment             | Completed               |
| Python scheduler               | Completed               |
| Supabase configuration         | Prepared                |
| Database insertion             | Test mode / Not enabled |
| Price history/comparison logic | Not implemented yet     |

## Notes

* Keep database credentials in `.env`.
* Do not commit `.env` to Git.
* Scrapyd must be running before the scheduler can schedule jobs.
* Deploy the latest project version to Scrapyd after code changes.
* `price_history.py` currently handles validation, affiliate URL generation, and database preparation.
* Price comparison/history logic has not been implemented yet.

```
```
