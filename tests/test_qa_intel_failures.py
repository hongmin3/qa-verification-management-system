"""QA Intelligence 실행 예외 시나리오 (SPEC specs/qa-intelligence.md, SPEC.md REQ-DAILY-006).

수집·저장·AI 가 도중에 실패하거나 미뤄질 때 기준 스냅샷과 이벤트가 어떻게 남는지 본다.
가짜 Polarion·가짜 Claude 만 쓴다 (`tests/qa_intel_harness.py`).

Validates: REQ-QAINTEL-004, REQ-QAINTEL-006, REQ-QAINTEL-007, REQ-QAINTEL-009, REQ-QAINTEL-010,
REQ-QAINTEL-025, REQ-DAILY-006
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.daily_qa import claude_limits, snapshots
from app.modules.daily_qa.agent_runner import RunnerOutcome
from app.modules.daily_qa.pipeline import (
    LEGACY_STATE_B_PENDING,
    STATE_CATCHUP_AFTER,
    STATE_CLAUDE_LIMIT,
)
from app.modules.daily_qa.rules import RulesState
from app.modules.daily_qa.settings import IntelligenceSettings
from tests.daily_qa_fixtures import comment, issue_item, make_tc_workbook, rules_ok, srs_item
from tests.qa_intel_harness import DAY1, DAY2, DAY3, Harness

DAY4 = DAY3 + timedelta(days=1)   # 금요일 (월요일 주간 점검을 피한다)
LONG_COMMENT = "원인은 캐시 초기화 누락으로 확인되었습니다. 재시작 뒤에도 재현됩니다."


def base_issues() -> list[dict]:
    return [issue_item(f"VP-{number}", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open") for number in (100, 101, 102)]


def new_issue(item_id: str) -> dict:
    return issue_item(item_id, "2026-09-29T09:00:00Z", [], review="", status="open")


def harness(tmp_path, **settings) -> Harness:
    h = Harness(tmp_path, **settings)
    h.srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색")]
    h.issues = base_issues()
    return h


def rules_blocked(tmp_path) -> RulesState:
    ok = rules_ok(tmp_path)
    return RulesState(False, "1.10", "QA 규칙 판이 지원 판과 다릅니다", ok.guide_path, None)


def failing(task, run):
    return RunnerOutcome(False, 1, 0.0, "exit=1 boom")


def limited(text: str, now: datetime, token: str = ""):
    info_now = now

    def produce(task, run):
        return RunnerOutcome(False, 1, 0.0, text, limit=claude_limits.classify(text, info_now, token=token))

    return produce


def issue_snapshot(h: Harness, day: str) -> dict[str, dict]:
    path = h.cfg.issue_snapshot_dir / f"{day}.json"
    return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))["items"]}


# -- 1. 스냅샷 쓰기 실패 (REQ-QAINTEL-009) ----------------------------------------------


def test_issue_snapshot_write_error_records_no_events_and_keeps_baseline(tmp_path, monkeypatch):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    original = snapshots.SnapshotStore.save

    def save(self, kind, run_date, items, collected_at=""):
        if kind == snapshots.KIND_ISSUES:
            raise OSError("disk full")
        return original(self, kind, run_date, items, collected_at)

    monkeypatch.setattr(snapshots.SnapshotStore, "save", save)
    outcome = h.run(DAY2)
    assert outcome["stages"]["collect_issues"]["status"] == "failed"
    assert outcome["stages"]["collect_issues"]["note"] == "스냅샷을 저장하지 못했습니다: OSError"
    assert not (h.cfg.issue_snapshot_dir / "2026-09-30.json").exists()
    assert h.events() == []
    assert h.calls == []
    run_dir = h.cfg.output_dir / outcome["run_id"]
    assert json.loads((run_dir / "change_events.json").read_text(encoding="utf-8")) == []

    monkeypatch.setattr(snapshots.SnapshotStore, "save", original)
    later = h.run(DAY3)   # 기준을 옮기지 않았으니 같은 변경을 다시 찾는다
    assert [(event["entity_id"], event["event_type"]) for event in h.events(later["run_id"])] == [("VP-200", "ISSUE_CREATED")]


# -- 2. 이벤트 저장 실패 → 오늘 스냅샷 되돌림 (REQ-QAINTEL-006 1번) ------------------------


def test_event_save_failure_discards_todays_snapshot(tmp_path, monkeypatch):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(h.store, "add_events", boom)
    outcome = h.run(DAY2)
    stage = outcome["stages"]["collect_issues"]
    assert stage["status"] == "failed" and "변경 이벤트를 저장하지 못해 스냅샷을 되돌렸습니다" in stage["note"]
    assert not (h.cfg.issue_snapshot_dir / "2026-09-30.json").exists()
    assert not (h.cfg.srs_snapshot_dir / "2026-09-30.json").exists()
    assert (h.cfg.issue_snapshot_dir / "2026-09-29.json").is_file()
    assert h.calls == []

    monkeypatch.undo()
    later = h.run(DAY3)
    assert [(event["entity_id"], event["event_type"]) for event in h.events(later["run_id"])] == [("VP-200", "ISSUE_CREATED")]


# -- 3. 한 이슈의 댓글 읽기 실패 (REQ-QAINTEL-004 4번, REQ-QAINTEL-009 참고) ------------------


def _comment_setup(tmp_path) -> Harness:
    h = harness(tmp_path)
    h.issues[0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"])
    h.issues[1] = issue_item("VP-101", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["d1"])
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁")], "VP-101": [comment("d1", "처음 댓글입니다 확인 부탁")]}
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2"])
    h.issues[1] = issue_item("VP-101", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["d1", "d2"])
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁"), comment("c2", LONG_COMMENT)],
                  "VP-101": [comment("d1", "처음 댓글입니다 확인 부탁"), comment("d2", LONG_COMMENT)]}
    h.fail_comments.add("VP-100")
    return h


def test_comment_read_failure_carries_yesterdays_comments_forward(tmp_path):
    h = _comment_setup(tmp_path)
    outcome = h.run(DAY2)
    stage = outcome["stages"]["collect_issues"]
    assert stage["status"] == "ok" and "댓글 읽기 실패 1건" in stage["note"]
    assert stage["counts"]["comments_failed"] == 1
    events = h.events(outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-101", "ISSUE_COMMENT_ADDED")]
    snapshot = issue_snapshot(h, "2026-09-30")
    assert [item["id"] for item in snapshot["VP-100"]["comments"]] == ["c1"]   # 어제 댓글을 옮겨 적는다
    assert snapshot["VP-100"]["comments_error"] is True
    assert (h.cfg.issue_snapshot_dir / "2026-09-30.json").is_file()   # 수집 실패가 아니라 기준은 옮긴다


def test_comment_read_failure_is_retried_and_detected_next_run(tmp_path):
    h = _comment_setup(tmp_path)
    h.run(DAY2)
    h.fail_comments.clear()
    outcome = h.run(DAY3)   # Polarion 값은 어제와 같다
    events = h.events(outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-100", "ISSUE_COMMENT_ADDED")]
    assert [item["id"] for item in events[0]["after"]["comments"]] == ["c2"]


def test_comment_read_failing_two_days_is_detected_on_the_first_successful_read(tmp_path):
    """이틀 연속 실패해도 옮겨 적는 댓글은 마지막으로 읽은 날 것이고, 읽히는 날 새 댓글이 한 번 잡힌다."""
    h = _comment_setup(tmp_path)
    h.run(DAY2)
    h.run(DAY3)                                    # 또 실패
    assert [item["id"] for item in issue_snapshot(h, "2026-10-01")["VP-100"]["comments"]] == ["c1"]
    assert h.events(entity_id="VP-100") == []
    h.fail_comments.clear()
    outcome = h.run(DAY4)
    events = h.events(outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-100", "ISSUE_COMMENT_ADDED")]
    assert "comments_error" not in issue_snapshot(h, "2026-10-02")["VP-100"]
    later = h.run(DAY4 + timedelta(days=1))        # 다 읽은 뒤에는 같은 댓글을 다시 이벤트로 만들지 않는다
    assert h.events(later["run_id"]) == []


def test_comment_read_failure_without_comment_ids_is_retried_by_updated_time(tmp_path):
    """이슈 응답에 댓글 번호가 없으면 수정 시각으로 읽을지 정한다. 실패한 날의 수정 시각은 기준이 아니다."""
    h = harness(tmp_path)
    h.issues[0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")
    h.run(DAY1)                                    # 기준: 댓글 번호도 본문도 없다
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open")
    h.comments = {"VP-100": [comment("c1", "처음 댓글입니다 확인 부탁", created="2026-09-29T09:00:00Z")]}
    h.run(DAY2)
    h.issues[0] = issue_item("VP-100", "2026-09-30T09:00:00Z", ["VP-10"], review="", status="open")
    h.comments["VP-100"].append(comment("c2", LONG_COMMENT, created="2026-09-30T09:00:00Z"))
    h.fail_comments.add("VP-100")
    failed = h.run(DAY3)
    assert all(event["event_type"] != "ISSUE_COMMENT_ADDED" for event in h.events(failed["run_id"]))
    h.fail_comments.clear()
    outcome = h.run(DAY4)                          # 수정 시각은 어제와 같다
    events = [event for event in h.events(outcome["run_id"]) if event["entity_id"] == "VP-100"]
    assert [event["event_type"] for event in events] == ["ISSUE_COMMENT_ADDED"]
    assert [item["id"] for item in events[0]["after"]["comments"]] == ["c2"]


# -- 4·5. 재시도 상한과 대상 사라짐 → abandoned (REQ-QAINTEL-006 3·4번) ------------------------


def test_event_is_abandoned_after_max_attempts(tmp_path):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.outcome_override = failing
    expected = [(DAY2, "failed", 1), (DAY3, "failed", 2), (DAY4, "abandoned", 3)]
    for day, status, attempts in expected:
        h.run(day)
        event = h.events(entity_id="VP-200")[0]
        assert (event["analysis_status"], event["attempts"]) == (status, attempts)
    calls = len(h.calls)
    h.outcome_override = None
    h.run(DAY4 + timedelta(hours=1))
    assert len(h.calls) == calls   # 포기한 이벤트는 다시 돌리지 않는다
    assert h.events(entity_id="VP-200")[0]["analysis_status"] == "abandoned"


def test_event_max_attempts_setting_is_respected(tmp_path):
    h = harness(tmp_path, intelligence=IntelligenceSettings(event_max_attempts=1))
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.outcome_override = failing
    h.run(DAY2)
    event = h.events(entity_id="VP-200")[0]
    assert (event["analysis_status"], event["attempts"]) == ("abandoned", 1)


def test_pending_event_is_abandoned_when_target_disappears(tmp_path):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.run(DAY2, rules_state=rules_blocked(tmp_path))   # 규칙 판 불일치 → pending 으로 남는다
    assert h.events(entity_id="VP-200")[0]["analysis_status"] == "pending"
    h.issues.pop()   # VP-200 이 오늘 조회되지 않는다
    outcome = h.run(DAY3)
    created = h.events(entity_id="VP-200", event_types=("ISSUE_CREATED",))[0]
    assert created["analysis_status"] == "abandoned" and created["attempts"] == 0
    assert "스냅샷에 없습니다" in created["last_error"]
    assert h.calls == []
    assert outcome["stages"]["NEW_ISSUE"]["status"] == "skipped"
    assert [event["event_type"] for event in h.events(outcome["run_id"])] == ["ISSUE_REMOVED"]


# -- 6·7. 인증 실패·주간 한도 (REQ-QAINTEL-025) ---------------------------------------------


def test_auth_failure_blocks_ai_until_token_changes(tmp_path):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.outcome_override = limited("OAuth token has expired. Please run /login", DAY2, token=h.cfg.claude_token)
    outcome = h.run(DAY2)
    assert outcome["summary"]["claude_limit"]["kind"] == claude_limits.KIND_AUTH
    assert len(h.calls) == 1
    event = h.events(entity_id="VP-200")[0]
    assert (event["analysis_status"], event["attempts"]) == ("pending", 0)
    stored = h.store.get_state(h.cfg.state_key(STATE_CLAUDE_LIMIT))
    assert stored and h.cfg.claude_token not in stored   # 토큰 원문은 남기지 않는다
    assert not h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER))

    h.outcome_override = None
    blocked = h.run(DAY3)   # 초기화 시각이 없다. 같은 토큰이면 며칠이 지나도 막는다
    assert len(h.calls) == 1
    assert blocked["stages"]["NEW_ISSUE"]["status"] == "limit"
    assert blocked["stages"]["preflight"]["status"] == "partial"

    h.cfg = replace(h.cfg, claude_token="tok-renewed-456")
    resumed = h.run(DAY3 + timedelta(hours=1))
    assert h.calls[-1] == ("qa-new-issue-analysis", ["VP-200"])
    assert h.events(entity_id="VP-200")[0]["analysis_status"] == "done"
    assert resumed["stages"]["NEW_ISSUE"]["status"] == "ok"


def test_weekly_limit_schedules_no_catchup_and_resumes_after_reset(tmp_path):
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.outcome_override = limited("Weekly limit reached ∙ resets Oct 6, 9am (Asia/Seoul)", DAY2)
    outcome = h.run(DAY2)
    assert outcome["summary"]["claude_limit"]["kind"] == claude_limits.KIND_WEEKLY
    assert outcome["summary"]["claude_limit"]["reset_kst"] == "2026-10-06 09:00"
    assert h.store.get_state(h.cfg.state_key(STATE_CLAUDE_LIMIT))
    assert not h.store.get_state(h.cfg.state_key(STATE_CATCHUP_AFTER))   # 주간 한도는 다시 돌지 않는다
    assert (h.events(entity_id="VP-200")[0]["analysis_status"], h.events(entity_id="VP-200")[0]["attempts"]) == ("pending", 0)

    h.outcome_override = None
    h.issues.append(new_issue("VP-201"))
    during = h.run(DAY3)   # 초기화 전: 변경 감지는 계속, AI 는 부르지 않는다
    assert len(h.calls) == 1
    assert [(event["entity_id"], event["event_type"]) for event in h.events(during["run_id"])] == [("VP-201", "ISSUE_CREATED")]
    assert during["stages"]["NEW_ISSUE"]["status"] == "limit"

    after_reset = datetime(2026, 10, 7, 7, 30, tzinfo=timezone.utc)
    h.run(after_reset)
    assert sorted(target for _, targets in h.calls[1:] for target in targets) == ["VP-200", "VP-201"]
    assert {event["analysis_status"] for event in h.events(event_types=("ISSUE_CREATED",))} == {"done"}


# -- 8. 작업 상한 초과 (REQ-QAINTEL-010 결과) -----------------------------------------------


def test_task_cap_leaves_extra_events_pending_for_next_runs(tmp_path):
    h = harness(tmp_path, max_tasks_per_run=1, batch_size=1)
    h.run(DAY1)
    h.issues += [new_issue("VP-200"), new_issue("VP-201"), new_issue("VP-202")]
    outcome = h.run(DAY2)
    assert len(h.calls) == 1
    assert "상한 초과로 2개 묶음 다음 실행으로 미룸" in outcome["stages"]["NEW_ISSUE"]["note"]
    statuses = sorted(event["analysis_status"] for event in h.events(event_types=("ISSUE_CREATED",)))
    assert statuses == ["done", "pending", "pending"]
    assert all(event["attempts"] == 0 for event in h.events(event_types=("ISSUE_CREATED",)))
    h.run(DAY3)
    h.run(DAY4)
    assert sorted(target for _, targets in h.calls for target in targets) == ["VP-200", "VP-201", "VP-202"]
    assert {event["analysis_status"] for event in h.events(event_types=("ISSUE_CREATED",))} == {"done"}


# -- 9. 개편 전 미뤄 둔 사양 변경 (REQ-QAINTEL-006 참고) -------------------------------------


def test_legacy_spec_change_pending_becomes_pending_srs_events(tmp_path):
    h = harness(tmp_path)
    h.run(DAY1)
    legacy = [
        {"change": "modified", "srs": {"id": "VP-10", "old_id": "01-01", "title": "목록"},
         "before": {"text": "옛 본문"}, "after": {"text": "본문"}, "fields": ["text"]},
        {"change": "added", "srs": {"id": "VP-11", "old_id": "01-02", "title": "검색"}, "after": {"text": "본문"}},
        {"change": "modified", "srs": {}},   # 번호 없는 항목은 버린다
    ]
    h.store.set_state(LEGACY_STATE_B_PENDING, json.dumps(legacy, ensure_ascii=False))
    outcome = h.run(DAY2, rules_state=rules_blocked(tmp_path))
    events = h.events(outcome["run_id"])
    assert sorted((event["entity_id"], event["event_type"], event["analysis_status"]) for event in events) == [
        ("VP-10", "SRS_UPDATED", "pending"), ("VP-11", "SRS_CREATED", "pending")]
    assert all(event["analyses"] == ["SPEC_COVERAGE"] for event in events)
    assert json.loads(h.store.get_state(LEGACY_STATE_B_PENDING)) == []
    h.run(DAY3)   # 규칙이 맞으면 다음 실행이 분석한다
    assert sorted(target for skill, targets in h.calls for target in targets) == ["VP-10", "VP-11"]
    assert {event["analysis_status"] for event in h.events(outcome["run_id"])} == {"done"}


# -- 10. 매뉴얼 누락 후보 점검은 사람이 요청할 때만 돈다 (REQ-QAINTEL-034, REQ-DAILY-006) -----


def test_manual_check_never_runs_automatically_only_on_request(tmp_path):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시")])
    h = harness(tmp_path, tc_paths=[workbook], manuals={"VXvue_Manual.txt": "목록 화면 설명"})
    h.run(DAY1)
    quiet = h.run(DAY2)
    assert quiet["status"] == "NO_CHANGE" and quiet["stages"]["F"]["status"] == "not_due"
    assert h.store.get_state("vxvue:manual_check_due") == "" and h.calls == []

    h.srs[0] = srs_item("VP-10", "01-01", "목록", text="표시 항목은 4개다.")
    changed = h.run(DAY3, force_weekly=True)                 # 변경 + 주간 요일이어도 자동으로는 돌지 않는다
    assert "qa-manual-completeness" not in h.skills_called()
    assert changed["stages"]["F"]["status"] == "not_due" and "요청할 때만" in changed["stages"]["F"]["note"]
    requested = h.run(DAY3.replace(hour=12), on_demand="manual-check")
    assert h.skills_called()[-1] == "qa-manual-completeness" and requested["stages"]["F"]["status"] == "ok"


# -- 11. 댓글 읽기 상한 (REQ-QAINTEL-004 3번) -----------------------------------------------


def _fetch_limit_setup(tmp_path) -> Harness:
    h = harness(tmp_path, intelligence=IntelligenceSettings(comment_fetch_limit=1))
    h.issues[0] = issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=[])
    h.issues[1] = issue_item("VP-101", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=[])
    h.issues[2] = issue_item("VP-102", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=[])
    h.run(DAY1)
    h.issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["a1"])
    h.issues[1] = issue_item("VP-101", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["b1"])
    h.comments = {"VP-100": [comment("a1", LONG_COMMENT)], "VP-101": [comment("b1", LONG_COMMENT)]}
    return h


def test_comment_fetch_limit_defers_excess_issues(tmp_path):
    h = _fetch_limit_setup(tmp_path)
    outcome = h.run(DAY2)
    stage = outcome["stages"]["collect_issues"]
    assert stage["counts"]["comments_fetched"] == 1
    assert "댓글 읽기 상한으로 1건 다음 실행으로 미룸" in stage["note"]
    events = h.events(outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-100", "ISSUE_COMMENT_ADDED")]


def test_comment_fetch_limit_deferred_issue_is_read_next_run(tmp_path):
    h = _fetch_limit_setup(tmp_path)
    h.run(DAY2)
    outcome = h.run(DAY3)   # Polarion 값은 어제와 같다
    events = h.events(outcome["run_id"])
    assert [(event["entity_id"], event["event_type"]) for event in events] == [("VP-101", "ISSUE_COMMENT_ADDED")]
    assert [item["id"] for item in events[0]["after"]["comments"]] == ["b1"]


def test_comment_fetch_limit_deferred_issue_is_read_once(tmp_path):
    """미뤘다 읽은 댓글은 그다음 실행에서 다시 새 댓글로 잡지 않는다 (REQ-QAINTEL-004 3번)."""
    h = _fetch_limit_setup(tmp_path)
    h.run(DAY2)
    assert issue_snapshot(h, "2026-09-30")["VP-101"].get("comments_deferred") is True
    h.run(DAY3)
    assert "comments_deferred" not in issue_snapshot(h, "2026-10-01")["VP-101"]
    later = h.run(DAY4)
    assert h.events(later["run_id"]) == []
