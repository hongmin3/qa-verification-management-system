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
    sent_before = len(env.summary_sent)         # 기준 기록 실행의 메일은 따로 센다
    set_flags(env.store, env.cfg, enabled=False)
    env.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    outcome = env.run(DAY2, trigger="collect")
    assert len(env.summary_sent) == sent_before
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


# Validates: REQ-ALMCHG-001
def test_savings_collect_run_ignores_unreadable_verification_sheet(tmp_path, monkeypatch):
    """절약 모드의 수집만 한 실행은 AI 전용인 검증 시트 확인에 실패해도 부분 실패로 끝나지 않는다."""
    from dataclasses import replace
    from app.modules.daily_qa import workflow_settings

    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    h.run(DAY1, trigger="collect")
    monkeypatch.setattr(workflow_settings, "versions", lambda paths: ({}, ["검증 시트를 읽지 못했습니다."]))
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    outcome = h.run(DAY2, trigger="collect")
    assert outcome["status"] != "PARTIAL", {k: (v["status"], v.get("note")) for k, v in outcome["stages"].items()}
    assert outcome["stages"]["preflight"]["status"] == "ok" and h.calls == []


# Validates: REQ-ALMCHG-001
def test_collect_run_is_labeled_as_collection_not_analysis_ready(env):
    from app.modules.qa_agent import run_view

    first = env.run(DAY1, trigger="collect")
    assert env.store.get_run(first["run_id"])["status_label"] == "기준 저장"
    set_flags(env.store, env.cfg, enabled=False)
    env.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    outcome = env.run(DAY2, trigger="collect")
    run = env.store.get_run(outcome["run_id"])
    assert run["status_label"] == "수집 완료"
    view = run_view.build(run, [], [], [])
    assert view["headline"] == "수집만 실행했습니다(자동 분석 꺼짐). AI 분석은 하지 않았습니다."
    assert "대상을 선택" not in view["headline"] and view["trigger"] == "수집만 실행"


# Validates: REQ-ALMCHG-001
def test_collect_run_without_changes_is_labeled_no_change(env):
    env.run(DAY1)
    set_flags(env.store, env.cfg, enabled=False)
    outcome = env.run(DAY2, trigger="collect")
    assert env.store.get_run(outcome["run_id"])["status_label"] == "변경사항 없음"


# Validates: REQ-ALMCHG-005
def test_run_view_trigger_labels_and_change_mail_line():
    from app.modules.qa_agent import run_view

    def view(trigger, mail):
        return run_view.build({"status": "SUCCESS", "summary": {"trigger": trigger, "change_mail": mail}}, [], [], [])

    assert view("deferred", None)["trigger"] == "밀린 변경 분석"
    assert view("prepare", None)["trigger"] == "대상 준비" and view("collect", None)["trigger"] == "수집만 실행"
    assert view("collect", None)["change_mail"] == ""
    assert view("collect", {"status": "sent"})["change_mail"] == "변경 리포트 메일: 보냄"
    note = "발송 2026-10-19 04:00 · 수신 hon***@vieworks.com · Message-ID <m1@example>"
    assert view("collect", {"status": "sent", "note": note})["change_mail"] == f"변경 리포트 메일: 보냄 · {note}"
    assert view("collect", {"status": "skipped", "reason": "변경이 없어 보내지 않았습니다."})["change_mail"] == "변경 리포트 메일: 보내지 않음(변경이 없어 보내지 않았습니다.)"
    assert view("collect", {"status": "failed", "reason": "smtp"})["change_mail"] == "변경 리포트 메일: 실패(smtp)"


def _savings_collect_env(tmp_path):
    from dataclasses import replace
    from tests.test_qa_workflow_settings import workbook

    h = Harness(tmp_path)
    h.cfg = replace(h.cfg, savings_enabled=True)
    path = workbook(tmp_path / "Checklist.xlsx", ["V1.1.0", "V2.0.0"])
    h.inputs.tc_paths = [path]
    h.inputs.checklist_template = path
    h.srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "상세")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-11"], review="", status="open")]
    h.run(DAY1, trigger="collect")                      # 기준 기록
    return h


# Validates: REQ-QAINTEL-028, REQ-ALMCHG-001
def test_savings_collect_still_produces_removed_srs_finding(tmp_path):
    """절약 모드의 수집만 한 실행도 TC가 가리키던 SRS가 사라지면 COV-removed 결과를 만든다(다음 실행은 이미 새 기록과 비교한다)."""
    h = _savings_collect_env(tmp_path)
    h.srs = [srs_item("VP-11", "01-02", "상세")]        # VP-10 삭제, TC 는 VP-10 을 가리킨다
    outcome = h.run(DAY2, trigger="collect")
    removed = [f for f in h.store.list_findings(run_id=outcome["run_id"]) if f["task_id"] == "COV-removed"]
    assert [f["subject"] for f in removed] == ["VP-10"]
    assert h.calls == [] and outcome["summary"]["claude_calls"] == 0


# Validates: REQ-QAINTEL-028, REQ-ALMCHG-001
def test_savings_collect_sheet_read_failure_is_not_blocking(tmp_path, monkeypatch):
    """검증 시트를 읽지 못해도 수집만 한 실행은 부분 실패가 아니고, tc_index 단계는 상태에 영향을 주지 않게 기록되며 기록·이벤트는 저장된다."""
    from app.modules.daily_qa import workflow_settings

    h = _savings_collect_env(tmp_path)
    monkeypatch.setattr(workflow_settings, "versions", lambda paths: ({}, ["검증 시트를 읽지 못했습니다."]))
    h.srs = [srs_item("VP-11", "01-02", "상세")]
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    outcome = h.run(DAY2, trigger="collect")
    assert outcome["status"] != "PARTIAL", {k: (v["status"], v.get("note")) for k, v in outcome["stages"].items()}
    assert outcome["stages"]["tc_index"]["status"] == "skipped" and "검증 시트를 읽지 못했습니다" in outcome["stages"]["tc_index"]["note"]
    assert outcome["stages"]["preflight"]["status"] == "ok" and h.calls == []
    assert "VP-200" in [e["entity_id"] for e in h.events(outcome["run_id"])]
    assert (h.cfg.issue_snapshot_dir / "2026-09-30.json").is_file()
