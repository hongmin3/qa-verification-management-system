"""이슈 기록이 없는 기간의 현재 상태 기준 점검 (REQ-QAINTEL-030, TEST-QAINTEL-017).

가짜 Polarion·가짜 Claude(`Harness`)로 돌린다. 실제 Polarion·Claude 를 부르지 않는다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import app.modules.daily_qa.pipeline as pipeline
from app.modules.daily_qa import issue_audit, snapshots
from app.modules.daily_qa.agent_runner import RunnerOutcome
from app.modules.daily_qa.change_events import ISSUE_AUDIT, ISSUE_AUDIT_TARGET
from app.modules.daily_qa.srs_snapshot import diff_snapshots
from tests.daily_qa_fixtures import issue_item, make_tc_workbook, srs_item
from tests.qa_intel_harness import DAY1, DAY2, Harness

SINCE = date(2026, 9, 20)
UNTIL = date(2026, 9, 30)


def _srs(item_id: str, text: str, updated: str = "2026-09-25T00:00:00Z", title: str = "") -> dict:
    return {"id": item_id, "old_id": "", "title": title or f"{item_id} 제목", "status": "draft", "text": text, "updated": updated}


def _issue(item_id: str, rd: str, linked: list[str], *, created: str = "2026-09-01T00:00:00Z", updated: str = "2026-09-26T00:00:00Z",
           title: str = "", description: str = "", status: str = "verified") -> dict:
    return {"id": item_id, "title": title or f"{item_id} 제목", "status": status, "rd_result": rd, "created": created,
            "updated": updated, "linked_ids": linked, "description": description, "reproduction_step": "", "comments": []}


def _plan():
    before = [_srs("VP-11", "저장 후 재진입 시 설정을 유지한다.", title="설정 저장"), _srs("VP-12", "목록은 이름순이다.", updated="2026-09-02T00:00:00Z")]
    after = [_srs("VP-11", "저장 후 재시작해야 적용된다.", title="설정 저장"), before[1]]
    issues = [
        _issue("VP-101", "FIXED", ["VP-11"]),
        _issue("VP-102", "SPEC", ["VP-11"]),
        _issue("VP-103", "PENDING", ["VP-11"], status="in_review"),
        _issue("VP-104", "SPEC", ["VP-12"]),                                   # SRS 는 9/2 이후 그대로, 이슈는 9/26 수정
        _issue("VP-105", "", [], created="2026-09-22T00:00:00Z"),            # 기간 안 신규, 연결 없음
        _issue("VP-106", "FIXED", ["VP-12"]),                                  # 연결 SRS 그대로 · 수정 완료 → 대상 아님
        _issue("VP-107", "FIXED", [], title="설정 저장 오류"),                 # 제목만 비슷 → 대상 아님
        _issue("VP-108", "FIXED", [], description="VP-11 의 저장 동작이 다르다"),  # 본문에 정확한 번호 → A
    ]
    return issue_audit.classify(issues, after, diff_snapshots(before, after), SINCE, UNTIL)


def test_classify_follows_the_category_table():
    plan = _plan()
    by_issue = {event.entity_id: event for event in plan.events}
    assert {key: event.after["category"] for key, event in by_issue.items()} == {
        "VP-101": "A-수정", "VP-102": "A-사양", "VP-103": "A-기타", "VP-104": "B", "VP-105": "D", "VP-108": "A-수정"}
    assert plan.not_target == 2                                                # VP-106, VP-107
    assert issue_audit.SIGNAL_SRS_STALE in by_issue["VP-104"].after["signals"]
    assert by_issue["VP-103"].after["reference_only"] is True                  # in_review 는 참고만 (지침 §6)
    change = by_issue["VP-101"].after["srs"][0]
    assert change["changed"] and change["added_sentences"] and change["removed_sentences"]
    assert all(event.event_type == ISSUE_AUDIT_TARGET and event.analyses == [ISSUE_AUDIT] for event in plan.events)
    summary = plan.summary(SINCE, UNTIL)
    assert summary["issues_seen"] == 8 and summary["targets"] == 6 and summary["created_in_period"] == 1
    assert "현재 상태 기준 점검으로 대신했습니다" in plan.note()


def test_same_state_makes_the_same_fingerprint():
    first, second = _plan(), _plan()
    assert [event.fingerprint for event in first.events] == [event.fingerprint for event in second.events]


# -- 실행 (가짜 Polarion · 가짜 Claude) ------------------------------------------------


def _world(tmp_path):
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-11", "설정 저장"), ("TC_2", "VP-12", "목록")])
    h = Harness(tmp_path, tc_paths=[workbook])
    store = snapshots.for_settings(h.cfg)
    store.save(snapshots.KIND_SRS, DAY1.date().isoformat(), [
        {"id": "VP-11", "old_id": "01-01", "title": "설정 저장", "status": "draft", "text": "저장 후 재진입 시 설정을 유지한다", "updated": ""},
        {"id": "VP-12", "old_id": "01-02", "title": "목록", "status": "draft", "text": "목록은 이름순이다", "updated": ""},
    ])
    h.srs = [srs_item("VP-11", "01-01", "설정 저장", text="저장 후 재시작해야 적용된다"), srs_item("VP-12", "01-02", "목록", text="목록은 이름순이다")]
    h.issues = [issue_item("VP-101", "2026-09-29T00:00:00Z", ["VP-11"], review="lab_fixed"),
                issue_item("VP-102", "2026-09-29T00:00:00Z", ["VP-11"], review="lab_inspec"),
                issue_item("VP-104", "2026-09-29T00:00:00Z", ["VP-12"], review="lab_inspec")]
    for target in ("VP-101", "VP-102", "VP-104"):
        h.script[target] = {"verdict": "CONSISTENT_WITH_SPEC", "evidence": [{"source_type": "srs", "location": "VP-11 / 바뀐 문장", "validity": "Current"}]}
    h.script["VP-101"]["sections"] = {"tc_impact": {"decision": "수정 필수", "tc_ids": ["TC_1", "TC_X"]}}
    return h


def test_audit_run_groups_by_srs_and_sends_srs_once(tmp_path):
    h = _world(tmp_path)
    outcome = h.run(DAY2, since=DAY1.date(), issue_audit=True)
    audit_payloads = [payload for payload in h.payloads if payload.get("analysis_type") == ISSUE_AUDIT]
    assert len(audit_payloads) == 2                                            # VP-1 묶음, VP-2 묶음
    vp1 = next(payload for payload in audit_payloads if payload["srs"][0]["id"] == "VP-11")
    vp2 = next(payload for payload in audit_payloads if payload["srs"][0]["id"] == "VP-12")
    assert sorted(item["target"] for item in vp1["items"]) == ["VP-101", "VP-102"]
    assert [entry["id"] for entry in vp1["srs"]] == ["VP-11"]                  # SRS 내용은 작업에 한 번
    assert all("_srs" not in item and "srs" not in item for item in vp1["items"])
    assert vp1["tcs"] and not vp2["tcs"]                                       # A-수정이 있는 작업에만 TC 후보
    assert vp1["srs"][0]["added_sentences"] and vp1["srs"][0]["excerpts"]
    assert outcome["summary"]["issue_audit"]["targets"] == 3
    assert "현재 상태 기준 점검" in outcome["stages"]["events"]["note"]


def test_audit_findings_are_validated_and_marked_with_basis(tmp_path):
    h = _world(tmp_path)
    outcome = h.run(DAY2, since=DAY1.date(), issue_audit=True)
    findings = {item["subject"]: item for item in h.store.list_findings(run_id=outcome["run_id"])}
    tc_impact = findings["VP-101"]["sections"]["tc_impact"]
    assert tc_impact["decision"] == "수정 필수" and tc_impact["tc_ids"] == ["TC_1"]   # 입력에 없는 TC_X 는 뺀다
    assert all(item["sections"]["basis"] == issue_audit.BASIS_TAG for item in findings.values())
    events = h.events(event_types=(ISSUE_AUDIT_TARGET,))
    assert {event["analysis_status"] for event in events} == {"done"}


def test_bad_tc_impact_decision_is_dropped(tmp_path):
    h = _world(tmp_path)
    h.script["VP-101"]["sections"] = {"tc_impact": {"decision": "삭제", "tc_ids": ["TC_1"]}}
    outcome = h.run(DAY2, since=DAY1.date(), issue_audit=True)
    finding = h.store.list_findings(run_id=outcome["run_id"], analysis_type=ISSUE_AUDIT)
    vp101 = next(item for item in finding if item["subject"] == "VP-101")
    assert vp101["sections"]["tc_impact"] == {}
    assert any(entry["reason"] == "허용되지 않은 TC 영향 판정" for entry in vp101["sections"]["validation"]["removed"])


def test_rerunning_the_same_period_does_not_call_claude_again(tmp_path):
    h = _world(tmp_path)
    h.run(DAY2, since=DAY1.date(), issue_audit=True)
    calls = len(h.calls)
    again = h.run(DAY2 + timedelta(hours=1), since=DAY1.date(), issue_audit=True)
    assert len(h.calls) == calls
    assert again["summary"]["claude_calls"] == 0
    assert "이미 점검한 상태 3건" in again["stages"]["events"]["note"]


def test_limit_leaves_audit_pending_and_next_daily_run_continues(tmp_path):
    from app.modules.daily_qa import claude_limits

    h = _world(tmp_path)
    limit = claude_limits.LimitInfo(claude_limits.KIND_SESSION, "", "Claude 세션 한도")
    h.outcome_override = lambda task, run: RunnerOutcome(False, 1, 0.0, "limit", limit=limit)
    first = h.run(DAY2, since=DAY1.date(), issue_audit=True)
    assert first["summary"]["issue_audit_remaining"] == 3
    h.outcome_override = None
    h.store.set_state(h.cfg.state_key(pipeline.STATE_CLAUDE_LIMIT), "")
    h.run(DAY2 + timedelta(hours=6))                                           # 보통 실행이 이어서 점검한다
    assert {event["analysis_status"] for event in h.events(event_types=(ISSUE_AUDIT_TARGET,))} == {"done"}


def test_audit_tasks_use_the_audit_model(tmp_path, monkeypatch):
    h = _world(tmp_path)
    h.cfg = replace(h.cfg, intelligence=replace(h.cfg.intelligence, audit_model="audit-light-model"))
    used: list[tuple[str, str]] = []

    class Recording:
        def __init__(self, command, token, timeout, model=""):
            self.model = model

        def run(self, task, run):
            used.append((task.task_id, self.model))
            return h._produce(task, run)

    monkeypatch.setattr(pipeline, "ClaudeRunner", Recording)
    h.run(DAY2, since=DAY1.date(), issue_audit=True, runner=None)
    audit = [model for task_id, model in used if task_id.startswith("AUD-")]
    assert audit and set(audit) == {"audit-light-model"}
