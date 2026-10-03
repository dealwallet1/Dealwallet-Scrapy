from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel
from .celery_app import celery_inst
from .tasks import run_scraper_batch

app = FastAPI(title="DealWallet Scraper Controller")

class RunScraperRequest(BaseModel):
    id: Optional[int] = 1

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "celery_redis",
    }

@app.post("/run-scraper")
async def trigger_scraper(payload: RunScraperRequest):
    # Enqueues the 18-spider batch run task into Redis
    task = run_scraper_batch.delay()
    return {
        "success": True,
        "status": "queued_in_redis",
        "task_id": str(task.id),
    }

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    result = celery_inst.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": str(result.result) if result.ready() else None,
    }