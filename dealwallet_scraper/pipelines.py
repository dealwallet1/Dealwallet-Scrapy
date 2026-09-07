
from itemadapter import ItemAdapter

from dealwallet_scraper.price_history import send_to_database


class DealwalletScraperPipeline:

    def process_item(self, item, spider):

        product = ItemAdapter(item).asdict()

        send_to_database(product)

        return item

