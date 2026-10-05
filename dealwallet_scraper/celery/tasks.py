import logging
import os
import subprocess
import sys
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


def get_active_spiders():
    """Returns a set of spider names that are currently running or pending in Scrapyd."""
    url = f"{SCRAPYD_URL}/listjobs.json"
    try:
        response = requests.get(url, params={"project": SCRAPYD_PROJECT}, timeout=30)
        response.raise_for_status()
        data = response.json()
        running = {job.get("spider") for job in data.get("running", []) if job.get("spider")}
        pending = {job.get("spider") for job in data.get("pending", []) if job.get("spider")}
        return running.union(pending)
    except Exception as exc:
        logger.exception("Failed to fetch active jobs from Scrapyd: %s", exc)
        return set()


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


@celery_inst.task(name="tasks.run_scraper")
def run_scraper(script_name: str):
    """Executes standalone scrapers (e.g. nike_scrapy.py, myntra_scrapy.py).
    Raises an exception to mark Celery task as failed if the script or Supabase upload fails.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, script_name)

    if not os.path.exists(script_path):
        err_msg = f"Script not found: {script_path}"
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)

    logger.info("Starting standalone scraper execution: %s", script_path)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    process = subprocess.Popen(
        [sys.executable, "-u", script_path],
        cwd=base_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )

    output_lines = []
    for line in iter(process.stdout.readline, ""):
        if line:
            print(line, end="", flush=True)
            output_lines.append(line)

    process.stdout.close()
    return_code = process.wait()

    if return_code != 0:
        logger.error("Scraper script %s failed with exit code %s", script_name, return_code)
        raise RuntimeError(f"Script {script_name} execution failed with exit code {return_code}")

    logger.info("Scraper script completed successfully: %s", script_name)
    return {
        "status": "success",
        "script": script_name,
        "logs": "".join(output_lines[-50:]),
    }


@celery_inst.task(name="dealwallet.run_single_spider")
def run_single_spider(spider_name: str):
    """Schedule a specific single spider on Scrapyd."""
    logger.info("Dispatching individual spider: %s", spider_name)
    try:
        job_id = schedule_spider(spider_name)
        return {"status": "scheduled", "spider": spider_name, "job_id": job_id}
    except Exception as exc:
        logger.exception("Failed to schedule individual spider %s: %s", spider_name, exc)
        raise


@celery_inst.task(name="dealwallet.run_scraper_batch")
def run_scraper_batch():
    logger.info("=" * 60)
    logger.info("DealWallet Celery scraper batch started")
    logger.info("Scrapyd URL: %s", SCRAPYD_URL)
    logger.info("Scrapyd project: %s", SCRAPYD_PROJECT)
    logger.info("Total spiders configured: %s", len(SCRAPYD_SPIDERS))
    logger.info("=" * 60)

    active_spiders = get_active_spiders()
    logger.info("Currently active/pending spiders: %s", active_spiders)

    scheduled_jobs = []
    skipped_spiders = []
    failed_spiders = []

    for spider_name in SCRAPYD_SPIDERS:
        # Per acceptance criteria: Skip ONLY spiders that are already active/pending
        if spider_name in active_spiders:
            logger.warning("Spider %s is already active or pending. Skipping.", spider_name)
            skipped_spiders.append(spider_name)
            continue

        try:
            job_id = schedule_spider(spider_name)
            if job_id:
                scheduled_jobs.append({"spider": spider_name, "job_id": job_id})
        except Exception as exc:
            logger.exception("Failed to schedule spider %s: %s", spider_name, exc)
            failed_spiders.append({"spider": spider_name, "error": str(exc)})

    logger.info(
        "Batch run summary: Scheduled=%s, Skipped=%s, Failed=%s",
        len(scheduled_jobs),
        len(skipped_spiders),
        len(failed_spiders),
    )

    if failed_spiders:
        raise RuntimeError(f"Batch completed with failures: {failed_spiders}")

    return {
        "status": "completed",
        "scheduled_count": len(scheduled_jobs),
        "skipped_count": len(skipped_spiders),
        "scheduled": scheduled_jobs,
        "skipped": skipped_spiders,
    }