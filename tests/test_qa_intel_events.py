"""변경 감지와 분석 대상 고르기 (SPEC specs/qa-intelligence.md).

Validates: REQ-QAINTEL-003, REQ-QAINTEL-005, REQ-QAINTEL-010
"""

from __future__ import annotations

import pytest

from app.modules.daily_qa.change_events import (
    COMMENT,
    FIXED_ISSUE,
    NEW_ISSUE,
    SPEC_COVERAGE,
    SPEC_DECISION,
    ChangeEvent,
    detect_issue_events,
    detect_srs_events,
    group_targets,
    is_significant,
    new_comments,
)
from app.modules.daily_qa.srs_snapshot import diff_snapshots


def issue(item_id="VP-1", **values) -> dict:
    base = {"id": item_id, "title": "목록 갱신 안 됨", "status": "open", "severity": "major", "rd_result_raw": "", "rd_result": "UNSET",
            "description": "설명", "reproduction_step": "1. 실행", "occurrence_cause": "", "action_details": "",
            "occurred_versions": ["1.0"], "target_versions": [], "linked_ids": [], "comment_ids": None, "comments": None,
            "created": "2026-09-01", "updated": "2026-09-01"}
    base.update(values)
    return base


def kinds(events) -> list[tuple[str, str]]:
    return sorted((event.entity_id, event.event_type) for event in events)


def test_baseline_run_makes_no_issue_events():
    assert detect_issue_events(None, [issue()]) == []


def test_created_and_removed_issues():
    events = detect_issue_events([issue("VP-1")], [issue("VP-2")])
    assert kinds(events) == [("VP-1", "ISSUE_REMOVED"), ("VP-2", "ISSUE_CREATED")]
    created = next(event for event in events if event.event_type == "ISSUE_CREATED")
    removed = next(event for event in events if event.event_type == "ISSUE_REMOVED")
    assert created.analysis_required and created.analyses == [NEW_ISSUE]
    assert not removed.analysis_required and removed.analyses == []


@pytest.mark.parametrize(("rd_raw", "rd", "expected"), [
    ("lab_fixed", "FIXED", [FIXED_ISSUE]),
    ("lab_inspec", "SPEC", [SPEC_DECISION]),
    ("lab_nobug", "NOT_BUG", [SPEC_DECISION]),
    ("lab_duplicate", "DUPLICATE", []),
    ("lab_pending", "PENDING", []),
    ("weird", "OTHER", []),
])
def test_rd_result_change_routes_by_common_value(rd_raw, rd, expected):
    events = detect_issue_events([issue()], [issue(rd_result_raw=rd_raw, rd_result=rd)])
    event = next(item for item in events if item.event_type == "ISSUE_RD_RESULT_CHANGED")
    assert event.analyses == expected and event.analysis_required == bool(expected)
    assert event.before["rd_result_raw"] == "" and event.after["rd_result_raw"] == rd_raw and event.after["rd_result"] == rd
    if not expected:
        assert "기록만" in event.reason


def test_root_cause_and_action_changes_on_fixed_issue_go_to_fixed_analysis():
    before = issue(rd_result_raw="lab_fixed", rd_result="FIXED")
    after = issue(rd_result_raw="lab_fixed", rd_result="FIXED", occurrence_cause="캐시 누락", action_details="초기화 추가")
    events = detect_issue_events([before], [after])
    assert kinds(events) == [("VP-1", "ISSUE_ACTION_DETAILS_CHANGED"), ("VP-1", "ISSUE_ROOT_CAUSE_CHANGED")]
    assert all(event.analyses == [FIXED_ISSUE] for event in events)
    grouped = group_targets([event.as_dict() for event in events])
    assert list(grouped[FIXED_ISSUE]) == ["VP-1"] and len(grouped[FIXED_ISSUE]["VP-1"]) == 2   # 한 분석으로 묶는다


def test_root_cause_change_on_unresolved_issue_is_record_only():
    events = detect_issue_events([issue()], [issue(occurrence_cause="원인 추정")])
    assert [(event.event_type, event.analysis_required) for event in events] == [("ISSUE_ROOT_CAUSE_CHANGED", False)]


@pytest.mark.parametrize(("rd", "expected"), [("UNSET", [NEW_ISSUE]), ("FIXED", [FIXED_ISSUE]), ("SPEC", [SPEC_DECISION])])
def test_content_and_reproduction_updates_route_by_state(rd, expected):
    events = detect_issue_events([issue(rd_result=rd)], [issue(rd_result=rd, title="새 제목", reproduction_step="1. 다시 실행")])
    assert kinds(events) == [("VP-1", "ISSUE_CONTENT_UPDATED"), ("VP-1", "ISSUE_REPRODUCTION_UPDATED")]
    assert all(event.analyses == expected for event in events)


def test_status_only_change_needs_no_ai():
    events = detect_issue_events([issue()], [issue(status="in_progress", updated="2026-09-02")])
    assert [(event.event_type, event.analysis_required) for event in events] == [("ISSUE_STATUS_ONLY_CHANGED", False)]
    assert events[0].changed_fields == ["status"]


def test_status_with_other_metadata_is_metadata_change_not_status_only():
    events = detect_issue_events([issue()], [issue(status="closed", severity="minor")])
    assert [(event.event_type, event.changed_fields) for event in events] == [("ISSUE_METADATA_CHANGED", ["status", "severity"])]


