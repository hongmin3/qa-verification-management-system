"""자동 분석이 꺼진 동안의 변경을 밀린 변경으로 저장. Validates: REQ-ALMCHG-005."""

from __future__ import annotations

import json

import pytest

from tests.daily_qa_fixtures import issue_item, make_tc_workbook, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness


def set_flags(store, cfg, enabled=True, validation_period=True):
    store.set_state(cfg.state_key("automation_policy"), json.dumps({"enabled": enabled, "validation_period": validation_period}))


@pytest.fixture
def env(tmp_path):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시")])
    h = Harness(tmp_path, tc_paths=[workbook])
    h.srs = [srs_item("VP-10", "01-01", "목록")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    return h


def status_of(h, run_id, entity_id):
    return {e["entity_id"]: e["analysis_status"] for e in h.events(run_id)}.get(entity_id)


def new_issue_after_baseline(h, mode, trigger, extra=None):
    h.run(DAY1)                                         # 기준 기록
    set_flags(h.store, h.cfg, enabled=mode != "off", validation_period=mode != "weekly")
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open"))
    if extra:
        extra(h)
    return h.run(DAY2, trigger=trigger)


# Validates: REQ-ALMCHG-005
def test_collect_while_off_stores_deferred(env):
    outcome = new_issue_after_baseline(env, "off", "collect")
    assert status_of(env, outcome["run_id"], "VP-200") == "deferred"
    assert outcome["summary"]["deferred_events"] >= 1
    assert env.calls == []


# Validates: REQ-ALMCHG-005
def test_not_required_events_stay_not_required(env):
    def change_status(h):
        h.issues[0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="closed")
    outcome = new_issue_after_baseline(env, "off", "collect", change_status)
    statuses = {e["entity_id"]: e["analysis_status"] for e in env.events(outcome["run_id"])}
    not_required = [e for e in env.events(outcome["run_id"]) if not e["analysis_required"]]
    assert all(e["analysis_status"] == "not_required" for e in not_required)
    assert statuses["VP-200"] == "deferred"


# Validates: REQ-ALMCHG-005
def test_weekly_collect_keeps_pending(env):
    outcome = new_issue_after_baseline(env, "weekly", "collect")
    assert status_of(env, outcome["run_id"], "VP-200") == "pending"
    assert outcome["summary"]["deferred_events"] == 0


# Validates: REQ-ALMCHG-005
def test_manual_run_while_off_keeps_pending(env):
    outcome = new_issue_after_baseline(env, "off", "manual")
    assert status_of(env, outcome["run_id"], "VP-200") in ("pending", "done")     # 사람이 누른 실행은 바로 분석한다
    assert outcome["summary"]["deferred_events"] == 0


# Validates: REQ-ALMCHG-005
def test_auto_analysis_skips_deferred(env):
    deferred_run = new_issue_after_baseline(env, "off", "collect")
    assert env.calls == []
    set_flags(env.store, env.cfg, enabled=True)
    env.run(DAY3, trigger="scheduled")                  # 새 변경 없음
    assert env.calls == []
    assert [e["entity_id"] for e in env.store.list_events(product=env.cfg.slug, statuses=("deferred",))] == ["VP-200"]
    # 켠 뒤에 저장한 새 변경은 분석 대상이다
    env.issues.append(issue_item("VP-300", "2026-10-01T09:00:00Z", ["VP-10"], review="", status="open"))
    later = env.run(DAY3.replace(day=DAY3.day + 1), trigger="scheduled")
    assert status_of(env, later["run_id"], "VP-300") != "deferred"
    assert env.calls and all("VP-200" not in targets for _, targets in env.calls)
    assert status_of(env, deferred_run["run_id"], "VP-200") == "deferred"
