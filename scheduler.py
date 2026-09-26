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
 
# LOCAL: 
#   http://127.0.0.1:6800 
# 
# KUBERNETES: 
#   http://dealwallet-scrapyd:6800 
# 
# Set SCRAPYD_URL in the environment for Kubernetes. 
# Local automatically uses 127.0.0.1:6800. 
 
SCRAPYD_URL = os.getenv( 
    "SCRAPYD_URL", 
    "http://127.0.0.1:6800", 
).rstrip("/") 
 
 
SCRAPYD_PROJECT = os.getenv( 
    "SCRAPYD_PROJECT", 
    "dealwallet_scraper", 
) 
 
 
SCRAPYD_SPIDERS = [ 
    # "myntra_products",
     # "myntra_cat", 
   # "soul_flower", 
   #  "wiselife", 
   #  "yardley",
   #  "zivame" ,
   #  "zigly",
   #  "abhishti",
   #  "zingavita",
   #  "amydus",
   #  "buywow",
   #  "flipkart",
    "world_of_asaya",
    "beneude",
    "beyours",
    "flipkart_price",
    
] 
 
 
# ============================================================ 
# INTERVAL 
# ============================================================ 
 
# Default = 20 minutes 
# The next batch starts only AFTER the previous batch 
# has completely finished, then waits this interval. 
 
SCHEDULE_INTERVAL_SECONDS = int( 
    os.getenv( 
        "SCHEDULE_INTERVAL_SECONDS", 
        str(20 * 60), 
    ) 
) 
 
 
# ============================================================ 
# SCRAPYD CHECK INTERVAL 
# ============================================================ 
 
# Check every 30 seconds while jobs are running. 
 
CHECK_INTERVAL = int( 
    os.getenv( 
        "SCRAPYD_CHECK_INTERVAL", 
        "30", 
    ) 
) 
 
 
# ============================================================ 
# SCRAPYD URLS 
# ============================================================ 
 
# If somebody provides: 
# 
# http://127.0.0.1:6800/schedule.json 
# 
# or: 
# 
# http://dealwallet-scrapyd:6800/schedule.json 
# 
# remove /schedule.json and build the URLs ourselves. 
 
if SCRAPYD_URL.endswith("/schedule.json"): 
    SCRAPYD_BASE_URL = SCRAPYD_URL[ 
        :-len("/schedule.json") 
    ] 
else: 
    SCRAPYD_BASE_URL = SCRAPYD_URL 
 
 
SCHEDULE_URL = ( 
    f"{SCRAPYD_BASE_URL}/schedule.json" 
) 
 
 
LISTJOBS_URL = ( 
    f"{SCRAPYD_BASE_URL}/listjobs.json" 
) 
 
 
# ============================================================ 
# GET ACTIVE JOBS 
# ============================================================ 
 
def get_active_jobs(): 
    """ 
    Return running and pending jobs from Scrapyd. 
 
    Returns: 
        (running, pending) 
 
    If Scrapyd cannot be reached: 
        (None, None) 
    """ 
 
    try: 
 
        response = requests.get( 
            LISTJOBS_URL, 
            params={ 
                "project": SCRAPYD_PROJECT, 
            }, 
            timeout=30, 
        ) 
 
        response.raise_for_status() 
 
        data = response.json() 
 
        running = data.get( 
            "running", 
            [], 
        ) 
 
        pending = data.get( 
            "pending", 
            [], 
        ) 
 
        return running, pending 
 
    except Exception as exc: 
 
        logger.error( 
            "Failed to check Scrapyd jobs: %s", 
            exc, 
        ) 
 
        # IMPORTANT: 
        # Never schedule new jobs if Scrapyd status 
        # cannot be verified. 
        return None, None 
 
 
# ============================================================ 
# SCHEDULE ONE SPIDER 
# ============================================================ 
 
