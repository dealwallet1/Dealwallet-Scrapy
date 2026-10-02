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
    raise RuntimeError(
        "REDIS_URL environment variable is not set. "
        "Please set REDIS_URL before starting Celery."
    )

celery_inst = Celery("scraper_tasks", broker=REDIS_URL, backend=REDIS_URL)

celery_inst.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    result_backend_transport_options={"retry_on_timeout": True}
)

@celery_inst.task(name="tasks.run_scraper")
def run_scraper_task(script_name: str = "myntra_scrapy.py"):
    cwd_dir = os.path.abspath(os.path.dirname(__file__))
    target_path = os.path.join(cwd_dir, script_name)

    if not os.path.exists(target_path):
        return f"[ERROR] File not found: {target_path}"

    print(f"[CELERY] Launching scraper: {target_path}")
    sub_env = os.environ.copy()
    sub_env["PYTHONIOENCODING"] = "utf-8"
    sub_env["PYTHONUTF8"] = "1"

    process = subprocess.Popen(
        [sys.executable, "-u", target_path],
        cwd=cwd_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=sub_env,
        bufsize=1
    )

    for line in iter(process.stdout.readline, ''):
        if line:
            print(line, end='', flush=True)

    process.stdout.close()
    return_code = process.wait()

    if return_code == 0:
        return f"[SUCCESS] {script_name} completed."
    else:
        return f"[FAILED] {script_name} exit code {return_code}."

celery_inst.conf.beat_schedule = {
    "demo_myntra_cron": {
        "task": "tasks.run_scraper",
        "schedule": crontab(minute="*/15"),
        "args": ("myntra_scrapy.py",)
    },

    "demo_nike_cron": {
        "task": "tasks.run_scraper",
        "schedule": crontab(minute="*/25"),
        "args": ("nike_scrapy.py",)
    },
}

