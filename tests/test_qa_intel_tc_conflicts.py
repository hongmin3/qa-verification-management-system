"""기존 TC Expected 충돌 저장·검증·표시. 모든 자료는 합성이다.

Validates: REQ-QAINTEL-016, REQ-QAINTEL-017, REQ-QAINTEL-020
"""

from datetime import timedelta

import pytest

from app.modules.daily_qa.change_events import SPEC_COVERAGE
from tests.qa_intel_harness import DAY1
from tests.test_qa_intel_coverage import build, change_spec, tc_check
from tests.test_qa_intel_dashboard import world  # noqa: F401 - 공용 HTTP fixture
from tests.test_qa_intel_validation import check, finding


def conflict(classification="기존 TC 수정 필요", **values):
    return {"classification": classification, "reason": "현재 사양은 선택 상태를 유지한다.", "issue_id": "VP-200", **values}


@pytest.mark.parametrize("classification", [
    "Issue만 수정 필요", "기존 TC 수정 필요", "Issue + TC 모두 수정 필요", "사양 확인 필요",
])
def test_conflict_result_survives_validation_and_http_display(world, classification):
    data = finding(subject="VP-10", verdict="PARTIALLY_COVERED", sections={"checklist_coverage": {"tcs": [
        {"tc_id": "TC_1", "decision": "KEEP", "expected_conflict": conflict(classification)}]}})
    result, reason = check(data, SPEC_COVERAGE, ("VP-10",), tc_mode=True)
    assert reason == "" and result["sections"]["checklist_coverage"]["tcs"][0]["expected_conflict"] == conflict(classification)
    assert result["related_ids"]["issues"] == ["VP-200"]
    result["analysis_type"] = "TC_CHECK"
    fid = world["store"].add_finding(world["changed"]["run_id"], "qa-spec-coverage-analysis", "COV-001", result, product="vxvue")
    response = world["client"].get(f"/qa-agent/findings/{fid}")
    assert response.status_code == 200
    assert "Expected 충돌" in response.text and classification in response.text
    assert "현재 사양은 선택 상태를 유지한다." in response.text
    assert "VP-200" in response.text


@pytest.mark.parametrize("bad", [
    conflict("UNKNOWN"), conflict(issue_id="VP-999"), conflict(reason=""), "UNKNOWN",
])
def test_unsupported_conflict_keeps_tc_and_records_original_without_reclassification(bad):
    data = finding(subject="VP-10", verdict="PARTIALLY_COVERED", sections={"checklist_coverage": {"tcs": [
        {"tc_id": "TC_1", "decision": "UPDATE_EXISTING", "expected_conflict": bad}]}})
    result, _ = check(data, SPEC_COVERAGE, ("VP-10",), tc_mode=True)
    tc = result["sections"]["checklist_coverage"]["tcs"][0]
    assert tc["decision"] == "UPDATE_EXISTING" and "expected_conflict" not in tc
    removed = result["sections"]["validation"]["removed"]
    assert any(item["kind"] == "expected_conflict" and str(bad)[:120] in item["value"] for item in removed)
    assert result["confidence"] == "Review Needed"


def test_legacy_tc_without_conflict_remains_valid():
    entry = {"tc_id": "TC_1", "decision": "KEEP"}
    result, reason = check(finding(subject="VP-10", verdict="FULLY_COVERED", sections={"checklist_coverage": {"tcs": [entry]}}),
                           SPEC_COVERAGE, ("VP-10",), tc_mode=True)
    assert reason == "" and result["sections"]["checklist_coverage"]["tcs"] == [entry]
    assert result["sections"]["validation"]["removed"] == []


def test_unknown_long_classification_preserves_complete_original_for_review():
    raw = conflict("UNKNOWN-" + "x" * 160)
    result, _ = check(finding(subject="VP-10", verdict="PARTIALLY_COVERED", sections={"checklist_coverage": {"tcs": [
        {"tc_id": "TC_1", "decision": "KEEP", "expected_conflict": raw}]}}), SPEC_COVERAGE, ("VP-10",), tc_mode=True)
    removed = result["sections"]["validation"]["removed"][0]
    assert removed["original"] == raw


def test_requested_tc_check_carries_expected_actual_and_persists_conflict(tmp_path):
    harness = build(tmp_path, [("TC-310", "VP-767", "선택 상태", "1. 선택 상태가 초기화된다.")])
    original_tc = harness.inputs.tc_paths[0].read_bytes()
    harness.issues[0]["attributes"]["reproductionStep"]["value"] = "[Step]<br/>" + "화면을 확인한다. " * 100 + "<br/>[Expected Result]<br/>선택 상태를 유지한다.<br/>[Actual Result]<br/>선택 상태가 초기화된다."
    # 새 기준 스냅샷에 합성 이슈 변경을 반영한다.
    harness.run(DAY1 + timedelta(hours=1))
    change_spec(harness)
    harness.script["VP-767"] = {"verdict": "PARTIALLY_COVERED", "sections": {"checklist_coverage": {"tcs": [
        {"tc_id": "TC-310", "decision": "UPDATE_EXISTING", "expected_conflict": conflict(issue_id="VP-30301")}]}}}
    outcome = tc_check(harness)
    related = harness.payloads[-1]["items"][0]["candidates"]["issues"][0]
    assert related["reproduction"]["expected"] == ["선택 상태를 유지한다."]
    assert related["reproduction"]["actual"] == ["선택 상태가 초기화된다."]
    saved = harness.store.list_findings(run_id=outcome["run_id"], analysis_type="TC_CHECK")[0]
    assert saved["sections"]["checklist_coverage"]["tcs"][0]["expected_conflict"]["classification"] == "기존 TC 수정 필요"
    assert harness.inputs.tc_paths[0].read_bytes() == original_tc
