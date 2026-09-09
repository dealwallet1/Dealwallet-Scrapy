import os
import sys
import time
import logging
import requests
from dotenv import load_dotenv


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

SCRAPYD_URL = os.getenv(
    "SCRAPYD_URL",
    "http://127.0.0.1:6800/schedule.json",
)

SCRAPYD_PROJECT = os.getenv(
    "SCRAPYD_PROJECT",
    "dealwallet_scraper",
)

SCRAPYD_SPIDERS = [
    "myntra",
    "soul_flower",
    "wiselife"
]


# ------------------------------------------------------------
# Schedule interval
#
# Default = 10 minutes
#
# 10 minutes = 600 seconds
# ------------------------------------------------------------

SCHEDULE_INTERVAL_SECONDS = int(
    os.getenv(
        "SCHEDULE_INTERVAL_SECONDS",
        str(20 * 60),
    )
)


# ============================================================
# RUN SPIDER
# ============================================================

def run_spider(spider_name):
    """
    Send a request to Scrapyd to start the specified spider.
    """

    payload = {
        "project": SCRAPYD_PROJECT,
        "spider": spider_name,
    }

    logger.info(
        "Scheduling spider: project=%s spider=%s",
        SCRAPYD_PROJECT,
        spider_name,
    )

    logger.info(
        "Scrapyd URL: %s",
        SCRAPYD_URL,
    )

    try:
        response = requests.post(
            SCRAPYD_URL,
            data=payload,
            timeout=30,
        )

        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            result = response.text

        logger.info(
            "Scrapyd response: %s",
            result,
        )

        if isinstance(result, dict):

            if result.get("status") == "ok":
                logger.info(
                    "Spider scheduled successfully. Job ID: %s",
                    result.get("jobid"),
                )
                return True

            logger.error(
                "Scrapyd rejected the spider request: %s",
                result,
            )
            return False

        return True

    except requests.exceptions.RequestException as exc:

        logger.exception(
            "Failed to connect to Scrapyd: %s",
            exc,
        )

        return False


# ============================================================
# RUN ALL SPIDERS
# ============================================================

def run_all_spiders():
    """
    Schedule all configured spiders.
    """

    for spider_name in SCRAPYD_SPIDERS:
        run_spider(spider_name)


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("=" * 60)
    logger.info("DealWallet scheduler started")
    logger.info("=" * 60)

    logger.info(
        "Scrapyd URL: %s",
        SCRAPYD_URL,
    )

    logger.info(
        "Project: %s",
        SCRAPYD_PROJECT,
    )

    logger.info(
        "Spiders: %s",
        ", ".join(SCRAPYD_SPIDERS),
    )

    logger.info(
        "Interval: %s seconds (%s minutes)",
        SCHEDULE_INTERVAL_SECONDS,
        SCHEDULE_INTERVAL_SECONDS / 60,
    )

    # --------------------------------------------------------
    # Run all spiders once immediately
    # --------------------------------------------------------

    run_all_spiders()

    # --------------------------------------------------------
    # Continue running forever
    # --------------------------------------------------------

    while True:

        logger.info(
            "Waiting %s seconds before next spider run...",
            SCHEDULE_INTERVAL_SECONDS,
        )

        try:
            time.sleep(SCHEDULE_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
            sys.exit(0)

        # ----------------------------------------------------
        # Run all spiders again
        # ----------------------------------------------------

        run_all_spiders()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()