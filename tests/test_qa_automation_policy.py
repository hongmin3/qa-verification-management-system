"""제품별 일일·주간·중지 정책. Validates: REQ-QAINTEL-038, TEST-QAINTEL-023."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from app.modules.daily_qa import holidays, scheduled_jobs
from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import REPO_ROOT, make_settings, srs_item
from tests.qa_intel_harness import DAY1, Harness

KST = holidays.KST
BASE_SCHEDULE = {"enabled": True, "day_of_week": "mon-fri", "time": "07:30",
                 "skip_holidays": True, "calendar": "kr", "extra": (), "run_missed": True}


def set_flags(store, cfg, enabled=True, validation_period=True):
    store.set_state(cfg.state_key("automation_policy"), json.dumps(
        {"enabled": enabled, "validation_period": validation_period}))


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path / "root", tmp_path / "ws")
    store = DailyQaStore(cfg.db_path)
    launched = []

    class Popen:
        pid = 42

        def __init__(self, args, **kwargs):
            launched.append(args)

    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    monkeypatch.setattr(scheduled_jobs, "configured_products", lambda: [cfg.product])
    monkeypatch.setattr(scheduled_jobs, "schedule_settings", lambda settings=None: dict(BASE_SCHEDULE))
    monkeypatch.setattr(holidays, "table_path", lambda root, calendar="kr": REPO_ROOT / "config/holidays/kr.yaml")
    return cfg, store, launched, Popen


def trigger_of(args):
    return args[args.index("--trigger") + 1]


def test_off_launches_collect_only_and_manual_still_launches(launcher):
    # 2026-10-16 변경(REQ-ALMCHG-001): 꺼짐이어도 예약은 수집만 하는 collect 로 띄운다.
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    now = datetime(2026, 10, 13, 7, 30, tzinfo=KST)
    assert scheduled_jobs.launch_detached(today=now, popen=popen)["status"] == "launched"
    assert [trigger_of(args) for args in launched] == ["collect"]
    assert scheduled_jobs.launch_detached(trigger="manual", today=now, popen=popen)["status"] == "launched"
    assert len(launched) == 2


def test_weekly_later_business_day_launches_collect_only(launcher):
    # 2026-10-16 변경(REQ-ALMCHG-001): 첫 업무일이 아닌 날은 띄우지 않던 것을 collect 로 띄운다.
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, validation_period=False)
    assert scheduled_jobs.launch_detached(today=datetime(2026, 10, 13, 7, 30, tzinfo=KST), popen=popen)["status"] == "launched"
    assert [trigger_of(args) for args in launched] == ["collect"]


def test_weekly_holiday_monday_runs_tuesday_and_only_today_is_caught_up(launcher):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, validation_period=False)
    tuesday = datetime(2026, 10, 6, 8, 1, tzinfo=KST)  # 월요일 10/5 대체공휴일
    assert [r["status"] for r in scheduled_jobs.run_missed_schedules(tuesday, popen)] == ["launched"]
    assert scheduled_jobs.run_missed_schedules(tuesday + timedelta(minutes=10), popen) == []
    # 2026-10-16 변경(REQ-ALMCHG-001): 다음 날(수)은 분석일이 아니어도 수집(collect)을 만회한다.
    assert [r["status"] for r in scheduled_jobs.run_missed_schedules(tuesday + timedelta(days=1), popen)] == ["launched"]
    assert [trigger_of(args) for args in launched] == ["scheduled", "collect"]


def test_host_schedule_switch_blocks_every_automatic_entry_point(launcher, monkeypatch):
    cfg, store, launched, popen = launcher
    monkeypatch.setattr(scheduled_jobs, "schedule_settings", lambda settings=None: {**BASE_SCHEDULE, "enabled": False})
    now = datetime(2026, 10, 13, 8, 1, tzinfo=KST)
    store.set_state(cfg.state_key(STATE_CATCHUP_AFTER), (now - timedelta(minutes=10)).isoformat())
    assert scheduled_jobs.launch_detached(today=now, popen=popen)["status"] == "schedule_disabled"
    assert scheduled_jobs.run_missed_schedules(now, popen) == []
    assert scheduled_jobs.catchup_due_products(now) == []
    assert launched == []
    assert scheduled_jobs.launch_detached(trigger="manual", today=now, popen=popen)["status"] == "launched"


@pytest.mark.parametrize("enabled,validation_period", [(False, True), (True, False)])
def test_off_and_weekly_block_limit_catchup_without_clearing_pending(launcher, enabled, validation_period):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled, validation_period)
    now = datetime(2026, 10, 13, 8, 1, tzinfo=KST)
    after = (now - timedelta(minutes=10)).isoformat()
    store.set_state(cfg.state_key(STATE_CATCHUP_AFTER), after)
    assert scheduled_jobs.catchup_due_products(now) == []
    assert store.get_state(cfg.state_key(STATE_CATCHUP_AFTER)) == after
    assert scheduled_jobs.launch_detached(trigger="catchup", today=now, popen=popen)["status"] in ("policy_off", "not_due")
    assert launched == []


def test_default_daily_and_product_isolation_with_persistent_history(launcher):
    from app.modules.daily_qa import automation_policy as policy
    cfg, store, launched, popen = launcher
    assert policy.get_policy(store, cfg.slug)["mode"] == "daily"
    policy.save_policy(store, cfg.slug, enabled=False, validation_period=False, changed_by="internal-client")
    restarted = DailyQaStore(cfg.db_path)
    assert policy.get_policy(restarted, cfg.slug)["mode"] == "off"
    assert policy.get_policy(restarted, "bellalun-viewer")["mode"] == "daily"
    events = policy.history(restarted, cfg.slug)
    assert len(events) == 1 and events[0]["changed_by"] == "internal-client"
    assert events[0]["enabled"] is False and events[0]["validation_period"] is False
    assert events[0]["updated_at"]
    policy.save_policy(restarted, cfg.slug, enabled=False, validation_period=False)
    assert len(policy.history(restarted, cfg.slug)) == 1


def test_policy_and_history_rollback_together_if_history_write_fails(launcher):
    from app.modules.daily_qa import automation_policy as policy
    cfg, store, _, _ = launcher
    policy.save_policy(store, cfg.slug, enabled=True, validation_period=False)
    before = policy.get_policy(store, cfg.slug), policy.history(store, cfg.slug)
    with store.storage.connect() as db:
        db.execute("CREATE TRIGGER reject_policy_history BEFORE UPDATE ON daily_qa_state "
                   "WHEN NEW.key='vxvue:automation_policy_history' "
                   "BEGIN SELECT RAISE(ABORT, 'simulated history write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="simulated history write failure"):
        policy.save_policy(store, cfg.slug, enabled=False, validation_period=False)
    assert (policy.get_policy(store, cfg.slug), policy.history(store, cfg.slug)) == before


def test_off_collect_consumes_todays_schedule(launcher):
    # 2026-10-16 변경(REQ-ALMCHG-001): 꺼짐이어도 collect 를 띄우므로 오늘 몫을 처리한 것으로 남긴다.
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    now = datetime(2026, 10, 13, 8, 1, tzinfo=KST)
    assert scheduled_jobs.launch_scheduled(now=now, popen=popen)["status"] == "launched"
    assert store.get_state(cfg.state_key(scheduled_jobs.STATE_SCHEDULED_DATE)) == "2026-10-13"
    set_flags(store, cfg)
    assert scheduled_jobs.run_missed_schedules(now, popen) == []
    assert [trigger_of(args) for args in launched] == ["collect"]


@pytest.mark.parametrize("now,extra,expected", [
    (datetime(2026, 10, 5, 6, tzinfo=KST), (), datetime(2026, 10, 6, 7, 30, tzinfo=KST)),
    (datetime(2026, 10, 6, 8, tzinfo=KST), (), datetime(2026, 10, 12, 7, 30, tzinfo=KST)),
    (datetime(2026, 10, 12, 6, tzinfo=KST), ("2026-10-12", "2026-10-13"), datetime(2026, 10, 14, 7, 30, tzinfo=KST)),
    (datetime(2026, 10, 12, 6, tzinfo=KST), tuple(f"2026-10-{i}" for i in range(12, 17)), datetime(2026, 10, 19, 7, 30, tzinfo=KST)),
])
def test_weekly_next_run_uses_first_business_day_and_skips_a_fully_closed_week(launcher, now, extra, expected):
    from app.modules.daily_qa import automation_policy as policy
    cfg, store, _, _ = launcher
    policy.save_policy(store, cfg.slug, enabled=True, validation_period=False)
    assert policy.next_run(cfg, store, {**BASE_SCHEDULE, "extra": extra}, now) == expected
    policy.save_policy(store, cfg.slug, enabled=False, validation_period=False)
    assert policy.next_run(cfg, store, BASE_SCHEDULE, now) is None


def test_manual_analysis_finishes_but_off_skips_email(tmp_path):
    h = Harness(tmp_path)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    set_flags(h.store, h.cfg, enabled=False)
    result = h.run(DAY1, trigger="manual")
    assert h.sent == [] and result["email"]["status"] == "skipped"
    assert result["status"] not in ("RUNNING", "CANCELLED")
    assert h.store.get_run(result["run_id"])["status"] == result["status"]
    assert (h.cfg.srs_snapshot_dir / "2026-09-29.json").is_file()
    assert "자동 점검" in result["stages"]["email"]["note"]


def test_off_is_read_again_immediately_before_sender(tmp_path, monkeypatch):
    from app.modules.daily_qa import report
    h = Harness(tmp_path)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    original = report.build_email

    def switch_off(*args, **kwargs):
        rendered = original(*args, **kwargs)
        set_flags(h.store, h.cfg, enabled=False)
        return rendered

    monkeypatch.setattr(report, "build_email", switch_off)
    result = h.run(DAY1)
    assert not h.sent
    assert result["status"] not in ("RUNNING", "CANCELLED")
    assert result["email"]["status"] == "skipped"
