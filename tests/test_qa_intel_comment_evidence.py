"""Validates: REQ-DAILY-007, REQ-QAINTEL-017. 댓글 ref는 입력 번호로 검증한다."""
import json

import pytest
from pydantic import ValidationError

from app.modules.daily_qa.change_events import FIXED_ISSUE
from app.modules.daily_qa.evidence_validation import validate_finding
from app.modules.daily_qa.intelligence import KnownIds
from app.modules.daily_qa.schema import Evidence, ResultFileError, parse_result
from tests.daily_qa_fixtures import comment, issue_item
from tests.test_qa_intel_pipeline import DAY1, DAY2, analysis_producer, env  # noqa: F401


def parse(tmp_path, evidence):
    path = tmp_path / "FIX-001.json"
    path.write_text(json.dumps({
        "skill": "qa-fixed-issue-analysis", "task_id": "FIX-001", "findings": [{
            "subject": "VP-200", "verdict": "CONSISTENT_WITH_SPEC", "summary": "수정 내용 대조",
            "evidence": evidence,
        }],
    }), encoding="utf-8")
    return parse_result(path, "qa-fixed-issue-analysis", "FIX-001").accepted[0].model_dump()


def check(finding):
    known = KnownIds(issue_ids={"VP-200"}, srs_ids={"VP-10"}, comment_ids={"c1"})
    return validate_finding(finding, FIXED_ISSUE, known, {"VP-200"})


def test_comment_ref_only_passes_file_format_then_input_validation(tmp_path):
    finding = parse(tmp_path, [{"source_type": "comment", "ref": "c1", "summary": "수정 설명"}])
    result, reason = check(finding)
    assert reason == ""
    assert result["evidence"][0]["location"] == "c1"
    assert result["evidence"][0]["ref"] == "c1"


def test_unknown_comment_ref_is_removed_with_record_not_whole_file_retry(tmp_path):
    finding = parse(tmp_path, [{"source_type": "comment", "ref": "invented-comment"},
                              {"source_type": "srs", "location": "VP-10"}])
    result, reason = check(finding)
    assert reason == ""
    assert [entry["source_type"] for entry in result["evidence"]] == ["srs"]
    removed = result["sections"]["validation"]["removed"]
    assert any(entry["value"] == "comment:invented-comment" and "작업 입력에 없는 댓글" in entry["reason"]
               for entry in removed)


def test_unknown_comment_ref_alone_cannot_support_assertive_finding(tmp_path):
    result, reason = check(parse(tmp_path, [{"source_type": "comment", "ref": "invented-comment"}]))
    assert result is None and "근거" in reason


@pytest.mark.parametrize("ref", [None, "", "   ", 42])
def test_missing_comment_location_without_usable_ref_still_fails_format(tmp_path, ref):
    with pytest.raises(ResultFileError):
        parse(tmp_path, [{"source_type": "comment", "ref": ref}])


def test_missing_comment_location_and_ref_still_fails_format(tmp_path):
    with pytest.raises(ResultFileError):
        parse(tmp_path, [{"source_type": "comment"}])


@pytest.mark.parametrize("source_type", ["srs", "spec_doc", "tc", "issue", "manual", "rules", "user_answer"])
def test_other_evidence_kinds_do_not_invent_location_from_ref(tmp_path, source_type):
    with pytest.raises(ResultFileError):
        parse(tmp_path, [{"source_type": source_type, "ref": "spec:doc-p1-1"}])


def test_comment_normalization_preserves_explicit_location_and_input():
    original = {"source_type": "comment", "ref": "c1"}
    assert Evidence.model_validate(original).location == "c1"
    assert "location" not in original
    assert Evidence.model_validate({**original, "location": "VP-200 댓글 c1"}).location == "VP-200 댓글 c1"
    with pytest.raises(ValidationError):
        Evidence.model_validate({**original, "location": None})


def test_comment_ref_is_not_trimmed_into_a_known_id(tmp_path):
    result, reason = check(parse(tmp_path, [{"source_type": "comment", "ref": " c1 "}]))
    assert result is None and "근거" in reason


def test_fixed_issue_comment_ref_only_is_saved_without_retry(env):
    from app.modules.daily_qa.agent_runner import FakeRunner

    env["state"]["comments"] = {"VP-100": [comment("c1", "원인은 목록 캐시 초기화 누락으로 확인했습니다.")]}
    env["run"](DAY1)
    env["state"]["issues"][0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"],
                                           review="lab_fixed", status="resolved")
    produce = analysis_producer(env["calls"])

    def ref_only(task, workspace):
        produce(task, workspace)
        if task.skill == "qa-fixed-issue-analysis":
            path = workspace.out_dir / f"{task.task_id}.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            for finding in result["findings"]:
                finding["evidence"] = [{"source_type": "comment", "ref": "c1"}]
            path.write_text(json.dumps(result), encoding="utf-8")

    outcome = env["run"](DAY2, runner=FakeRunner(ref_only))
    assert sum(skill == "qa-fixed-issue-analysis" for skill, _ in env["calls"]) == 1
    findings = env["store"].list_findings(run_id=outcome["run_id"], analysis_type=FIXED_ISSUE)
    assert len(findings) == 1
    assert findings[0]["evidence"][0]["location"] == "c1"
    assert outcome["stages"][FIXED_ISSUE]["status"] == "ok"
