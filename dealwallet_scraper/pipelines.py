import json
from pathlib import Path

from itemadapter import ItemAdapter

from dealwallet_scraper.price_history import (
    generate_affiliate_url,
    send_to_database,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

AFFILIATE_JSON_FILE = (
    PROJECT_ROOT / "scrape_affiliate_urls.json"
)


class DealwalletScraperPipeline:

    def open_spider(self, spider):
        self.products = []

    def process_item(self, item, spider):

        product = ItemAdapter(item).asdict()

        affiliate_url = generate_affiliate_url(
            product.get("product_link")
        )

        if affiliate_url:

            product["affiliate_url"] = affiliate_url

            self.products.append(
                product.copy()
            )

            # DB insertion
            # send_to_database(product)

        return item

    def close_spider(self, spider):

        with open(
            AFFILIATE_JSON_FILE,
            "w",
            encoding="utf-8",
        ) as json_file:

            json.dump(
                self.products,
                json_file,
                ensure_ascii=False,
                indent=4,
            )