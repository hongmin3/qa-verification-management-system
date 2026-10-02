"""QA Agent 점검 예약 (SPEC REQ-DAILY-001, REQ-QAINTEL-001·021·025).

다른 모듈과 같은 방식이다 — `app/core/scheduler.py` 는 이 모듈을 모르고, `app/main.py` 가
콜백으로 넘긴다. 신규 systemd 유닛을 만들지 않는다.

점검 한 번은 Claude 작업 때문에 수십 분이 걸릴 수 있다. 그래서 웹 프로세스 안에서 직접 돌리지
않고 `scripts/run_daily_qa.py --product <slug>` 를 **분리된 별도 프로세스**로 띄운다. 앱이 재시작돼도
진행 중인 점검은 끊기지 않고, 중복 실행은 점검 쪽 잠금 파일이 막는다. [지금 실행] 버튼도 같은
함수(`launch_detached`)를 쓴다 — 수동 실행용 분석 코드는 따로 없다.

제품마다 예약을 하나씩 둔다(`qa_agent_<slug>`). 한 작업이 제품 목록을 돌면 한 제품의 오래 걸리는
실행이 다음 제품을 막고, 제품마다 잠금·로그를 나누기 어렵다.

Polarion 설정이 없는 호스트(개발 PC 등)에서는 시각에 깨어나도 점검을 띄우지 않는다. 공휴일에도
띄우지 않는다. Claude 토큰만 없으면 점검은 띄운다 — 변경 감지와 AI 가 필요 없는 점검은 돈다.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.modules.daily_qa import holidays
from app.modules.daily_qa.settings import configured_products, load

logger = logging.getLogger("regression_analyzer")

JOB_PREFIX = "qa_agent_"
CATCHUP_JOB_ID = "qa_agent_limit_catchup"


def job_id(slug: str) -> str:
    return f"{JOB_PREFIX}{slug}"


def schedule_settings(settings=None) -> dict:
    settings = settings or get_settings()
    extra = settings.get("daily_qa.schedule.extra_holidays", []) or []
    return {
        "day_of_week": str(settings.get("daily_qa.schedule.day_of_week", "mon-fri") or "mon-fri"),
        "time": str(settings.get("daily_qa.schedule.time", "07:30") or "07:30"),
        "skip_holidays": bool(settings.get("daily_qa.schedule.skip_holidays", True)),
        "calendar": str(settings.get("daily_qa.schedule.holiday_calendar", "kr") or "kr"),
        "extra": tuple(str(item) for item in extra),
    }


def launch_detached(product: str | None = None, *, trigger: str = "scheduled", today: datetime | None = None,
                    popen=subprocess.Popen, since: str = "", until: str = "", issue_audit: bool = False,
                    on_demand: str = "", finding_id: int | None = None) -> dict:
    """점검 프로세스를 띄우고 바로 돌아온다. 돌려주는 `status` 로 화면·로그가 이유를 안다.

    `holiday`·`running`·`disabled`·`not_configured` 이면 띄우지 않았다.
    """
    from app.modules.daily_qa.pipeline import is_running

    cfg = load(product=product)
    if not cfg.enabled:
        logger.info("qa_agent_skipped reason=disabled product=%s", cfg.slug)
        return {"status": "disabled", "product": cfg.slug}
    # 종료일이 지난 날인 기간 실행과 버튼 요청 실행은 저장 스냅샷만 쓰므로 Polarion 이 없어도 된다 (REQ-QAINTEL-021, 027).
    if not cfg.polarion.configured and not until and not on_demand:
        logger.info("qa_agent_skipped reason=polarion_설정_없음 product=%s", cfg.slug)
        return {"status": "not_configured", "product": cfg.slug}
    if trigger == "scheduled":
        sched = schedule_settings()
        day = (today or datetime.now(timezone.utc)).astimezone(holidays.KST).date()
        check = holidays.check(day, cfg.root, sched["calendar"], sched["extra"])
        if check.table_missing:
            logger.warning("qa_agent_holiday_table_missing year=%s", day.year)
        if sched["skip_holidays"] and check.holiday:
            logger.info("qa_agent_skipped reason=holiday product=%s date=%s name=%s", cfg.slug, day.isoformat(), check.name)
            return {"status": "holiday", "product": cfg.slug, "date": day.isoformat(), "name": check.name}
    if is_running(cfg):
        logger.info("qa_agent_skipped reason=running product=%s", cfg.slug)
        return {"status": "running", "product": cfg.slug}
    if not cfg.claude_token:
        logger.warning("daily_qa_without_claude_token AI 단계는 건너뜁니다 product=%s", cfg.slug)
    log_dir = cfg.root / "output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    options: dict = {"cwd": cfg.root, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    command = [sys.executable, str(cfg.root / "scripts" / "run_daily_qa.py"), "--product", cfg.product, "--trigger", trigger]
    if since:
        command += ["--since", since]
    if until:
        command += ["--until", until]
    if issue_audit:
        command += ["--issue-audit"]
    if on_demand:
        command += ["--on-demand", on_demand, "--no-email"]
        if finding_id is not None:
            command += ["--finding", str(finding_id)]
    with (log_dir / "daily_qa.out").open("a", encoding="utf-8") as log:
        process = popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
    logger.info("daily_qa_launched pid=%s product=%s trigger=%s", process.pid, cfg.slug, trigger)
    return {"status": "launched", "pid": process.pid, "product": cfg.slug}


def catchup_due_products(now: datetime | None = None, clear: bool = True) -> list[str]:
    """세션 한도가 풀린 뒤 다시 돌 차례인 제품 (REQ-QAINTEL-025). `clear` 면 차례 기록을 지운다."""
    from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
    from app.modules.daily_qa.store import DailyQaStore

    now = now or datetime.now(timezone.utc)
    due = []
    for product in configured_products():
        cfg = load(product=product)
        store = DailyQaStore(cfg.db_path)
        after = store.get_state(cfg.state_key(STATE_CATCHUP_AFTER))
        if after:
            try:
                ready = datetime.fromisoformat(after) <= now
            except ValueError:
                ready = True
            if ready:
                if clear:
                    store.set_state(cfg.state_key(STATE_CATCHUP_AFTER), "")
                due.append(product)
    return due


def run_catchups() -> list[dict]:
    """catch-up 실행을 띄운다. 다른 실행이 돌고 있으면 기록을 남겨 10분 뒤 다시 본다."""
    from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
    from app.modules.daily_qa.store import DailyQaStore

    results = []
    for product in catchup_due_products(clear=False):
        result = launch_detached(product, trigger="catchup")
        if result.get("status") != "running":
            cfg = load(product=product)
            DailyQaStore(cfg.db_path).set_state(cfg.state_key(STATE_CATCHUP_AFTER), "")
        results.append(result)
    return results


def register_scheduled_jobs(scheduler: BackgroundScheduler) -> None:
    sched = schedule_settings()
    hour, _, minute = sched["time"].partition(":")
    for product in configured_products():
        slug = load(product=product).slug
        scheduler.add_job(
            launch_detached,
            CronTrigger(day_of_week=sched["day_of_week"], hour=int(hour or 7), minute=int(minute or 30), timezone="Asia/Seoul"),
            kwargs={"product": product},
            id=job_id(slug),
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduled_job_registered id=%s day_of_week=%s time=%s", job_id(slug), sched["day_of_week"], sched["time"])
    scheduler.add_job(run_catchups, IntervalTrigger(minutes=10), id=CATCHUP_JOB_ID, replace_existing=True, max_instances=1)
    logger.info("scheduled_job_registered id=%s every=10m", CATCHUP_JOB_ID)