def test_status_with_content_change_does_not_claim_status_only():
    events = detect_issue_events([issue()], [issue(status="closed", description="새 설명")])
    assert kinds(events) == [("VP-1", "ISSUE_CONTENT_UPDATED"), ("VP-1", "ISSUE_METADATA_CHANGED")]


def test_updated_timestamp_or_whitespace_only_change_is_no_event():
    events = detect_issue_events([issue()], [issue(updated="2026-09-30", description="  설명\n", title="목록  갱신 안 됨")])
    assert events == []


def test_new_comment_by_id_and_significance():
    before = issue(comments=[{"id": "c1", "created": "", "text": ""}], comment_ids=["c1"])
    after = issue(comment_ids=["c1", "c2", "c3"], comments=[
        {"id": "c1", "created": "2026-09-01", "text": "옛 댓글"},
        {"id": "c2", "created": "2026-09-29", "text": "원인은 캐시 초기화 누락입니다. 재시작 뒤에도 재현됩니다."},
        {"id": "c3", "created": "2026-09-29", "text": "확인했습니다."},
    ])
    events = detect_issue_events([before], [after])
    assert [event.event_type for event in events] == ["ISSUE_COMMENT_ADDED"]
    assert [item["id"] for item in events[0].after["comments"]] == ["c2", "c3"]
    assert events[0].after["significant_ids"] == ["c2"] and events[0].analyses == [COMMENT]


def test_only_noise_comments_are_record_only():
    before = issue(comments=[])
    after = issue(comments=[{"id": "c9", "created": "x", "text": "확인 부탁드립니다"}, {"id": "c10", "created": "x", "text": "ok"}])
    events = detect_issue_events([before], [after])
    assert events[0].event_type == "ISSUE_COMMENT_ADDED" and not events[0].analysis_required


def test_significant_comment_on_fixed_issue_also_runs_fixed_analysis():
    before = issue(rd_result="FIXED", comments=[])
    after = issue(rd_result="FIXED", comments=[{"id": "c1", "created": "x", "text": "조치 내용: 캐시를 화면 진입 때마다 비우도록 수정했습니다."}])
    assert detect_issue_events([before], [after])[0].analyses == [COMMENT, FIXED_ISSUE]


def test_unknown_previous_comments_use_collected_time():
    previous = issue(comments=None, comment_ids=None)
    current = {"comments": [{"id": "a", "created": "2026-09-28T00:00:00Z", "text": "old"},
                            {"id": "b", "created": "2026-09-29T02:00:00Z", "text": "new"}]}
    assert [item["id"] for item in new_comments(previous, current, "2026-09-29T00:00:00Z")] == ["b"]
    assert new_comments(previous, current, "") == []          # 기준 시각도 모르면 새 댓글로 단정하지 않는다


def test_comment_read_failure_skips_comment_diff():
    before = issue(comments=[])
    after = issue(comments=[{"id": "c1", "created": "x", "text": "원인 정보가 담긴 긴 댓글입니다 확인 바랍니다"}], comments_error=True)
    assert detect_issue_events([before], [after]) == []


def test_product_noise_patterns_override_default():
    comment = {"text": "배포 완료 보고서 첨부합니다"}
    assert is_significant(comment, 5)
    assert not is_significant(comment, 5, (r"배포 완료.*",))


def test_fingerprints_are_stable_and_after_fingerprint_ignores_before():
    first = ChangeEvent("issue", "VP-1", "ISSUE_CONTENT_UPDATED", {"title": "a"}, {"title": "b"})
    same = ChangeEvent("issue", "VP-1", "ISSUE_CONTENT_UPDATED", {"title": "a"}, {"title": "b"})
    other_before = ChangeEvent("issue", "VP-1", "ISSUE_CONTENT_UPDATED", {"title": "z"}, {"title": "b"})
    assert first.fingerprint == same.fingerprint != other_before.fingerprint
    assert first.after_fingerprint == other_before.after_fingerprint


def test_srs_events_carry_changed_fields_and_sentences():
    before = [{"id": "VP-1", "old_id": "01", "title": "A", "status": "draft", "text": "하나다. 둘이다."},
              {"id": "VP-2", "old_id": "02", "title": "B", "status": "draft", "text": "x"}]
    after = [{"id": "VP-1", "old_id": "01", "title": "A", "status": "draft", "text": "하나다. 셋이다."},
             {"id": "VP-3", "old_id": "03", "title": "C", "status": "draft", "text": "새"}]
    events = detect_srs_events(diff_snapshots(before, after))
    assert kinds(events) == [("VP-1", "SRS_UPDATED"), ("VP-2", "SRS_REMOVED"), ("VP-3", "SRS_CREATED")]
    updated = next(event for event in events if event.event_type == "SRS_UPDATED")
    assert updated.changed_fields == ["text"] and updated.after["added_sentences"] == ["셋이다."]
    assert updated.after["removed_sentences"] == ["둘이다."] and updated.analyses == [SPEC_COVERAGE]
    removed = next(event for event in events if event.event_type == "SRS_REMOVED")
    assert not removed.analysis_required


def test_srs_baseline_has_no_events():
    assert detect_srs_events(diff_snapshots(None, [{"id": "VP-1"}])) == []
