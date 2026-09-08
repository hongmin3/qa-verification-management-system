"""제품 지식 폴더 수집 cron job 등록.

`impact_analyzer/scheduled_jobs.py` 와 같은 방식이다 — `core/scheduler.py` 는 이 모듈의
존재를 모르고, `app/main.py` 가 콜백으로 넘긴다.

지식 폴더는 QA 담당자 PC 에 있고 운영 서버에는 없다. 서버에서 이 job 이 시각에 깨어나도
폴더에 접근할 수 없으면 **조용히 건너뛴다** (오류로 만들지 않는다). 실제 자동 실행은 폴더가
있는 PC 의 작업 스케줄러(`scripts/sync_product_knowledge.py`)가 담당한다.

제품별로 job 을 따로 등록한다 — 제품마다 `sync.schedule_time` 이 다를 수 있고, 한 제품의
실패가 다른 제품 수집을 막지 않아야 한다.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.product_config import list_product_configs
from app.core.product_knowledge import product_slug, resolve_config, source_available, sync_and_register
from app.core.storage import Storage

logger = logging.getLogger("regression_analyzer")

SYNC_KIND = "product_knowledge"


def _sync_product_knowledge_job(product: str) -> None:
    storage = Storage()
    config = resolve_config(product)
    if config is None or not source_available(config):
        logger.info("knowledge_sync_skipped product=%s reason=지식_폴더_접근_불가", product)
        return
    if storage.is_sync_running(product, SYNC_KIND):
        logger.info("knowledge_sync_skipped product=%s reason=이미_실행_중", product)
        return
    sync_id = storage.sync_start(product, SYNC_KIND, "knowledge_folder")
    try:
        result = sync_and_register(product, storage=storage)
        storage.sync_finish(sync_id, result["status"], result["detail"])
        logger.info("knowledge_sync_finished product=%s status=%s detail=%s", product, result["status"], result["detail"])
    except Exception:
        storage.sync_finish(sync_id, "FAILED", "예외 발생 (로그 참고)")
        logger.exception("knowledge_sync_failed product=%s", product)


def register_scheduled_jobs(scheduler: BackgroundScheduler) -> None:
    for config in list_product_configs():
        if not config.knowledge_source.dir:
            continue
        schedule_time = config.sync.schedule_time or "07:45"
        hour, _, minute = schedule_time.partition(":")
        job_id = f"sync_product_knowledge_{product_slug(config.product)}"
        scheduler.add_job(
            _sync_product_knowledge_job,
            CronTrigger(
                day_of_week=config.sync.day_of_week or "mon",
                hour=int(hour or 7),
                minute=int(minute or 45),
                timezone="Asia/Seoul",
            ),
            id=job_id,
            args=[config.product],
            replace_existing=True,
        )
        logger.info("scheduled_job_registered id=%s day_of_week=%s schedule_time=%s", job_id, config.sync.day_of_week, schedule_time)
