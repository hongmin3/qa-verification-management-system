"""Validates: REQ-DAILY-003, REQ-DAILY-005 (TEST-DAILY-003)."""

from __future__ import annotations

from app.modules.daily_qa import packages
from app.modules.daily_qa.srs_snapshot import SrsDiff
from app.modules.daily_qa.tc_index import build_index, read_workbook, srs_tokens
from tests.daily_qa_fixtures import make_tc_workbook


def _srs(item_id, old_id="", title="T", category=False, status="draft"):
    return {"id": item_id, "old_id": old_id, "title": title, "status": status, "text": "본문", "is_category": category}


def test_tc_index_reads_srs_column_and_real_row_numbers(tmp_path):
    path = make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A"), ("TC_2", "03-10-05, VP-11", "B")])
    rows = read_workbook(path)
    assert [(row.tc_id, row.row, row.srs_refs) for row in rows] == [("TC_1", 2, ["VP-10"]), ("TC_2", 3, ["03-10-05", "VP-11"])]
    assert rows[0].location() == "tc.xlsx / Viewer / 2행"


def test_tc_index_reports_unreadable_file_instead_of_hiding_it(tmp_path):
    broken = tmp_path / "broken.xlsx"
    broken.write_text("not a workbook", encoding="utf-8")
    rows, errors = build_index([broken])
    assert rows == [] and errors and errors[0].startswith("broken.xlsx")


def test_srs_tokens_accept_polarion_ids_and_legacy_numbers():
    assert srs_tokens("VP-606 / 05-140-10 / N/A / 1.1.0") == ["VP-606", "05-140-10"]


def test_candidates_match_polarion_id_and_legacy_number(tmp_path):
    rows = read_workbook(make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A"), ("TC_2", "03-10-05", "B"), ("TC_3", "VP-99", "C")]))
    index = __import__("app.modules.daily_qa.tc_index", fromlist=["by_srs"]).by_srs(rows)
    picked = packages.candidates_for({"id": "VP-10", "old_id": "03-10-05"}, index, limit=10)
    assert [row.tc_id for row in picked] == ["TC_1", "TC_2"]


def test_removed_srs_still_referenced_becomes_must_update(tmp_path):
    rows = read_workbook(make_tc_workbook(tmp_path / "tc.xlsx", [("TC_1", "VP-10", "A")]))
    findings = packages.removed_srs_findings(SrsDiff(removed=[_srs("VP-10", title="삭제됨")]), rows)
    assert len(findings) == 1
    assert findings[0].verdict == "수정 필수" and findings[0].tc_ref["tc_id"] == "TC_1"
    assert any(item["validity"] == "Deleted" for item in findings[0].evidence)


def test_trace_gaps_finds_uncovered_srs_and_deleted_references_but_not_legacy(tmp_path):
    rows = read_workbook(make_tc_workbook(
        tmp_path / "tc.xlsx",
        [("TC_1", "VP-1", "A"), ("TC_2", "02-20-11", "B"), ("TC_3", "VP-404", "C"), ("TC_4", "99-99-99", "D")],
    ))
    srs = [
        _srs("VP-1"),
        _srs("VP-2", old_id="02-20-11"),          # Legacy 번호로 연결됨
        _srs("VP-3"),                              # TC 없음
        _srs("VP-5", category=True),               # 분류 항목은 제외
        _srs("VP-6", status="deleted"),            # 무효 상태는 제외
    ]
    result = packages.trace_gaps(srs, rows)
    by_verdict = {}
    for finding in result.findings:
        by_verdict.setdefault(finding.verdict, []).append(finding.subject)
    assert by_verdict == {"TC 없음": ["VP-3"], "삭제된 SRS 참조": ["VP-404"]}
    assert result.legacy_unmatched == 1          # 99-99-99 는 삭제로 단정하지 않는다
    assert (result.srs_seen, result.srs_examined, result.tc_rows_seen) == (5, 3, 4)
