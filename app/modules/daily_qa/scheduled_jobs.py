"""일일 QA 점검 예약 (SPEC REQ-DAILY-001).

다른 모듈과 같은 방식이다 — `app/core/scheduler.py` 는 이 모듈을 모르고, `app/main.py` 가
콜백으로 넘긴다. 신규 systemd 유닛을 만들지 않는다.

점검 한 번은 Claude 작업 때문에 수십 분이 걸릴 수 있다. 그래서 웹 프로세스 안에서 직접 돌리지
않고 `scripts/run_daily_qa.py` 를 **분리된 별도 프로세스**로 띄운다. 앱이 재시작돼도 진행 중인
점검은 끊기지 않고, 중복 실행은 점검 쪽 잠금 파일이 막는다.

Polarion 설정이 없는 호스트(개발 PC 등)에서는 시각에 깨어나도 점검을 띄우지 않는다.
Claude 토큰만 없으면 점검은 띄운다. 그때는 AI 단계만 `건너뜀` 으로 남고, AI 가 필요 없는
점검(삭제 SRS 참조, 사양–TC 연결 점검)은 돌며, 이유가 실행 기록과 메일에 남는다.
"""

from __future__ import annotations

import logging
import subprocess
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.modules.daily_qa.settings import load

logger = logging.getLogger("regression_analyzer")

JOB_ID = "daily_qa_vxvue"


def launch_detached() -> dict:
    """점검 프로세스를 띄우고 바로 돌아온다."""
    cfg = load()
    if not cfg.enabled:
        logger.info("daily_qa_skipped reason=disabled")
        return {"status": "disabled"}
    if not cfg.polarion.configured:
        logger.info("daily_qa_skipped reason=polarion_설정_없음")
        return {"status": "not_configured"}
    if not cfg.claude_token:
        logger.warning("daily_qa_without_claude_token AI 단계는 건너뜁니다")
    log_dir = cfg.root / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    options: dict = {"cwd": cfg.root, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with (log_dir / "daily_qa.out").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(cfg.root / "scripts" / "run_daily_qa.py")], stdout=log, stderr=subprocess.STDOUT, **options
        )
    logger.info("daily_qa_launched pid=%s", process.pid)
    return {"status": "launched", "pid": process.pid}


def register_scheduled_jobs(scheduler: BackgroundScheduler) -> None:
    settings = get_settings()
    days = str(settings.get("daily_qa.schedule.day_of_week", "mon-fri") or "mon-fri")
    at = str(settings.get("daily_qa.schedule.time", "07:30") or "07:30")
    hour, _, minute = at.partition(":")
    scheduler.add_job(
        launch_detached,
        CronTrigger(day_of_week=days, hour=int(hour or 7), minute=int(minute or 30), timezone="Asia/Seoul"),
        id=JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("scheduled_job_registered id=%s day_of_week=%s time=%s", JOB_ID, days, at)
