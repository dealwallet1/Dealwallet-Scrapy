import os
import time
import requests
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SCRAPYD_URL = os.getenv(
    "SCRAPYD_URL",
    "http://127.0.0.1:6800"
).rstrip("/")

SCRAPYD_PROJECT = os.getenv(
    "SCRAPYD_PROJECT",
    "dealwallet_scraper"
)

SPIDERS = [
    "myntra",
    "soul_flower",
    "wiselife",
]

SCHEDULE_INTERVAL_SECONDS = int(
    os.getenv("SCHEDULE_INTERVAL_SECONDS", "1200")
)

CHECK_INTERVAL_SECONDS = int(
    os.getenv("CHECK_INTERVAL_SECONDS", "30")
)


def get_active_jobs():
    """Return currently running and pending jobs."""

    try:
        response = requests.get(
            f"{SCRAPYD_URL}/listjobs.json",
            params={"project": SCRAPYD_PROJECT},
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        running = data.get("running", [])
        pending = data.get("pending", [])

        logging.info(
            "Scrapyd status: running=%d pending=%d",
            len(running),
            len(pending)
        )

        return running, pending

    except Exception as exc:
        logging.error("Failed to check Scrapyd jobs: %s", exc)

        # Do NOT start jobs if we cannot determine the current state.
        return None, None


def schedule_spider(spider):
    """Schedule one spider."""

    try:
        response = requests.post(
            f"{SCRAPYD_URL}/schedule.json",
            data={
                "project": SCRAPYD_PROJECT,
                "spider": spider,
            },
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if data.get("status") == "ok":
            job_id = data.get("jobid")
            logging.info(
                "Scheduled spider=%s jobid=%s",
                spider,
                job_id
            )
            return job_id

        logging.error(
            "Failed to schedule spider=%s response=%s",
            spider,
            data
        )

    except Exception as exc:
        logging.error(
            "Exception scheduling spider=%s: %s",
            spider,
            exc
        )

    return None


def wait_until_no_active_jobs():
    """Wait until there are no running/pending jobs."""

    while True:
        running, pending = get_active_jobs()

        if running is None:
            logging.warning(
                "Cannot determine active jobs. Retrying in %d seconds.",
                CHECK_INTERVAL_SECONDS
            )
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        if not running and not pending:
            logging.info("No active jobs found.")
            return

        logging.info(
            "Existing jobs detected: running=%d pending=%d. "
            "Waiting before starting a new batch.",
            len(running),
            len(pending)
        )

        time.sleep(CHECK_INTERVAL_SECONDS)


def start_new_batch():
    """Start exactly one batch containing all spiders."""

    logging.info(
        "Starting new batch: %s",
        ", ".join(SPIDERS)
    )

    job_ids = []

    for spider in SPIDERS:
        job_id = schedule_spider(spider)

        if job_id:
            job_ids.append(job_id)

    logging.info(
        "Batch submitted: %d/%d spiders",
        len(job_ids),
        len(SPIDERS)
    )

    return job_ids


def wait_for_batch_to_finish():
    """Wait until all Scrapyd jobs finish."""

    while True:
        running, pending = get_active_jobs()

        if running is None:
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        if not running and not pending:
            logging.info("All jobs finished.")
            return

        logging.info(
            "Batch still active: running=%d pending=%d",
            len(running),
            len(pending)
        )

        time.sleep(CHECK_INTERVAL_SECONDS)


def main():

    logging.info("========================================")
    logging.info("DealWallet scheduler started")
    logging.info("Scrapyd URL: %s", SCRAPYD_URL)
    logging.info("Project: %s", SCRAPYD_PROJECT)
    logging.info("Spiders: %s", ", ".join(SPIDERS))
    logging.info(
        "Interval: %d seconds (%.1f minutes)",
        SCHEDULE_INTERVAL_SECONDS,
        SCHEDULE_INTERVAL_SECONDS / 60
    )
    logging.info("========================================")

    while True:

        # Never start a new batch if another batch is active.
        wait_until_no_active_jobs()

        # Start exactly one batch.
        start_new_batch()

        # Wait until all jobs from the batch finish.
        wait_for_batch_to_finish()

        # Wait before the next batch.
        logging.info(
            "All spiders completed. "
            "Waiting %d seconds before next batch.",
            SCHEDULE_INTERVAL_SECONDS
        )

        time.sleep(SCHEDULE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
