import os
from celery import Celery
from celery.schedules import crontab

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
    result_backend_transport_options={
        "retry_on_timeout": True,
    },
    task_track_started=True,
)

celery_inst.conf.beat_schedule = {
    # 1. Everyday 1:00 AM IST - Nike scraper
    "daily-nike-scraper-1am": {
        "task": "dealwallet.run_single_spider",
        "schedule": crontab(hour=1, minute=0),
        "args": ("nike",),  # If your Scrapyd spider name is nike_scrapy, replace 'nike' with that exact name
    },

    # 2. Everyday 2:00 AM IST - Myntra scraper
    "daily-myntra-scraper-2am": {
        "task": "dealwallet.run_single_spider",
        "schedule": crontab(hour=2, minute=0),
        "args": ("myntra",),  # If your Scrapyd spider name is myntra_scrapy, replace 'myntra' with that exact name
    },

    # 3. Recurring job every 5 hours (00:00, 05:00, 10:00, 15:00, 20:00 IST)
    "recurring-batch-every-5-hours": {
        "task": "dealwallet.run_scraper_batch",
        "schedule": crontab(minute=0, hour="*/5"),
    },

    # 4. Recurring job every 6 hours (00:00, 06:00, 12:00, 18:00 IST)
    "recurring-batch-every-6-hours": {
        "task": "dealwallet.run_scraper_batch",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}