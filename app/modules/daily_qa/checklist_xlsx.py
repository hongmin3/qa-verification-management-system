"""이슈 수정확인·Regression 초안 Excel (SPEC REQ-DAILY-004).

열 순서는 변경사항 영향성평가 Checklist 와 같다. 원본 Checklist 가 있으면 가장 앞쪽의
버전 시트에서 머리글 행의 글꼴·채우기·테두리·열 너비를 복사한다 (QA 규칙 G6: 예시 파일의
서식을 우선 재사용하고, 본문 셀에는 임의 배경색을 넣지 않는다). 원본은 읽기 전용으로 연다.

실행용 시트(`Checklist 초안`)에는 등록 가능한 열만 두고, AI 판단·근거는 `Review` 시트에
따로 둔다 (QA 규칙 §75).
"""

from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

CHECKLIST_HEADERS = (
    "Category", "TC ID", "버전", "SRS No", "변경사항", "변경사항 상세",
    "Title", "Precondition", "Test Step", "Expected Result", "Test Data",
)
REVIEW_HEADERS = ("이슈 ID", "이슈 제목", "이슈 유형", "판정", "요약", "근거 위치", "신뢰도", "Finding 번호")
DEFAULT_WIDTHS = (18, 20, 10, 12, 18, 30, 30, 30, 45, 45, 20)


def _template_header(template: Path | None):
    """원본 Checklist 에서 'Category … Test Data' 머리글 행을 찾는다. 없으면 None."""
    if not template or not template.is_file():
        return None
    workbook = load_workbook(template, read_only=False)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=8):
            values = [str(cell.value or "").strip() for cell in row]
            if "Category" in values and "Expected Result" in values:
                start = values.index("Category")
                cells = row[start:start + len(CHECKLIST_HEADERS)]
                widths = []
                for cell in cells:
                    dimension = sheet.column_dimensions.get(cell.column_letter)
                    widths.append(dimension.width if dimension and dimension.width else None)
                return cells, widths, sheet.title
    return None


def write_draft(path: Path, rows: list[dict], review_rows: list[dict], template: Path | None = None) -> dict:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Checklist 초안"
    sheet.append(list(CHECKLIST_HEADERS))
    template_info = _template_header(template)
    for index, header_cell in enumerate(sheet[1]):
        if template_info:
            cells, widths, _ = template_info
            source = cells[index] if index < len(cells) else None
            if source is not None and source.has_style:
                header_cell.font = copy(source.font)
                header_cell.fill = copy(source.fill)
                header_cell.border = copy(source.border)
                header_cell.alignment = copy(source.alignment)
            width = widths[index] if index < len(widths) else None
        else:
            width = None
        sheet.column_dimensions[header_cell.column_letter].width = width or DEFAULT_WIDTHS[index]
    wrap = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        sheet.append([row.get(name, "") for name in CHECKLIST_HEADERS])
        for cell in sheet[sheet.max_row]:
            cell.alignment = wrap
    sheet.freeze_panes = "A2"

    review = workbook.create_sheet("Review")
    review.append(list(REVIEW_HEADERS))
    for item in review_rows:
        review.append([item.get(name, "") for name in REVIEW_HEADERS])
        for cell in review[review.max_row]:
            cell.alignment = wrap
    for letter, width in zip("ABCDEFGH", (12, 30, 18, 14, 50, 40, 14, 12)):
        review.column_dimensions[letter].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return {"rows": len(rows), "review_rows": len(review_rows), "template_sheet": template_info[2] if template_info else ""}


def draft_rows_from_finding(finding: dict, version: str = "") -> list[dict]:
    rows = []
    for draft in finding.get("draft_tcs") or []:
        rows.append(
            {
                "Category": "Changes Checklist",
                "TC ID": "",
                "버전": version,
                "SRS No": draft.get("srs_no", "") or "N/A",
                "변경사항": draft.get("change", "") or finding.get("subject_title", ""),
                "변경사항 상세": draft.get("change_detail", ""),
                "Title": draft.get("title", ""),
                "Precondition": draft.get("precondition", ""),
                "Test Step": draft.get("test_step", ""),
                "Expected Result": draft.get("expected_result", ""),
                "Test Data": draft.get("test_data", ""),
            }
        )
    return rows
