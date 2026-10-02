import sys
import os
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
from celery_app import celery_inst, run_scraper_task

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

class RunScraperRequest(BaseModel):
    id: Optional[int] = 1
    x_name: Optional[str] = "myntra"
    x_script_path: Optional[str] = "myntra_scrapy.py"

app = FastAPI(title="Product Scraper Controller")

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "celery_redis"}

@app.post("/run-scraper")
async def trigger_scraper(payload: RunScraperRequest):
    script_name = payload.x_script_path or "myntra_scrapy.py"
    
    # Push job asynchronously to Redis Cloud
    task = run_scraper_task.delay(script_name)
    
    return {
        "success": True,
        "status": "queued_in_redis",
        "task_id": str(task.id),
        "script_name": script_name
    }

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    res = celery_inst.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": res.status,
        "result": str(res.result) if res.ready() else None
    }
