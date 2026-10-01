"""검증 TC 초안·Coverage Excel (SPEC REQ-DAILY-019, REQ-QAINTEL-018).

열 순서는 제품의 영향성평가 Checklist 형식(제품 설정 `qa_intelligence.checklist.headers`)과 같다.
원본 Checklist 가 있으면 머리글 행의 글꼴·채우기·테두리·열 너비를 복사한다 (QA 규칙 G6: 예시 파일의
서식을 우선 재사용하고, 본문 셀에는 임의 배경색을 넣지 않는다). 원본은 읽기 전용으로 연다.

- `Checklist 초안`: 등록 가능한 신규 초안만 (수정 완료 이슈 초안, Coverage `CREATE_NEW`).
- `Coverage`: 사양 변경마다 TC 별 조치(`KEEP`·`UPDATE_EXISTING`·`CREATE_NEW`·`SPEC_REVIEW_REQUIRED`).
- `Review`: AI 판단·근거 (QA 규칙 §75 — 실행용 시트와 판단을 섞지 않는다).
"""

from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

#: 제품 설정에 형식이 없을 때 쓰는 기본 열 (변경사항 영향성평가 Checklist 의 일반적인 열).
DEFAULT_HEADERS = (
    "Category", "TC ID", "버전", "SRS No", "변경사항", "변경사항 상세",
    "Title", "Precondition", "Test Step", "Expected Result", "Test Data",
)
CHECKLIST_HEADERS = DEFAULT_HEADERS
#: 초안 필드 → 기본 열 이름.
DEFAULT_COLUMNS = {
    "category": "Category", "tc_id": "TC ID", "version": "버전", "srs_no": "SRS No", "change": "변경사항",
    "change_detail": "변경사항 상세", "title": "Title", "precondition": "Precondition", "test_step": "Test Step",
    "expected_result": "Expected Result", "test_data": "Test Data",
}
REVIEW_HEADERS = ("대상", "대상 제목", "분석", "판정", "요약", "근거 위치", "신뢰도", "Finding 번호")
COVERAGE_HEADERS = ("SRS No", "변경사항", "Coverage", "TC 위치", "조치", "이유", "추천 수정", "관련 Issue", "Finding 번호")
WIDTH = 20


def _profile(checklist: dict | None) -> tuple[tuple[str, ...], dict[str, str]]:
    checklist = checklist or {}
    headers = tuple(checklist.get("headers") or DEFAULT_HEADERS)
    columns = {**DEFAULT_COLUMNS, **(checklist.get("columns") or {})}
    return headers, columns


def _template_header(template: Path | None, headers: tuple[str, ...]):
    """원본 Checklist 에서 첫 열·Expected 열이 함께 있는 머리글 행을 찾는다. 없으면 None."""
    if not template or not template.is_file():
        return None
    first = headers[0]
    expected = next((name for name in headers if "expected" in name.casefold()), headers[-1])
    workbook = load_workbook(template, read_only=False)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=8):
            values = [str(cell.value or "").strip() for cell in row]
            if first in values and expected in values:
                start = values.index(first)
                cells = row[start:start + len(headers)]
                widths = []
                for cell in cells:
                    dimension = sheet.column_dimensions.get(cell.column_letter)
                    widths.append(dimension.width if dimension and dimension.width else None)
                return cells, widths, sheet.title
    return None


def _append(sheet, values: list, wrap: Alignment) -> None:
    sheet.append(values)
    for cell in sheet[sheet.max_row]:
        cell.alignment = wrap


