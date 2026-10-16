"""매 업무일 수집 실행. Validates: REQ-ALMCHG-001, REQ-QAINTEL-001, REQ-QAINTEL-035, REQ-QAINTEL-038."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.modules.daily_qa import holidays, scheduled_jobs
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import REPO_ROOT, issue_item, make_settings, make_tc_workbook, srs_item
from tests.qa_intel_harness import DAY1, DAY2, Harness

KST = holidays.KST
BASE_SCHEDULE = {"enabled": True, "day_of_week": "mon-fri", "time": "04:00", "skip_holidays": True,
                 "calendar": "kr", "extra": (), "run_missed": True}


def set_flags(store, cfg, enabled=True, validation_period=True):
    store.set_state(cfg.state_key("automation_policy"), json.dumps(
        {"enabled": enabled, "validation_period": validation_period}))


def trigger_of(args):
    return args[args.index("--trigger") + 1]


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


# Validates: REQ-ALMCHG-001
def test_policy_off_still_launches_collect(launcher):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    result = scheduled_jobs.launch_detached(today=datetime(2026, 10, 13, 4, 0, tzinfo=KST), popen=popen)
    assert result["status"] == "launched"
    assert trigger_of(launched[0]) == "collect"


# Validates: REQ-ALMCHG-001
def test_weekly_non_first_day_launches_collect(launcher):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, validation_period=False)
    scheduled_jobs.launch_detached(today=datetime(2026, 10, 13, 4, 0, tzinfo=KST), popen=popen)    # 화
    scheduled_jobs.launch_detached(today=datetime(2026, 10, 12, 4, 0, tzinfo=KST), popen=popen)    # 월
    assert [trigger_of(args) for args in launched] == ["collect", "scheduled"]


# Validates: REQ-ALMCHG-001
def test_daily_mode_keeps_scheduled(launcher):
    cfg, store, launched, popen = launcher
    scheduled_jobs.launch_detached(today=datetime(2026, 10, 13, 4, 0, tzinfo=KST), popen=popen)
    assert [trigger_of(args) for args in launched] == ["scheduled"]


# Validates: REQ-ALMCHG-001
def test_holiday_and_disabled_launch_nothing(launcher, monkeypatch):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    holiday = scheduled_jobs.launch_detached(today=datetime(2026, 10, 9, 4, 0, tzinfo=KST), popen=popen)   # 한글날
    assert holiday["status"] == "holiday"
    monkeypatch.setattr(scheduled_jobs, "schedule_settings", lambda settings=None: {**BASE_SCHEDULE, "enabled": False})
    disabled = scheduled_jobs.launch_detached(today=datetime(2026, 10, 13, 4, 0, tzinfo=KST), popen=popen)
    assert disabled["status"] == "schedule_disabled"
    assert launched == []


# Validates: REQ-ALMCHG-001
def test_catchup_trigger_keeps_daily_only_rule(launcher):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    result = scheduled_jobs.launch_detached(trigger="catchup", today=datetime(2026, 10, 13, 8, 0, tzinfo=KST), popen=popen)
    assert result["status"] == "policy_off" and launched == []


# Validates: REQ-ALMCHG-001
def test_missed_run_catches_up_collect_when_off(launcher):
    cfg, store, launched, popen = launcher
    set_flags(store, cfg, enabled=False)
    now = datetime(2026, 10, 13, 8, 0, tzinfo=KST)
    results = scheduled_jobs.run_missed_schedules(now, popen)
    assert [r["status"] for r in results] == ["launched"]
    assert [trigger_of(args) for args in launched] == ["collect"]
    assert store.get_state(cfg.state_key(scheduled_jobs.STATE_SCHEDULED_DATE)) == "2026-10-13"
    assert scheduled_jobs.run_missed_schedules(now, popen) == []


@pytest.fixture
def env(tmp_path):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시")])
    h = Harness(tmp_path, tc_paths=[workbook])
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    return h


def collect_run(h, mode="off"):
    h.run(DAY1)                               # 기준 기록
    set_flags(h.store, h.cfg, enabled=mode != "off", validation_period=mode != "weekly")
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    return h.run(DAY2, trigger="collect")


# Validates: REQ-ALMCHG-001
@pytest.mark.parametrize("mode,reason,note", [("off", "policy_off", "자동 분석 꺼짐"), ("weekly", "weekly_not_due", "주간 모드의 분석일 아님")])
def test_collect_run_calls_claude_zero_times(env, mode, reason, note):
    outcome = collect_run(env, mode)
    assert env.calls == [] and outcome["summary"]["claude_calls"] == 0
    assert [e["entity_id"] for e in env.events(outcome["run_id"])] == ["VP-200"]
    assert outcome["summary"]["run_kind"] == "collect" and outcome["summary"]["collection_only"] is True
    assert outcome["summary"]["collect_reason"] == reason
    assert outcome["stages"]["AI"]["note"] == f"수집만 실행 · AI 분석 없음 ({note})"


# Validates: REQ-ALMCHG-001
def test_collect_run_sends_no_ai_summary_mail(env):
    env.run(DAY1)
    sent_before = len(env.sent)         # 기준 기록 실행의 메일은 따로 센다
    set_flags(env.store, env.cfg, enabled=False)
    env.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    outcome = env.run(DAY2, trigger="collect")
    assert len(env.sent) == sent_before
    assert outcome["email"]["status"] == "skipped"


# Validates: REQ-ALMCHG-001
def test_collect_run_makes_no_prepare_plan(env):
    outcome = collect_run(env)
    assert not outcome["summary"]["plan_id"]


# Validates: REQ-ALMCHG-001
def test_analysis_run_reports_run_kind_analysis(env):
    outcome = env.run(DAY1)
    assert outcome["summary"]["run_kind"] == "analysis" and outcome["summary"]["collect_reason"] == ""


def _collect_after_baseline(h, mutate):
    h.run(DAY1)                               # 기준 기록
    set_flags(h.store, h.cfg, enabled=False)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    mutate(h)
    return h.run(DAY2, trigger="collect")


def _assert_collected(h, outcome):
    assert outcome["status"] in ("SUCCESS", "NO_CHANGE", "BASELINE"), {k: (v["status"], v.get("note")) for k, v in outcome["stages"].items()}
    assert outcome["stages"]["preflight"]["status"] == "ok"
    assert h.calls == [] and outcome["summary"]["claude_calls"] == 0
    assert [e["entity_id"] for e in h.events(outcome["run_id"])] == ["VP-200"]
    assert (h.cfg.issue_snapshot_dir / "2026-09-30.json").is_file()
    assert (h.cfg.srs_snapshot_dir / "2026-09-30.json").is_file()


# Validates: REQ-ALMCHG-001
def test_collect_run_under_active_claude_limit_is_not_partial(env):
    from datetime import timedelta
    from app.modules.daily_qa import claude_limits, pipeline

    def limit(h):
        info = claude_limits.LimitInfo(claude_limits.KIND_WEEKLY, (DAY2 + timedelta(days=2)).isoformat(), "주간 한도")
        h.store.set_state(h.cfg.state_key(pipeline.STATE_CLAUDE_LIMIT), claude_limits.dump(info))

    outcome = _collect_after_baseline(env, limit)
    _assert_collected(env, outcome)
    assert "claude_limit" not in outcome["summary"]


# Validates: REQ-ALMCHG-001
def test_collect_run_with_missing_qa_rules_is_not_partial(env):
    from app.modules.daily_qa.rules import RulesState

    env.run(DAY1)
    set_flags(env.store, env.cfg, enabled=False)
    env.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    outcome = env.run(DAY2, trigger="collect",
                      rules_state=RulesState(False, "1.17", "QA 규칙 파일이 없습니다.", None, None))
    _assert_collected(env, outcome)
