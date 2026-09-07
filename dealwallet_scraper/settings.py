
# Scrapy settings for dealwallet_scraper project

import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsProactorEventLoopPolicy()
    )


BOT_NAME = "dealwallet_scraper"

SPIDER_MODULES = ["dealwallet_scraper.spiders"]
NEWSPIDER_MODULE = "dealwallet_scraper.spiders"

ADDONS = {}


# Crawl responsibly by identifying yourself as a scraper
USER_AGENT = "dealwallet_scraper"


# Obey robots.txt rules
ROBOTSTXT_OBEY = True


# Concurrency and throttling settings
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 1


# Feed export encoding
FEED_EXPORT_ENCODING = "utf-8"


# Item pipelines
ITEM_PIPELINES = {
    "dealwallet_scraper.pipelines.DealwalletScraperPipeline": 300,
}


# Use asyncio reactor
TWISTED_REACTOR = (
    "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
)

