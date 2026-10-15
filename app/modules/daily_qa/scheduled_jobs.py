"""QA Agent 점검 예약 (SPEC REQ-DAILY-001, REQ-QAINTEL-001·021·025·035·038).

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

예약은 메모리에만 있어서 예약 시각에 앱이 꺼져 있었으면 그날 실행이 사라진다. 그래서 예약을 처리한
날짜를 DB 상태에 남기고, 감시 예약이 앱 시작 직후와 10분마다 오늘 몫이 빠졌는지 본다(REQ-QAINTEL-035).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from datetime import datetime, time, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.modules.daily_qa import automation_policy, holidays
from app.modules.daily_qa.settings import configured_products, load

logger = logging.getLogger("regression_analyzer")

JOB_PREFIX = "qa_agent_"
CATCHUP_JOB_ID = "qa_agent_limit_catchup"
MISSED_JOB_ID = "qa_agent_missed_schedule"
STATE_SCHEDULED_DATE = "scheduled_date"   # 예약 실행을 처리한 날짜(한국 시간). 정시·만회가 함께 쓴다
_schedule_lock = threading.Lock()         # 정시 예약과 만회 확인이 같은 순간 둘 다 띄우지 않게 한다


def job_id(slug: str) -> str:
    return f"{JOB_PREFIX}{slug}"


def schedule_settings(settings=None) -> dict:
    settings = settings or get_settings()
    extra = settings.get("daily_qa.schedule.extra_holidays", []) or []
    return {
        "day_of_week": str(settings.get("daily_qa.schedule.day_of_week", "mon-fri") or "mon-fri"),
        "time": str(settings.get("daily_qa.schedule.time", "04:00") or "04:00"),
        "skip_holidays": bool(settings.get("daily_qa.schedule.skip_holidays", True)),
        "calendar": str(settings.get("daily_qa.schedule.holiday_calendar", "kr") or "kr"),
        "extra": tuple(str(item) for item in extra),
        "run_missed": bool(settings.get("daily_qa.schedule.run_missed", True)),
        "enabled": bool(settings.get("daily_qa.schedule.enabled", True)),
    }


def launch_detached(product: str | None = None, *, trigger: str = "scheduled", today: datetime | None = None,
                    popen=subprocess.Popen, since: str = "", until: str = "", issue_audit: bool = False,
                    on_demand: str = "", finding_id: int | None = None, selection_id: str = "", analysis_scope: str = "all") -> dict:
    """점검 프로세스를 띄우고 바로 돌아온다. 돌려주는 `status` 로 화면·로그가 이유를 안다.

    `launched` 이외의 상태는 휴일·진행 중·설정 없음·자동 점검 정책 등으로 띄우지 않은 결과다.
    """
    from app.modules.daily_qa.pipeline import is_running

    cfg = load(product=product)
    if not cfg.enabled:
        logger.info("qa_agent_skipped reason=disabled product=%s", cfg.slug)
        return {"status": "disabled", "product": cfg.slug}
    if trigger in ("scheduled", "catchup"):
        from app.modules.daily_qa.store import DailyQaStore

        sched = schedule_settings()
        store = DailyQaStore(cfg.db_path)
        if not sched.get("enabled", True):
            logger.info("qa_agent_skipped reason=schedule_disabled product=%s", cfg.slug)
            return {"status": "schedule_disabled", "product": cfg.slug}
        policy = automation_policy.get_policy(store, cfg.slug)
        if policy["mode"] == "off":
            logger.info("qa_agent_skipped reason=policy_off product=%s", cfg.slug)
            return {"status": "policy_off", "product": cfg.slug}
        if trigger == "catchup" and not automation_policy.catchup_allowed(cfg, store, sched):
            logger.info("qa_agent_skipped reason=weekly_catchup_disabled product=%s", cfg.slug)
            return {"status": "not_due", "product": cfg.slug}
    # 종료일이 지난 날인 기간 실행과 버튼 요청 실행은 저장 스냅샷만 쓰므로 Polarion 이 없어도 된다 (REQ-QAINTEL-021, 027).
    if not cfg.polarion.configured and not until and not on_demand and not selection_id:
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
        if not automation_policy.is_schedule_day(cfg, store, sched, day):
            logger.info("qa_agent_skipped reason=not_due product=%s date=%s", cfg.slug, day.isoformat())
            return {"status": "not_due", "product": cfg.slug}
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
    if selection_id:
        command += ['--selection-id', selection_id, '--no-email']
    if analysis_scope != 'all':
        command += ['--analysis-scope', analysis_scope]
    if on_demand:
        command += ["--on-demand", on_demand, "--no-email"]
        if finding_id is not None:
            command += ["--finding", str(finding_id)]
    with (log_dir / "daily_qa.out").open("a", encoding="utf-8") as log:
        process = popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
    logger.info("daily_qa_launched pid=%s product=%s trigger=%s", process.pid, cfg.slug, trigger)
    return {"status": "launched", "pid": process.pid, "product": cfg.slug}


def launch_scheduled(product: str | None = None, *, now: datetime | None = None, popen=subprocess.Popen) -> dict:
    """정시 예약과 만회 확인이 부르는 예약 실행. 처리한 날짜를 남겨 하루 한 번만 띄운다 (REQ-QAINTEL-035).

    오늘 이미 처리했으면 `already` 다. 다른 실행이 돌고 있으면(`running`) 날짜를 남기지 않아 만회 확인이
    10분 뒤 다시 본다.
    """
    from app.modules.daily_qa.store import DailyQaStore

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(holidays.KST).date().isoformat()
    cfg = load(product=product)
    store = DailyQaStore(cfg.db_path)
    key = cfg.state_key(STATE_SCHEDULED_DATE)
    with _schedule_lock:
        if store.get_state(key) == today:
            return {"status": "already", "product": cfg.slug, "date": today}
        result = launch_detached(product, trigger="scheduled", today=now, popen=popen)
        if result.get("status") not in ("running", "policy_off", "not_due", "schedule_disabled"):
            store.set_state(key, today)
    return result


def run_missed_schedules(now: datetime | None = None, popen=subprocess.Popen) -> list[dict]:
    """예약 시각이 지났는데 오늘 예약을 처리하지 않은 제품의 실행을 띄운다. 지난 날은 만회하지 않는다."""
    from app.modules.daily_qa.store import DailyQaStore

    sched = schedule_settings()
    if not sched.get("run_missed", True) or not sched.get("enabled", True):
        return []
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(holidays.KST)
    hour, _, minute = sched["time"].partition(":")
    scheduled_at = time(int(hour or 7), int(minute or 30))
    if local.weekday() not in holidays.parse_days(sched["day_of_week"]) or local.time() < scheduled_at:
        return []
    today = local.date().isoformat()
    results = []
    for product in configured_products():
        cfg = load(product=product)
        store = DailyQaStore(cfg.db_path)
        policy = automation_policy.get_policy(store, cfg.slug)
        if policy["mode"] == "off" or (policy["mode"] == "weekly" and not automation_policy.is_schedule_day(cfg, store, sched, local.date())):
            continue
        if store.get_state(cfg.state_key(STATE_SCHEDULED_DATE)) == today:
            continue
        logger.info("qa_agent_missed_schedule product=%s date=%s time=%s", cfg.slug, today, sched["time"])
        result = launch_scheduled(product, now=now, popen=popen)
        if result.get("status") != "already":
            results.append(result)
    return results


def catchup_due_products(now: datetime | None = None, clear: bool = True) -> list[str]:
    """일일 모드에서 한도(세션·주간)나 사용률 기준 멈춤이 풀린 제품. `clear` 면 실행할 차례 기록만 지운다 (REQ-QAINTEL-025·038)."""
    from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
    from app.modules.daily_qa.store import DailyQaStore

    now = now or datetime.now(timezone.utc)
    sched = schedule_settings()
    if not sched.get("enabled", True):
        return []
    due = []
    for product in configured_products():
        cfg = load(product=product)
        store = DailyQaStore(cfg.db_path)
        if not automation_policy.catchup_allowed(cfg, store, sched):
            continue
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
        if result.get("status") not in ("running", "policy_off", "not_due", "schedule_disabled"):
            cfg = load(product=product)
            DailyQaStore(cfg.db_path).set_state(cfg.state_key(STATE_CATCHUP_AFTER), "")
        results.append(result)
    return results


def register_scheduled_jobs(scheduler: BackgroundScheduler) -> None:
    sched = schedule_settings()
    hour, _, minute = sched["time"].partition(":")
    # 예약은 한 곳에서만 켠다. 감시는 등록해도 비활성 서버에서는 자동 재개하지 않는다 (REQ-QAINTEL-038).
    if not sched.get("enabled", True):
        scheduler.add_job(run_catchups, IntervalTrigger(minutes=10), id=CATCHUP_JOB_ID, replace_existing=True, max_instances=1)
        logger.info("scheduled_job_skipped reason=daily_qa.schedule.enabled=false registered=%s", CATCHUP_JOB_ID)
        return
    for product in configured_products():
        slug = load(product=product).slug
        scheduler.add_job(
            launch_scheduled,
            CronTrigger(day_of_week=sched["day_of_week"], hour=int(hour or 7), minute=int(minute or 30), timezone="Asia/Seoul"),
            kwargs={"product": product},
            id=job_id(slug),
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduled_job_registered id=%s day_of_week=%s time=%s", job_id(slug), sched["day_of_week"], sched["time"])
    scheduler.add_job(run_catchups, IntervalTrigger(minutes=10), id=CATCHUP_JOB_ID, replace_existing=True, max_instances=1)
    logger.info("scheduled_job_registered id=%s every=10m", CATCHUP_JOB_ID)
    # 앱이 예약 시각 뒤에 켜졌으면 1분 뒤 첫 확인에서 오늘 몫을 띄운다 (REQ-QAINTEL-035).
    scheduler.add_job(run_missed_schedules, IntervalTrigger(minutes=10), id=MISSED_JOB_ID, replace_existing=True,
                      max_instances=1, next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1))
    logger.info("scheduled_job_registered id=%s every=10m first=+1m", MISSED_JOB_ID)
