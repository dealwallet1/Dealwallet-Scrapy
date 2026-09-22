import json
from pathlib import Path

from itemadapter import ItemAdapter

from dealwallet_scraper.price_history import (
    clean_product_url,
    generate_affiliate_url,
    send_to_database,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

AFFILIATE_JSON_FILE = (
    PROJECT_ROOT / "scrape_affiliate_urls1.json"
)


class DealwalletScraperPipeline:

    def open_spider(self, spider):
        self.products = []

    def process_item(self, item, spider):

        product = ItemAdapter(item).asdict()

        # --------------------------------------------------------
        # CLEAN PRODUCT URL
        # --------------------------------------------------------

        original_product_link = product.get("product_link")

        cleaned_product_link = clean_product_url(
            original_product_link
        )

        product["product_link"] = cleaned_product_link

        # --------------------------------------------------------
        # CUELINKS AFFILIATE URL
        # --------------------------------------------------------

        affiliate_url = generate_affiliate_url(
            original_product_link
        )

        product["affiliate_url"] = affiliate_url

        # Add every product to JSON,
        # whether Cuelinks is affiliated or not.
        self.products.append(
            product.copy()
        )

        # --------------------------------------------------------
        # DB INSERTION
        # --------------------------------------------------------

        send_to_database(product)

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
