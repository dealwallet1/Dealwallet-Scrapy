import logging
import os
import requests
from .celery_app import celery_inst

logger = logging.getLogger(__name__)

SCRAPYD_URL = os.getenv(
    "SCRAPYD_URL",
    "http://dealwallet-scrapyd:4079",
).rstrip("/")

SCRAPYD_PROJECT = os.getenv(
    "SCRAPYD_PROJECT",
    "dealwallet_scraper",
)

SCRAPYD_SPIDERS = [
    "soul_flower",
    "wiselife",
    "yardley",
    "zivame",
    "zigly",
    "abhishti",
    "zingavita",
    "amydus",
    "buywow",
    "world_of_asaya",
    "beneude",
    "beyours",
    "flipkart_price",
    "boveee",
    "wrapcart",
    "woodland",
    "woodland_price_tracker",
    "snapdeal_price_tracker",
]


def get_active_jobs():
    url = f"{SCRAPYD_URL}/listjobs.json"
    response = requests.get(url, params={"project": SCRAPYD_PROJECT}, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("running", []), data.get("pending", [])


def schedule_spider(spider_name):
    url = f"{SCRAPYD_URL}/schedule.json"
    logger.info("Scheduling spider: %s", spider_name)
    response = requests.post(
        url,
        data={
            "project": SCRAPYD_PROJECT,
            "spider": spider_name,
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("status") != "ok":
        raise RuntimeError(f"Scrapyd rejected spider {spider_name}: {result}")
    job_id = result.get("jobid")
    logger.info("Spider scheduled successfully: spider=%s job_id=%s", spider_name, job_id)
    return job_id


@celery_inst.task(name="dealwallet.run_single_spider")
def run_single_spider(spider_name: str):
    """Schedule a specific single spider on Scrapyd."""
    logger.info("Dispatching individual spider: %s", spider_name)
    try:
        job_id = schedule_spider(spider_name)
        return {"status": "scheduled", "spider": spider_name, "job_id": job_id}
    except Exception as exc:
        logger.exception("Failed to schedule individual spider %s: %s", spider_name, exc)
        return {"status": "error", "spider": spider_name, "error": str(exc)}


@celery_inst.task(name="dealwallet.run_scraper_batch")
def run_scraper_batch():
    logger.info("=" * 60)
    logger.info("DealWallet Celery scraper batch started")
    logger.info("Scrapyd URL: %s", SCRAPYD_URL)
    logger.info("Scrapyd project: %s", SCRAPYD_PROJECT)
    logger.info("Total spiders: %s", len(SCRAPYD_SPIDERS))
    logger.info("=" * 60)

    try:
        running, pending = get_active_jobs()
    except Exception as exc:
        logger.exception("Unable to check Scrapyd jobs: %s", exc)
        raise

    logger.info("Scrapyd status: running=%s pending=%s", len(running), len(pending))

    if running or pending:
        logger.warning("Existing Scrapyd jobs detected. Skipping this batch.")
        return {
            "status": "skipped",
            "reason": "active_jobs",
            "running": len(running),
            "pending": len(pending),
        }

    scheduled_jobs = []
    failed_spiders = []

    for spider_name in SCRAPYD_SPIDERS:
        try:
            job_id = schedule_spider(spider_name)
            if job_id:
                scheduled_jobs.append({"spider": spider_name, "job_id": job_id})
        except Exception as exc:
            logger.exception("Failed to schedule spider %s: %s", spider_name, exc)
            failed_spiders.append({"spider": spider_name, "error": str(exc)})

    logger.info("Spider batch submitted: %s/%s", len(scheduled_jobs), len(SCRAPYD_SPIDERS))
    if failed_spiders:
        logger.warning("Failed spiders: %s", len(failed_spiders))
    logger.info("DealWallet Celery scraper batch completed")

    return {
        "status": "scheduled",
        "scheduled_count": len(scheduled_jobs),
        "total_count": len(SCRAPYD_SPIDERS),
        "jobs": scheduled_jobs,
        "failed": failed_spiders,
    }