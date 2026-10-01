"""분석 결과 근거 검증 — 모델이 지어낸 번호·근거를 코드가 걸러 내는지 (SPEC REQ-QAINTEL-017).

Validates: REQ-QAINTEL-017
"""

from __future__ import annotations

import json

import pytest

from app.modules.daily_qa.change_events import COMMENT, FIXED_ISSUE, NEW_ISSUE, SPEC_COVERAGE, SPEC_DECISION
from app.modules.daily_qa.evidence_validation import validate_finding
from app.modules.daily_qa.intelligence import KnownIds
from app.modules.daily_qa.schema import parse_result

SRS_EVIDENCE = {"source_type": "srs", "location": "VP-10 / 2번째 문장", "summary": "", "validity": "Current"}


def known() -> KnownIds:
    return KnownIds(
        issue_ids={"VP-100", "VP-200", "VP-300"},
        srs_ids={"VP-10", "01-10-10"},
        tc_ids={"TC_1"},
        tc_locations={"tc.xlsx / Viewer / 2행"},
        spec_refs={"spec:doc-p1-1"},
        comment_ids={"c1"},
        issue_rd={"VP-100": "UNSET", "VP-200": "FIXED", "VP-300": "SPEC"},
    )


def finding(subject="VP-100", verdict="SPEC_VIOLATION", **values) -> dict:
    data = {"subject": subject, "verdict": verdict, "summary": "요약", "confidence": "Confirmed", "evidence": [dict(SRS_EVIDENCE)],
            "draft_tcs": [], "sections": {}, "action": ""}
    data.update(values)
    return data


def check(data, kind=NEW_ISSUE, targets=("VP-100",), axes=()):
    return validate_finding(data, kind, known(), set(targets), axes)


def test_unknown_and_self_duplicate_candidates_are_removed_and_recorded():
    data = finding(sections={"duplicate": {"verdict": "STRONG_DUPLICATE", "candidates": [
        {"issue_id": "VP-999", "reason": "지어냄"}, {"issue_id": "VP-100", "reason": "자기 자신"}, {"issue_id": "VP-300", "reason": "같은 조건"}]}})
    result, reason = check(data)
    assert reason == ""
    assert [item["issue_id"] for item in result["sections"]["duplicate"]["candidates"]] == ["VP-300"]
    removed = {item["value"]: item["reason"] for item in result["sections"]["validation"]["removed"]}
    assert "VP-999" in removed and "오늘 이슈 스냅샷에 없는" in removed["VP-999"]
    assert removed["VP-100"] == "대상 이슈 자신"
    assert result["related_ids"]["issues"] == ["VP-300"]


