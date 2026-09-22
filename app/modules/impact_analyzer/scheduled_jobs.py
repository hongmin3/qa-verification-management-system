"""VXvue 사양서 동기화 cron job 등록. core/scheduler.py는 이 모듈의 존재를 모른다 —
app/main.py가 `start_scheduler([register_scheduled_jobs])`로 이 콜백을 넘겨준다.

VXvue 사양서 동기화는 크롤러가 Windows 전용이라, 이 스케줄러가 Ubuntu 서버에서 돌 때는
매일 시각이 되면 시도는 하되 `is_available_on_this_host()`가 False이면 조용히 건너뛴다.
실제 자동 실행은 크롤러가 있는 Windows PC의 작업 스케줄러(scripts/sync_vxvue_spec.py)가 담당한다.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import app_self_url
from app.core.product_config import load_product_config
from app.core.storage import Storage

logger = logging.getLogger("regression_analyzer")


def _sync_specification_job() -> None:
    from app.modules.impact_analyzer.vxvue_spec_sync import is_available_on_this_host, run

    storage = Storage()
    product = "VXvue"
    if not is_available_on_this_host():
        logger.info("scheduled_sync_skipped product=%s reason=크롤러_output_폴더_접근_불가", product)
        return
    if storage.is_sync_running(product, "specification"):
        logger.info("scheduled_sync_skipped product=%s reason=이미_실행_중", product)
        return
    sync_id = storage.sync_start(product, "specification", "alm_crawler")
    try:
        result = run(app_self_url())
        storage.sync_finish(sync_id, result["status"], result["detail"])
        logger.info("scheduled_sync_finished product=%s status=%s detail=%s", product, result["status"], result["detail"])
    except Exception as exc:
        storage.sync_finish(sync_id, "FAILED", str(exc))
        logger.exception("scheduled_sync_failed product=%s", product)


def register_scheduled_jobs(scheduler: BackgroundScheduler) -> None:
    config = load_product_config("vxvue")
    schedule_time = (config.sync.schedule_time if config else "07:30") or "07:30"
    day_of_week = (config.sync.day_of_week if config else "mon") or "mon"
    hour, _, minute = schedule_time.partition(":")
    scheduler.add_job(
        _sync_specification_job,
        CronTrigger(day_of_week=day_of_week, hour=int(hour or 7), minute=int(minute or 30), timezone="Asia/Seoul"),
        id="sync_vxvue_specification",
        replace_existing=True,
    )
    logger.info("scheduled_job_registered id=sync_vxvue_specification day_of_week=%s schedule_time=%s", day_of_week, schedule_time)
