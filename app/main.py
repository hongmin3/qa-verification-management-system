from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.storage import Storage
from app.modules.impact_analyzer.scheduled_jobs import register_scheduled_jobs
from app.modules.qa_agent.scheduled_jobs import register_scheduled_jobs as register_knowledge_jobs
from app.modules.daily_qa.scheduled_jobs import register_scheduled_jobs as register_daily_qa_jobs
from app.modules.impact_analyzer.router import resume_queued_jobs as resume_queued_impact_jobs
from app.modules.manual_review.router import resume_queued_jobs as resume_queued_manual_jobs
from app.modules.qa_agent.router import resume_queued_jobs as resume_queued_qa_agent_jobs
from app.web.router import build_router

settings = get_settings()
storage = Storage()


def _ensure_configured_products() -> None:
    """설정 파일이 있는 제품을 제품 표에 더한다 (REQ-KNOW-002). 설정을 배포하고 다시 띄우면 각 기능의 제품 목록에 나온다."""
    from app.core.product_config import list_product_configs

    try:
        configs = list_product_configs()
    except Exception:  # 설정 파일 하나가 깨져도 앱 시작을 막지 않는다 (NFR-OPS-002).
        return
    for config in configs:
        storage.ensure_product(config.product)


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.fail_running_analyses()
    storage.fail_running_syncs()
    _ensure_configured_products()
    resume_queued_impact_jobs()
    resume_queued_manual_jobs()
    resume_queued_qa_agent_jobs()
    start_scheduler([register_scheduled_jobs, register_knowledge_jobs, register_daily_qa_jobs])
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
