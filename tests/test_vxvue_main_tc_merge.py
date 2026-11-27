"""Validates: REQ-VXVUECHATBOT-029 with synthetic workbooks."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_vxvue_main_tc_merge.py"


def workbook(path, title, note, *, formula="=1+1", relocate=False):
    book = Workbook()
    sheet = book.active
    sheet.title = "Registration"
    sheet["B2"] = "TC-1" if not relocate else "TC-2"
    sheet["J2"] = title
    sheet["K2"] = note
    book.create_sheet("Other")["A1"] = formula
    book.save(path)


def setup_workbooks(tmp_path, current, candidate, *, candidate_note="다른 작업", **options):
    base, live, proposed = [tmp_path / f"{name}.xlsx" for name in ("base", "live", "proposed")]
    workbook(base, "기존", "이전 참고")
    workbook(live, current, "다른 작업")
    workbook(proposed, candidate, candidate_note, **options)
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({
        "baseline_sha256": sha256(base.read_bytes()).hexdigest(),
        "changes": [{"sheet": "Registration", "cell": "J2", "id_cell": "B2", "before": "기존",
                     "after": "승인\n제목", "reason": "승인한 제목 갱신"}],
    }, ensure_ascii=False), encoding="utf-8")
    return base, live, proposed, approvals


def run(paths):
    result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), *map(str, paths)],
                            capture_output=True, encoding="utf-8", check=False)
    return result.returncode, json.loads(result.stdout)


def test_preserves_live_changes_and_can_run_again(tmp_path):
    paths = setup_workbooks(tmp_path, "기존", "승인\n제목")
    before = [path.read_bytes() for path in paths]
    code, report = run(paths)
    assert code == 0
    assert report["pending"] == ["Registration!J2"]
    base, _, proposed, approvals = paths
    code, report = run((base, proposed, proposed, approvals))
    assert code == 0
    assert report["already_applied"] == ["Registration!J2"]
    assert report["pending"] == []
    assert [path.read_bytes() for path in paths] == before


def test_flags_same_cell_conflict(tmp_path):
    code, report = run(setup_workbooks(tmp_path, "동시 변경", "승인\n제목"))
    assert code == 1
    assert report["conflicts"] == ["Registration!J2"]


def test_rejects_lost_notes_and_changed_formula(tmp_path):
    code, report = run(setup_workbooks(tmp_path, "기존", "승인\n제목",
                                      candidate_note="이전 참고", formula="=2+1"))
    assert code == 1
    assert report["unexpected"] == ["Other!A1", "Registration!K2"]


def test_rejects_candidate_that_missed_edit_or_moved_row(tmp_path):
    code, report = run(setup_workbooks(tmp_path, "기존", "기존", relocate=True))
    assert code == 1
    assert report["missing"] == ["Registration!J2"]
    assert report["unexpected"] == ["Registration!B2"]


def test_checks_approved_target_identity_against_base(tmp_path):
    paths = setup_workbooks(tmp_path, "기존", "승인\n제목")
    _, live, proposed, _ = paths
    for file in (live, proposed):
        book = load_workbook(file)
        book["Registration"]["B2"] = "TC-2"
        book.save(file)
    code, report = run(paths)
    assert code == 1
    assert report["conflicts"] == ["Registration!J2"]


def test_rejects_manifest_with_changed_baseline(tmp_path):
    paths = setup_workbooks(tmp_path, "기존", "승인\n제목")
    workbook(paths[0], "다른 판", "이전 참고")
    code, report = run(paths)
    assert code == 2
    assert report["passed"] is False


def test_compares_rich_text_as_displayed_text(tmp_path):
    paths = setup_workbooks(tmp_path, "기존", "승인\n제목")
    _, _, proposed, _ = paths
    book = load_workbook(proposed)
    book["Registration"]["J2"] = CellRichText([TextBlock(InlineFont(b=True), "승인"), "\n제목"])
    book["Registration"]["K2"] = CellRichText([TextBlock(InlineFont(b=True), "다른"), " 작업"])
    book.save(proposed)
    code, report = run(paths)
    assert code == 0
    assert report["unexpected"] == []


def test_unreadable_ooxml_returns_json_input_error(tmp_path):
    paths = setup_workbooks(tmp_path, "기존", "승인\n제목")
    with ZipFile(paths[2], "w") as archive:
        archive.writestr("other.txt", "synthetic")
    code, report = run(paths)
    assert code == 2
    assert report["passed"] is False

    with ZipFile(paths[2], "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types")
    code, report = run(paths)
    assert code == 2
    assert report["passed"] is False