def schedule_spider(spider_name): 
    """ 
    Submit one spider to Scrapyd. 
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
        "Scrapyd schedule URL: %s", 
        SCHEDULE_URL, 
    ) 
 
    try: 
 
        response = requests.post( 
            SCHEDULE_URL, 
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
 
                job_id = result.get( 
                    "jobid" 
                ) 
 
                logger.info( 
                    "Spider scheduled successfully. " 
                    "Spider=%s Job ID=%s", 
                    spider_name, 
                    job_id, 
                ) 
 
                return job_id 
 
            logger.error( 
                "Scrapyd rejected spider %s: %s", 
                spider_name, 
                result, 
            ) 
 
            return None 
 
        logger.error( 
            "Unexpected Scrapyd response for %s: %s", 
            spider_name, 
            result, 
        ) 
 
        return None 
 
    except requests.exceptions.RequestException as exc: 
 
        logger.exception( 
            "Failed to schedule spider %s: %s", 
            spider_name, 
            exc, 
        ) 
 
        return None 
 
 
# ============================================================ 
# WAIT UNTIL SCRAPYD IS EMPTY 
# ============================================================ 
 
def wait_until_no_active_jobs(): 
    """ 
    Wait until there are no running or pending jobs. 
 
    This prevents duplicate batches. 
    """ 
 
    while True: 
 
        running, pending = get_active_jobs() 
 
        # Scrapyd cannot be reached. 
        if running is None or pending is None: 
 
            logger.warning( 
                "Unable to verify Scrapyd status. " 
                "Will retry without scheduling." 
            ) 
 
            time.sleep( 
                CHECK_INTERVAL 
            ) 
 
            continue 
 
        logger.info( 
            "Scrapyd status: running=%s pending=%s", 
            len(running), 
            len(pending), 
        ) 
 
        # Existing jobs found. 
        if running or pending: 
 
            logger.info( 
                "Existing jobs detected. " 
                "Waiting before starting a new batch..." 
            ) 
 
            time.sleep( 
                CHECK_INTERVAL 
            ) 
 
            continue 
 
        logger.info( 
            "No running or pending jobs found." 
        ) 
 
        return 
 
 
# ============================================================ 
# START NEW BATCH 
# ============================================================ 
 
def start_new_batch(): 
    """ 
    Submit all three spiders. 
 
    Scrapyd max_proc=3 allows all three to run 
    simultaneously. 
    """ 
 
    logger.info("=" * 60) 
    logger.info("Starting new spider batch") 
    logger.info("=" * 60) 
 
    scheduled_jobs = [] 
 
    for spider_name in SCRAPYD_SPIDERS: 
 
        job_id = schedule_spider( 
            spider_name 
        ) 
 
        if job_id: 
 
            scheduled_jobs.append( 
                job_id 
            ) 
 
    logger.info( 
        "Batch submitted. Jobs scheduled: %s/%s", 
        len(scheduled_jobs), 
        len(SCRAPYD_SPIDERS), 
    ) 
 
    return scheduled_jobs 
 
 
# ============================================================ 
# WAIT FOR BATCH TO FINISH 
# ============================================================ 
 
def wait_for_batch_to_finish(): 
    """ 
    Wait until Scrapyd has no running or pending jobs. 
    """ 
 
    logger.info( 
        "Waiting for current batch to finish..." 
    ) 
 
    while True: 
 
        running, pending = get_active_jobs() 
 
        if running is None or pending is None: 
 
            logger.warning( 
                "Unable to check batch status. " 
                "Retrying..." 
            ) 
 
            time.sleep( 
                CHECK_INTERVAL 
            ) 
 
            continue 
 
        logger.info( 
            "Current batch status: " 
            "running=%s pending=%s", 
            len(running), 
            len(pending), 
        ) 
 
        # All jobs finished. 
        if not running and not pending: 
 
            logger.info( 
                "All spiders completed." 
            ) 
 
            return 
 
        time.sleep( 
            CHECK_INTERVAL 
        ) 
 
 
# ============================================================ 
# MAIN 
# ============================================================ 
 
def main(): 
 
    logger.info("=" * 60) 
    logger.info("DealWallet scheduler started") 
    logger.info("=" * 60) 
 
    logger.info( 
        "Scrapyd base URL: %s", 
        SCRAPYD_BASE_URL, 
    ) 
 
    logger.info( 
        "Scrapyd schedule URL: %s", 
        SCHEDULE_URL, 
    ) 
 
    logger.info( 
        "Scrapyd list jobs URL: %s", 
        LISTJOBS_URL, 
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
        "Interval: %s seconds (%.1f minutes)", 
        SCHEDULE_INTERVAL_SECONDS, 
        SCHEDULE_INTERVAL_SECONDS / 60, 
    ) 
 
    logger.info( 
        "Scrapyd check interval: %s seconds", 
        CHECK_INTERVAL, 
    ) 
 
    # ======================================================== 
    # CONTINUOUS LOOP 
    # ======================================================== 
 
    while True: 
 
        # ---------------------------------------------------- 
        # STEP 1 
        # Check for old/running/pending jobs. 
        # ---------------------------------------------------- 
 
        logger.info( 
            "Checking Scrapyd for existing jobs..." 
        ) 
 
        wait_until_no_active_jobs() 
 
        # ---------------------------------------------------- 
        # STEP 2 
        # Start exactly one batch. 
        # ---------------------------------------------------- 
 
        scheduled_jobs = start_new_batch() 
 
        # ---------------------------------------------------- 
        # STEP 3 
        # Wait for the batch. 
        # ---------------------------------------------------- 
 
        if scheduled_jobs: 
 
            wait_for_batch_to_finish() 
 
        else: 
 
            logger.error( 
                "No spiders were scheduled. " 
                "Skipping batch wait." 
            ) 
 
        # ---------------------------------------------------- 
        # STEP 4 
        # Wait before next batch. 
        # ---------------------------------------------------- 
 
        logger.info( 
            "Waiting %s seconds before next batch...", 
            SCHEDULE_INTERVAL_SECONDS, 
        ) 
 
        try: 
 
            time.sleep( 
                SCHEDULE_INTERVAL_SECONDS 
            ) 
 
        except KeyboardInterrupt: 
 
            logger.info( 
                "Scheduler stopped by user." 
            ) 
 
            sys.exit(0) 
 
 
# ============================================================ 
# ENTRY POINT 
# ============================================================ 
 
if __name__ == "__main__": 
    main()
