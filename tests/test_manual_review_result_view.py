"""Validates: REQ-MANUAL-013 — 할 일 묶음 규칙 (TEST-MANUAL-013)."""

from app.modules.manual_review import result_view


def _change(decision=None, qa_decision=None, functional=True, issues=None, human=False, **extra):
    return {
        "id": extra.pop("id", 1), "functional": functional, "decision": decision, "qa_decision": qa_decision,
        "review_required": False, "paragraph_index": extra.pop("paragraph_index", 0),
        "ai_judgment": {"needs_human_review": human, "text_issues": issues or []}, **extra,
    }


def test_bucket_rules_follow_the_spec_table():
    assert result_view.bucket_for(_change(functional=False)) == "simple"
    assert result_view.bucket_for(_change("MODIFICATION_REQUIRED")) == "request"
    assert result_view.bucket_for(_change("MISSING_SUSPECTED")) == "request"
    assert result_view.bucket_for(_change("PASS", issues=[{"code": "DUPLICATE_WORD"}])) == "request"
    assert result_view.bucket_for(_change("SPEC_CONFIRMATION_REQUIRED")) == "check"
    assert result_view.bucket_for(_change(None)) == "check"
    assert result_view.bucket_for(_change("PASS", human=True)) == "check"
    assert result_view.bucket_for(_change("MODIFICATION_REQUIRED", human=True)) == "request"
    assert result_view.bucket_for(_change("PASS")) == "pass"


def test_qa_decision_alone_decides_the_bucket():
    # QA 가 문제없음으로 확정하면 문장 점검이나 사람 검토 표시로 다시 올리지 않는다.
    assert result_view.bucket_for(_change("MODIFICATION_REQUIRED", qa_decision="PASS", issues=[{"code": "X"}], human=True)) == "pass"
    assert result_view.bucket_for(_change("PASS", qa_decision="SUPPLEMENT_REQUIRED")) == "request"
    assert result_view.bucket_for(_change("PASS", qa_decision="UNABLE_TO_DETERMINE")) == "check"


def test_counts_progress_default_bucket_and_order():
    changes = [
        _change("PASS", id=1, paragraph_index=9),
        _change("MODIFICATION_REQUIRED", id=2, paragraph_index=5),
        _change("PASS", qa_decision="PASS", id=3, paragraph_index=1),
        _change(functional=False, id=4, paragraph_index=0),
    ]
    counts = result_view.bucket_counts(changes)
    assert counts == {"request": 1, "check": 0, "pass": 2, "simple": 1}
    assert result_view.progress(changes) == {"confirmed": 1, "functional": 3}
    assert result_view.default_bucket(counts) == "request"
    assert result_view.default_bucket({"request": 0, "check": 0, "pass": 0, "simple": 2}) == "simple"
    assert [change["id"] for change in result_view.ordered_changes(changes)] == [2, 3, 1, 4]


def test_location_uses_heading_or_pdf_page():
    assert result_view.location({"section_heading": "문서 규칙", "paragraph_index": 7}) == "문서 규칙 · 문단 #7"
    assert result_view.location({"section_heading": "", "paragraph_index": 7}) == "문단 #7"
    assert result_view.location({"source_page": 3, "paragraph_index": 0}) == "PDF 페이지 3"
