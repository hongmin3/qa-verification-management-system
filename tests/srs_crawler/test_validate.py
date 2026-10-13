# Validates: REQ-SYNC-003
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip('bs4', reason='SRS 수집 PC의 선택 라이브러리')

from app.modules.knowledge.srs_crawler.collector import CollectStats
from app.modules.knowledge.srs_crawler.pdf import PdfResult
from app.modules.knowledge.srs_crawler.validate import check_duplicates, validate_run


def test_check_duplicates_finds_repeated_uid():
    records = [{"uid": "VXvue/VP-1"}, {"uid": "VXvue/VP-2"}, {"uid": "VXvue/VP-1"}]
    assert check_duplicates(records) == ["VXvue/VP-1"]


def test_validate_run_fails_when_srs_count_mismatch_protects_existing_pdfs():
    stats = [CollectStats(project_id="VXvue", total_items=350, expected_total=360)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": f"VXvue/VP-{i}", "id": f"VP-{i}"} for i in range(350)],
        assigned_records=[{"uid": f"VXvue/VP-{i}"} for i in range(350)],
        required_pdf_names=["a.pdf"],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=100, page_count=5, ok=True)],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is False
    failed_names = [c["name"] for c in result.checks if not c["passed"]]
    assert "srs_count_match:VXvue" in failed_names


def test_validate_run_fails_when_pdf_is_empty():
    stats = [CollectStats(project_id="VXvue", total_items=1, expected_total=1)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": "VXvue/VP-1", "id": "VP-1"}],
        assigned_records=[{"uid": "VXvue/VP-1"}],
        required_pdf_names=["a.pdf"],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=0, page_count=0, ok=False, error="empty")],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is False


def test_validate_run_passes_when_all_checks_ok():
    stats = [CollectStats(project_id="VXvue", total_items=2, expected_total=2)]
    result = validate_run(
        collect_stats=stats,
        all_records=[{"uid": "VXvue/VP-1", "id": "VP-1"}, {"uid": "VXvue/VP-2", "id": "VP-2"}],
        assigned_records=[{"uid": "VXvue/VP-1"}, {"uid": "VXvue/VP-2"}],
        required_pdf_names=["a.pdf"],
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=100, page_count=3, ok=True)],
        expected_pdf_count=1,
        partition_warnings=[],
    )
    assert result.ok is True


def _validation(**overrides):
    records = [{"uid": "P/1", "id": "1"}, {"uid": "P/2", "id": "2"}]
    arguments = dict(
        collect_stats=[], all_records=records, assigned_records=records,
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=100, page_count=1, ok=True)],
        expected_pdf_count=1, partition_warnings=[],
        required_pdf_names=["a.pdf"],
    )
    arguments.update(overrides)
    return validate_run(**arguments)


@pytest.mark.parametrize("assigned", [[], [{"uid": "P/1"}],
    [{"uid": "P/1"}, {"uid": "P/1"}],
    [{"uid": "P/1"}, {"uid": "P/2"}, {"uid": "P/3"}]])
def test_assignment_must_cover_collected_ids_exactly_once(assigned):
    result = _validation(assigned_records=assigned)
    assert not result.ok
    assert any(c["name"] == "all_srs_assigned_once" and not c["passed"] for c in result.checks)


@pytest.mark.parametrize("previous_count,ratio,expected", [(3, .95, False), (4, .5, True),
    (None, .95, True), (0, .95, True), (3, 0, True), (3, -1, False), (3, 1.1, False)])
def test_previous_snapshot_count_ratio(previous_count, ratio, expected):
    assert _validation(previous_srs_count=previous_count, min_expected_srs_ratio=ratio).ok is expected


def test_optional_pdfs_allows_only_empty_group_omission():
    assert _validation(expected_pdf_count=2, require_all_pdfs=False).ok
    assert not _validation(expected_pdf_count=2, require_all_pdfs=True).ok
    assert not _validation(expected_pdf_count=2, require_all_pdfs=False,
                           required_pdf_names=["a.pdf", "b.pdf"]).ok


def test_optional_pdfs_never_accepts_bad_or_zero_results():
    assert not _validation(require_all_pdfs=False, required_pdf_names=[], pdf_results=[]).ok
    assert not _validation(require_all_pdfs=False,
        pdf_results=[PdfResult(path=Path("a.pdf"), size_bytes=0, page_count=0, ok=False)]).ok
