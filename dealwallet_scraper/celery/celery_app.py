import os
from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://dealwallet-redis:6379/0",
)

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

celery_inst.conf.beat_schedule = {
    # 1. Everyday 1:00 AM IST - Nike standalone script
    "daily-nike-scraper-1am": {
        "task": "tasks.run_scraper",
        "schedule": crontab(hour=1, minute=0),
        "args": ("nike_scrapy.py",),
    },

    # 2. Everyday 2:00 AM IST - Myntra standalone script
    "daily-myntra-scraper-2am": {
        "task": "tasks.run_scraper",
        "schedule": crontab(hour=2, minute=0),
        "args": ("myntra_scrapy.py",),
    },

    # 3. Recurring batch every 5 hours (Scrapyd batch)
    "recurring-batch-every-5-hours": {
        "task": "dealwallet.run_scraper_batch",
        "schedule": crontab(minute=0, hour="*/5"),
    },
}