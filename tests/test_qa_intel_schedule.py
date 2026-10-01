"""예약·공휴일·실행 잠금·Claude 사용량 한도 (SPEC REQ-QAINTEL-001·021·025).

Validates: REQ-QAINTEL-001, REQ-QAINTEL-021, REQ-QAINTEL-025
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.daily_qa import claude_limits, holidays, scheduled_jobs
from app.modules.daily_qa.pipeline import STATE_CATCHUP_AFTER
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import make_settings

REPO = Path(__file__).resolve().parents[1]
KST = holidays.KST


@pytest.mark.parametrize(("day", "holiday", "name"), [
    (date(2026, 9, 25), True, "추석"),
    (date(2026, 9, 24), True, "추석 연휴"),
    (date(2026, 10, 5), True, "대체공휴일(개천절)"),
    (date(2026, 10, 9), True, "한글날"),
    (date(2026, 6, 3), True, "제9회 전국동시지방선거"),
    (date(2026, 10, 6), False, ""),
    (date(2027, 2, 9), True, "대체공휴일(설날)"),
])
def test_holiday_table(day, holiday, name):
    check = holidays.check(day, REPO)
    assert (check.holiday, check.name) == (holiday, name) and not check.table_missing


def test_year_missing_from_table_uses_fixed_holidays_and_warns():
    assert holidays.check(date(2031, 12, 25), REPO) == holidays.HolidayCheck(True, "성탄절", table_missing=True)
    assert holidays.check(date(2031, 9, 15), REPO).holiday is False


def test_extra_company_holiday():
    assert holidays.check(date(2026, 10, 6), REPO, extra=("2026-10-06",)).holiday


def test_next_run_skips_weekend_and_chuseok():
    now = datetime(2026, 9, 23, 8, 0, tzinfo=KST)            # 수요일 07:30 이 지난 뒤
    assert holidays.next_run(now, REPO) == datetime(2026, 9, 28, 7, 30, tzinfo=KST)   # 목~토 추석, 일요일 → 월요일
    before = datetime(2026, 9, 23, 7, 0, tzinfo=KST)
    assert holidays.next_run(before, REPO) == datetime(2026, 9, 23, 7, 30, tzinfo=KST)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path / "root", tmp_path / "ws")
    (tmp_path / "root").mkdir()
    launched: list = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            launched.append(args)
            self.pid = 99

    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    monkeypatch.setattr(scheduled_jobs, "schedule_settings", lambda settings=None: {
        "day_of_week": "mon-fri", "time": "07:30", "skip_holidays": True, "calendar": "kr", "extra": ()})
    return cfg, launched, FakePopen


def test_scheduled_run_on_holiday_launches_nothing(launcher, monkeypatch):
    cfg, launched, popen = launcher
    logged: list = []
    monkeypatch.setattr(scheduled_jobs.logger, "info", lambda message, *args: logged.append(message % args))
    monkeypatch.setattr(holidays, "table_path", lambda root, calendar="kr": REPO / "config" / "holidays" / "kr.yaml")
    result = scheduled_jobs.launch_detached(today=datetime(2026, 9, 25, 7, 30, tzinfo=KST), popen=popen)
    assert result["status"] == "holiday" and result["name"] == "추석" and launched == []
    assert any("reason=holiday" in line and "name=추석" in line for line in logged)


def test_manual_run_on_holiday_still_launches(launcher, monkeypatch):
    cfg, launched, popen = launcher
    monkeypatch.setattr(holidays, "table_path", lambda root, calendar="kr": REPO / "config" / "holidays" / "kr.yaml")
    result = scheduled_jobs.launch_detached(trigger="manual", today=datetime(2026, 9, 25, 7, 30, tzinfo=KST), popen=popen)
    assert result["status"] == "launched" and launched


def test_running_lock_blocks_launch_but_stale_lock_does_not(launcher):
    cfg, launched, popen = launcher
    cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.lock_path.write_text("1", encoding="utf-8")
    assert scheduled_jobs.launch_detached(trigger="manual", popen=popen)["status"] == "running" and not launched
    old = time.time() - 7 * 3600
    os.utime(cfg.lock_path, (old, old))                       # 6시간 넘은 잠금은 멈춘 실행이 남긴 것
    assert scheduled_jobs.launch_detached(trigger="manual", popen=popen)["status"] == "launched"


def test_disabled_product_is_not_launched(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path, tmp_path / "ws", enabled=False)
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    assert scheduled_jobs.launch_detached(trigger="manual", popen=lambda *a, **k: None)["status"] == "disabled"


def test_period_arguments_reach_the_cli(launcher):
    cfg, launched, popen = launcher
    scheduled_jobs.launch_detached(trigger="manual", popen=popen, since="2026-09-20", until="2026-09-25")
    args = launched[0]
    assert args[args.index("--since") + 1] == "2026-09-20" and args[args.index("--until") + 1] == "2026-09-25"


def test_one_job_per_product_plus_catchup_watch(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    monkeypatch.setattr(scheduled_jobs, "configured_products", lambda settings=None: ["VXvue", "Bellalun Viewer"])
    scheduler = BackgroundScheduler()
    scheduled_jobs.register_scheduled_jobs(scheduler)
    ids = {job.id for job in scheduler.get_jobs()}
    assert {"qa_agent_vxvue", "qa_agent_bellalun-viewer", "qa_agent_limit_catchup"} <= ids
    trigger = str(scheduler.get_job("qa_agent_vxvue").trigger)
    assert "day_of_week='mon-fri'" in trigger and "hour='7'" in trigger and "minute='30'" in trigger
    assert scheduler.get_job("qa_agent_vxvue").kwargs == {"product": "VXvue"}


def test_catchup_fires_once_after_reset(tmp_path, monkeypatch):
    cfg = make_settings(tmp_path, tmp_path / "ws")
    store = DailyQaStore(cfg.db_path)
    monkeypatch.setattr(scheduled_jobs, "configured_products", lambda settings=None: ["VXvue"])
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: cfg)
    now = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
    store.set_state(cfg.state_key(STATE_CATCHUP_AFTER), (now + timedelta(minutes=10)).isoformat())
    assert scheduled_jobs.catchup_due_products(now) == []
    assert scheduled_jobs.catchup_due_products(now + timedelta(minutes=11)) == ["VXvue"]
    assert scheduled_jobs.catchup_due_products(now + timedelta(minutes=12)) == []          # 한 번만


NOW = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)   # 12:00 KST


@pytest.mark.parametrize(("text", "kind", "reset_kst"), [
    ("You've hit your session limit · resets 7pm", claude_limits.KIND_SESSION, None),     # 서버 시간대로 읽는다
    ("5-hour limit reached ∙ resets 3pm (Asia/Seoul)", claude_limits.KIND_SESSION, "2026-09-30 15:00"),
    ("5-hour limit reached ∙ resets 11am (Asia/Seoul)", claude_limits.KIND_SESSION, "2026-10-01 11:00"),  # 지난 시각은 다음 날
    ("Weekly limit reached ∙ resets Oct 6, 9am (Asia/Seoul)", claude_limits.KIND_WEEKLY, "2026-10-06 09:00"),
    ("Weekly limit reached ∙ resets Mon 9am (Asia/Seoul)", claude_limits.KIND_WEEKLY, "2026-10-05 09:00"),
    (f"Claude AI usage limit reached|{int((NOW + timedelta(hours=2)).timestamp())}", claude_limits.KIND_SESSION, "2026-09-30 14:00"),
    (f"Claude AI usage limit reached|{int((NOW + timedelta(days=3)).timestamp())}", claude_limits.KIND_WEEKLY, "2026-10-03 12:00"),
    ("Your usage limit has been reached", claude_limits.KIND_UNKNOWN, ""),
])
def test_limit_messages_are_classified_with_reset_time(text, kind, reset_kst):
    info = claude_limits.classify(text, NOW)
    assert info is not None and info.kind == kind
    if reset_kst is not None:
        assert info.reset_kst() == reset_kst


@pytest.mark.parametrize("text", ["OAuth token has expired. Please run /login", "Invalid API key · Fix external API key"])
def test_auth_failures_block_until_token_changes(text):
    info = claude_limits.classify(text, NOW, token="old-token")
    assert info.kind == claude_limits.KIND_AUTH and "tok" not in info.token_fingerprint
    assert info.active(NOW, "old-token") and not info.active(NOW, "new-token")
    assert "setup-token" in info.describe()


def test_ordinary_failures_are_not_mistaken_for_limits():
    assert claude_limits.classify("exit=1 결과 파일을 쓰지 못했습니다", NOW) is None
    assert claude_limits.classify("제한 시간 900초 초과", NOW) is None


def test_limit_expires_at_reset_and_unknown_reset_never_blocks():
    info = claude_limits.classify("5-hour limit reached ∙ resets 3pm (Asia/Seoul)", NOW)
    assert info.active(NOW) and not info.active(NOW + timedelta(hours=4))
    assert not claude_limits.classify("usage limit reached", NOW).active(NOW)
    restored = claude_limits.load(claude_limits.dump(info))
    assert restored.kind == info.kind and restored.reset_at == info.reset_at
    assert claude_limits.load("not json") is None
