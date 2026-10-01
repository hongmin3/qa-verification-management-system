"""앱 프로세스 내부에서 도는 범용 스케줄러. 신규 systemd 유닛 없이 기존 uvicorn 프로세스
안에서만 동작한다 (서버 설정 변경 없음).

이 모듈은 특정 모듈(VXvue 사양서 동기화 등)의 job 내용을 알지 못한다 — 각 modules/* 패키지가
자신의 job을 등록하는 콜백(`register_scheduled_jobs(scheduler)`)을 제공하고, `start_scheduler`가
그 콜백들을 실행해 실제 job을 붙인다. 예: app/modules/knowledge/scheduled_jobs.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger("scheduler")
_scheduler: BackgroundScheduler | None = None


def start_scheduler(job_registrars: Iterable[Callable[[BackgroundScheduler], None]] = ()) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    for register in job_registrars:
        register(scheduler)
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler_started timezone=Asia/Seoul")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