def test_duplicate_claim_without_any_real_candidate_is_downgraded():
    data = finding(sections={"duplicate": {"verdict": "POSSIBLE_DUPLICATE", "candidates": [{"issue_id": "VP-4040"}]}})
    result, _ = check(data)
    assert result["sections"]["duplicate"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert any("INSUFFICIENT_EVIDENCE" in note for note in result["sections"]["validation"]["notes"])


def test_hallucinated_srs_evidence_is_removed_and_assertive_verdict_without_evidence_is_rejected():
    data = finding(evidence=[{"source_type": "srs", "location": "VP-77777 / 본문"}])
    result, reason = check(data)
    assert result is None and "근거" in reason


def test_absence_verdict_survives_without_evidence_but_confidence_is_capped():
    data = finding(verdict="SPEC_UNDEFINED", evidence=[{"source_type": "srs", "location": "VP-77777"}], confidence="Confirmed")
    result, _ = check(data)
    assert result["confidence"] == "Review Needed" and result["evidence"] == []


@pytest.mark.parametrize(("evidence", "kept"), [
    ({"source_type": "spec_doc", "ref": "spec:doc-p1-1", "location": "사양서 · p.1"}, True),
    ({"source_type": "spec_doc", "ref": "spec:made-up", "location": "사양서 · p.9"}, False),
    ({"source_type": "tc", "location": "tc.xlsx / Viewer / 2행"}, True),
    ({"source_type": "tc", "location": "TC_1 Expected 1번"}, True),
    ({"source_type": "tc", "location": "없는.xlsx / 시트 / 99행"}, False),
    ({"source_type": "issue", "location": "VP-200 재현 절차"}, True),
    ({"source_type": "issue", "location": "VP-12345"}, False),
    ({"source_type": "srs", "location": "Legacy 01-10-10"}, True),
    ({"source_type": "comment", "ref": "c1", "location": "VP-100 댓글"}, True),
    ({"source_type": "comment", "ref": "c404", "location": "VP-100 댓글"}, False),
    ({"source_type": "rules", "location": "§61"}, True),
    ({"source_type": "rules", "location": "규칙 어딘가"}, False),
])
def test_each_evidence_kind_is_checked_against_real_data(evidence, kept):
    data = finding(verdict="SPEC_UNDEFINED", evidence=[evidence])
    result, _ = check(data)
    assert (len(result["evidence"]) == 1) is kept


def test_fix_tc_for_issue_that_is_not_fixed_is_rejected():
    draft = {"kind": "수정확인", "title": "t", "test_step": "1.", "expected_result": "1."}
    result, reason = check(finding(verdict="CONSISTENT_WITH_SPEC", draft_tcs=[draft]), FIXED_ISSUE)   # VP-100 은 UNSET
    assert result is None and "FIXED 가 아닌" in reason
    ok, _ = check(finding(subject="VP-200", verdict="CONSISTENT_WITH_SPEC", draft_tcs=[dict(draft)]), FIXED_ISSUE, ("VP-200",))
    assert ok and ok["draft_tcs"]


@pytest.mark.parametrize("kind", [NEW_ISSUE, SPEC_DECISION])
def test_analyses_that_must_not_draft_tcs_are_rejected(kind):
    draft = {"kind": "Regression", "title": "t", "test_step": "1.", "expected_result": "1."}
    result, reason = check(finding(verdict="SPEC_VIOLATION" if kind == NEW_ISSUE else "SUPPORTED_BY_SPEC", draft_tcs=[draft]), kind)
    assert result is None and "초안" in reason


@pytest.mark.parametrize("verdict", ["SPEC_UNDEFINED", "INSUFFICIENT_EVIDENCE"])
def test_fixed_analysis_without_spec_basis_holds_drafts(verdict):
    draft = {"kind": "수정확인", "title": "t", "test_step": "1.", "expected_result": "1. 지어낸 Expected"}
    result, _ = check(finding(subject="VP-200", verdict=verdict, draft_tcs=[draft]), FIXED_ISSUE, ("VP-200",))
    assert result["draft_tcs"] == [] and result["sections"]["tc_hold"]["reason"]


def test_coverage_fully_covered_never_keeps_new_drafts():
    draft = {"kind": "Regression", "title": "필요 없는 TC", "test_step": "1.", "expected_result": "1."}
    tcs = [{"tc_id": "TC_1", "decision": "KEEP", "reason": "Expected 까지 검증"}]
    result, _ = check(finding(subject="VP-10", verdict="FULLY_COVERED", draft_tcs=[draft],
                              sections={"checklist_coverage": {"tcs": tcs}}), SPEC_COVERAGE, ("VP-10",))
    assert result["draft_tcs"] == []


def test_fully_covered_without_real_tc_is_rejected():
    tcs = [{"tc_id": "TC_404", "decision": "KEEP"}]
    result, reason = check(finding(subject="VP-10", verdict="FULLY_COVERED", sections={"checklist_coverage": {"tcs": tcs}}),
                           SPEC_COVERAGE, ("VP-10",))
    assert result is None and "FULLY_COVERED" in reason


def test_coverage_cleans_tc_decisions_relations_and_alerts():
    sections = {
        "checklist_coverage": {"tcs": [{"tc_id": "TC_1", "decision": "UPDATE_EXISTING", "recommended_change": "Expected 3번"},
                                       {"tc_id": "TC_1", "decision": "DELETE"}, {"location": "없음 / 1행", "decision": "KEEP"}]},
        "issue_coverage": {"issues": [{"issue_id": "VP-200", "relation": "PAST_FIXED"}, {"issue_id": "VP-300", "relation": "WHATEVER"},
                                      {"issue_id": "VP-777", "relation": "PAST_SPEC"}]},
        "alerts": [{"type": "ISSUE_WITHOUT_TC"}, {"type": "MADE_UP"}],
    }
    result, _ = check(finding(subject="VP-10", verdict="PARTIALLY_COVERED", sections=sections), SPEC_COVERAGE, ("VP-10",))
    s = result["sections"]
    assert [entry["decision"] for entry in s["checklist_coverage"]["tcs"]] == ["UPDATE_EXISTING"]
    assert {entry["issue_id"]: entry["relation"] for entry in s["issue_coverage"]["issues"]} == {"VP-200": "PAST_FIXED", "VP-300": "EXISTING_DEFECT"}
    assert s["issue_coverage"]["has_related"] and [alert["type"] for alert in s["alerts"]] == ["ISSUE_WITHOUT_TC"]
    assert result["related_ids"]["srs"] == ["VP-10"] and result["related_ids"]["tcs"] == ["TC_1"]


def test_regression_axes_are_fixed_by_code():
    risk = {"axes": [{"axis": "DIRECT", "applicable": True, "reason": "직접"}, {"axis": "MADE_UP", "applicable": True}]}
    result, _ = check(finding(subject="VP-200", verdict="CONSISTENT_WITH_SPEC", sections={"regression_risk": risk}),
                      FIXED_ISSUE, ("VP-200",), ("DIRECT", "STATE", "GENERATOR"))
    axes = result["sections"]["regression_risk"]["axes"]
    assert [entry["axis"] for entry in axes] == ["DIRECT", "STATE", "GENERATOR"]
    assert axes[1]["applicable"] is None and "QA 확인 필요" in axes[1]["reason"]


def test_comment_analysis_keeps_only_input_comments_and_skips_insignificant():
    sections = {"comments": [{"comment_id": "c1", "classification": "ROOT_CAUSE_INFORMATION"},
                             {"comment_id": "c404", "classification": "SPEC_CLAIM"}]}
    result, _ = check(finding(verdict="ROOT_CAUSE_INFORMATION", evidence=[], sections=sections), COMMENT)
    assert [entry["comment_id"] for entry in result["sections"]["comments"]] == ["c1"]
    result, reason = check(finding(verdict="NOT_SIGNIFICANT", sections={"comments": [
        {"comment_id": "c1", "classification": "NOT_SIGNIFICANT"}]}), COMMENT)
    assert result is None and reason == "not_significant"


def test_history_flags_need_supporting_candidates():
    sections = {"historical": {"flags": ["POSSIBLE_REGRESSION", "NOT_A_FLAG"], "candidates": [{"issue_id": "VP-9999"}]}}
    result, _ = check(finding(sections=sections))
    assert result["sections"]["historical"]["flags"] == []


def test_forbidden_actions_in_recommendation_are_rejected():
    result, reason = check(finding(sections={"recommendation": {"actions": ["VP-100 이슈를 close 한다"]}}))
    assert result is None and "§55" in reason


def test_result_for_a_target_not_in_the_task_is_rejected():
    result, reason = check(finding(subject="VP-300"))
    assert result is None and "작업 대상이 아닌" in reason


def test_unknown_perspective_is_cleared():
    draft = {"kind": "수정확인", "title": "t", "test_step": "1.", "expected_result": "1.", "perspective": "VIBES"}
    result, _ = check(finding(subject="VP-200", verdict="CONSISTENT_WITH_SPEC", draft_tcs=[draft]), FIXED_ISSUE, ("VP-200",))
    assert result["draft_tcs"][0]["perspective"] == ""


def test_parse_result_rejects_unknown_verdicts_for_analysis_skills(tmp_path):
    path = tmp_path / "NEW-001.json"
    path.write_text(json.dumps({"skill": "qa-new-issue-analysis", "task_id": "NEW-001", "findings": [
        {"subject": "VP-100", "verdict": "LOOKS_BAD", "summary": "?"},
        {"subject": "VP-100", "verdict": "SPEC_UNDEFINED", "summary": "근거 없음 판정", "evidence": []},
    ]}), encoding="utf-8")
    checked = parse_result(path, "qa-new-issue-analysis", "NEW-001")
    assert [item.verdict for item in checked.accepted] == ["SPEC_UNDEFINED"]
    assert "허용되지 않은 판정" in checked.rejected[0]["reason"]
