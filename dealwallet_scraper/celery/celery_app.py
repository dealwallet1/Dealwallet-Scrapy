import os 
import sys 
import subprocess 
 
from dotenv import load_dotenv 
from celery import Celery 
from celery.schedules import crontab 
 
 
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..")) 
 
load_dotenv(os.path.join(PROJECT_ROOT, ".env")) 
 
 
REDIS_URL = os.getenv("REDIS_URL") 
 
if not REDIS_URL: 
    raise RuntimeError("REDIS_URL environment variable is not set.") 
 
 
celery_inst = Celery( 
    "dealwallet_scraper", 
    broker=REDIS_URL, 
    backend=REDIS_URL, 
    include=[ 
        "dealwallet_scraper.celery.tasks", 
    ], 
) 
 
 
celery_inst.conf.update( 
    timezone="Asia/Kolkata", 
    enable_utc=False, 
    broker_connection_retry_on_startup=True, 
    worker_prefetch_multiplier=1, 
    task_acks_late=True, 
    task_track_started=True, 
    result_backend_transport_options={ 
        "retry_on_timeout": True, 
    }, 
) 
 
 
@celery_inst.task(name="tasks.run_scraper") 
def run_scraper_task(script_name="myntra_scrapy.py"): 
    cwd_dir = os.path.dirname(os.path.abspath(__file__)) 
    target_path = os.path.join(cwd_dir, script_name) 
 
    if not os.path.exists(target_path): 
        raise FileNotFoundError( 
            f"Scraper file not found: {target_path}" 
        ) 
 
    env = os.environ.copy() 
    env["PYTHONIOENCODING"] = "utf-8" 
    env["PYTHONUTF8"] = "1" 
 
    process = subprocess.Popen( 
        [sys.executable, "-u", target_path], 
        cwd=cwd_dir, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True, 
        encoding="utf-8", 
        errors="replace", 
        env=env, 
        bufsize=1, 
    ) 
 
    for line in iter(process.stdout.readline, ""): 
        if line: 
            print(line, end="", flush=True) 
 
    process.stdout.close() 
 
    return_code = process.wait() 
 
    if return_code != 0: 
        raise RuntimeError( 
            f"{script_name} failed with exit code {return_code}" 
        ) 
 
    return f"{script_name} completed successfully." 
 
 
celery_inst.conf.beat_schedule = { 
    "daily-nike-scraper-1am": { 
        "task": "dealwallet.run_single_spider", 
        "schedule": crontab( 
            hour=1, 
            minute=0, 
        ), 
        "args": ("nike",), 
    }, 
 
    "daily-myntra-scraper-2am": { 
        "task": "dealwallet.run_single_spider", 
        "schedule": crontab( 
            hour=2, 
            minute=0, 
        ), 
        "args": ("myntra",), 
    }, 
 
    "recurring-batch-5h": { 
        "task": "dealwallet.run_scraper_batch", 
        "schedule": crontab( 
            minute=30, 
            hour="1,6,11,16,21", 
        ), 
    }, 
 
    "recurring-batch-6h": { 
        "task": "dealwallet.run_scraper_batch", 
        "schedule": crontab( 
            minute=0, 
            hour="0,6,12,18", 
        ), 
    }, 
}