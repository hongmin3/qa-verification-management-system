from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.storage import Storage
from app.modules.impact_analyzer.scheduled_jobs import register_scheduled_jobs
from app.modules.impact_analyzer.router import resume_queued_jobs as resume_queued_impact_jobs
from app.modules.manual_review.router import resume_queued_jobs as resume_queued_manual_jobs
from app.modules.qa_agent.router import resume_queued_jobs as resume_queued_qa_agent_jobs
from app.web.router import build_router

settings = get_settings()
storage = Storage()


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.fail_running_analyses()
    resume_queued_impact_jobs()
    resume_queued_manual_jobs()
    resume_queued_qa_agent_jobs()
    start_scheduler([register_scheduled_jobs])
    yield
    stop_scheduler()


app = FastAPI(title=settings.get("app.name"), version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=settings.root / "app" / "web" / "static"), name="static")
app.include_router(build_router())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/operations/status")
def operations_status() -> dict:
    timeout = int(get_settings().get("analysis.job_timeout_minutes", 30) or 30)
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=timeout)).isoformat()
    return storage.operations_status(stale_before)