def write_draft(path: Path, rows: list[dict], review_rows: list[dict], template: Path | None = None,
                coverage_rows: list[dict] | None = None, checklist: dict | None = None) -> dict:
    headers, _ = _profile(checklist)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist 초안"
    sheet.append(list(headers))
    template_info = _template_header(template, headers)
    for index, header_cell in enumerate(sheet[1]):
        width = None
        if template_info:
            cells, widths, _ = template_info
            source = cells[index] if index < len(cells) else None
            if source is not None and source.has_style:
                header_cell.font = copy(source.font)
                header_cell.fill = copy(source.fill)
                header_cell.border = copy(source.border)
                header_cell.alignment = copy(source.alignment)
            width = widths[index] if index < len(widths) else None
        sheet.column_dimensions[header_cell.column_letter].width = width or (45 if "Step" in headers[index] or "Expected" in headers[index] else WIDTH)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        _append(sheet, [row.get(name, "") for name in headers], wrap)
    sheet.freeze_panes = "A2"

    coverage = workbook.create_sheet("Coverage")
    coverage.append(list(COVERAGE_HEADERS))
    for item in coverage_rows or []:
        _append(coverage, [item.get(name, "") for name in COVERAGE_HEADERS], wrap)
    for letter, width in zip("ABCDEFGHI", (14, 30, 20, 40, 18, 45, 45, 20, 12)):
        coverage.column_dimensions[letter].width = width

    review = workbook.create_sheet("Review")
    review.append(list(REVIEW_HEADERS))
    for item in review_rows:
        _append(review, [item.get(name, "") for name in REVIEW_HEADERS], wrap)
    for letter, width in zip("ABCDEFGH", (14, 30, 20, 20, 50, 40, 14, 12)):
        review.column_dimensions[letter].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return {"rows": len(rows), "coverage_rows": len(coverage_rows or []), "review_rows": len(review_rows),
            "template_sheet": template_info[2] if template_info else ""}


def draft_rows_from_finding(finding: dict, version: str = "", checklist: dict | None = None) -> list[dict]:
    """Finding 의 초안을 Checklist 행으로 바꾼다. 열 이름은 제품 형식을 따른다."""
    _, columns = _profile(checklist)
    category = (checklist or {}).get("category") or "Changes Checklist"
    rows = []
    for draft in finding.get("draft_tcs") or []:
        values = {
            "category": category,
            "tc_id": "",
            "version": version,
            "srs_no": draft.get("srs_no", "") or "N/A",
            "change": draft.get("change", "") or finding.get("subject_title", ""),
            "change_detail": draft.get("change_detail", ""),
            "title": draft.get("title", ""),
            "precondition": draft.get("precondition", ""),
            "test_step": draft.get("test_step", ""),
            "expected_result": draft.get("expected_result", ""),
            "test_data": draft.get("test_data", ""),
        }
        rows.append({columns[key]: value for key, value in values.items() if key in columns})
    return rows


def coverage_rows_from_finding(finding: dict) -> list[dict]:
    """Coverage Finding → TC 별 조치 행 (REQ-QAINTEL-018 2번)."""
    sections = finding.get("sections") or {}
    base = {
        "SRS No": finding.get("subject", ""),
        "변경사항": (sections.get("change") or {}).get("summary", "") or finding.get("summary", ""),
        "Coverage": finding.get("verdict", ""),
        "관련 Issue": ", ".join(entry["issue_id"] for entry in (sections.get("issue_coverage") or {}).get("issues") or []),
        "Finding 번호": finding.get("id", ""),
    }
    rows = []
    for entry in (sections.get("checklist_coverage") or {}).get("tcs") or []:
        rows.append({**base, "TC 위치": entry.get("location") or entry.get("tc_id", ""), "조치": entry.get("decision", ""),
                     "이유": entry.get("reason", ""), "추천 수정": entry.get("recommended_change", "")})
    for draft in finding.get("draft_tcs") or []:
        rows.append({**base, "TC 위치": "(신규)", "조치": "CREATE_NEW", "이유": draft.get("rationale", ""),
                     "추천 수정": draft.get("title", "")})
    if finding.get("verdict") == "SPEC_REVIEW_REQUIRED":
        hold = sections.get("tc_hold") or {}
        rows.append({**base, "TC 위치": "", "조치": "SPEC_REVIEW_REQUIRED", "이유": hold.get("reason", "Checklist TC 생성 보류"),
                     "추천 수정": "\n".join(hold.get("questions") or [])})
    return rows
