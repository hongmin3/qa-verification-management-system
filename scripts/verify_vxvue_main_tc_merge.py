"""Compare an approved Main TC change with the latest original and a candidate."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from openpyxl.worksheet.worksheet import Worksheet


def coordinate(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", value):
        raise ValueError("시트의 셀 주소를 확인하세요.")
    row, column = coordinate_to_tuple(value)
    if row > 1048576 or column > 16384:
        raise ValueError("XLSX 셀 범위를 벗어났습니다.")
    return row, column


def expected_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, str) and value:
        return ("s", value)
    if isinstance(value, int) or isinstance(value, float) and math.isfinite(value):
        return ("n", value)
    raise ValueError("승인 셀 값에는 문자열, 유한한 숫자, 불리언 또는 null을 쓰세요.")


def cell_value(cell):
    if cell is None or cell.value is None:
        return None
    value = cell.value
    if isinstance(value, (ArrayFormula, DataTableFormula)):
        value = vars(value)
    if isinstance(value, CellRichText):
        value = str(value)
    return cell.data_type, value


def cell_values(sheet):
    # Use the loaded cells. The used range may span many otherwise empty rows.
    return {address: cell_value(cell) for address, cell in sheet._cells.items()
            if cell.value is not None}


def read_snapshot(path):
    if path.suffix.lower() != ".xlsx":
        raise ValueError("원본과 후보는 .xlsx 파일이어야 합니다.")
    data = path.read_bytes()
    try:
        book = load_workbook(BytesIO(data), data_only=False, rich_text=True)
    except (KeyError, SyntaxError, BadZipFile, InvalidFileException) as exc:
        raise ValueError("XLSX 내부 구조를 읽을 수 없습니다.") from exc
    if any(not isinstance(book[name], Worksheet) for name in book.sheetnames):
        raise ValueError("일반 워크시트로 구성된 XLSX 파일을 사용하세요.")
    return book, sha256(data).hexdigest()


def validate(baseline_path, current_path, candidate_path, approvals_path):
    baseline, base_sha = read_snapshot(baseline_path)
    current, current_sha = read_snapshot(current_path)
    candidate, candidate_sha = read_snapshot(candidate_path)
    manifest = json.loads(approvals_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("baseline_sha256") != base_sha:
        raise ValueError("승인 목록의 기준 SHA-256이 지정한 원본과 다릅니다.")
    changes = manifest.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("승인 셀 목록은 하나 이상이어야 합니다.")

    pending, already_applied, conflicts, missing = [], [], [], []
    approved = set()
    for edit in changes:
        if (not isinstance(edit, dict)
                or set(edit) != {"sheet", "cell", "id_cell", "before", "after", "reason"}
                or not isinstance(edit["sheet"], str)
                or not isinstance(edit["reason"], str) or not edit["reason"].strip()):
            raise ValueError("승인 목록에 시트, 셀, 같은 행의 TC 식별 셀, 전후 값과 근거가 필요합니다.")
        sheet, address = edit["sheet"], edit["cell"]
        row, column = coordinate(address)
        identity = coordinate(edit["id_cell"])
        position = (sheet, (row, column))
        before = expected_value(edit["before"])
        after = expected_value(edit["after"])
        if identity[0] != row or identity == position[1] or position in approved or before == after:
            raise ValueError("승인 셀이 중복됐거나 TC 식별 셀 또는 전후 값이 올바르지 않습니다.")
        if sheet not in baseline.sheetnames or cell_value(baseline[sheet].cell(row, column)) != before:
            raise ValueError(f"승인 목록의 이전 값이 기준과 다릅니다 ({sheet}!{address}).")
        base_id = cell_value(baseline[sheet].cell(*identity))
        if base_id is None:
            raise ValueError(f"기준의 TC 식별 값이 비어 있습니다 ({sheet}!{edit['id_cell']}).")
        approved.add(position)
        label = f"{sheet}!{address}"
        if (sheet not in current.sheetnames
                or cell_value(current[sheet].cell(*identity)) != base_id):
            conflicts.append(label)
            continue
        live_value = cell_value(current[sheet].cell(row, column))
        if live_value == after:
            already_applied.append(label)
        elif live_value == before:
            pending.append(label)
        else:
            conflicts.append(label)
        if (sheet not in candidate.sheetnames
                or cell_value(candidate[sheet].cell(*identity)) != base_id
                or cell_value(candidate[sheet].cell(row, column)) != after):
            missing.append(label)

    structure = []
    if current.sheetnames != candidate.sheetnames:
        structure.append("시트 목록 또는 순서")
    unexpected = []
    for sheet in sorted(set(current.sheetnames) & set(candidate.sheetnames)):
        live, proposed = current[sheet], candidate[sheet]
        if live.sheet_state != proposed.sheet_state or live.merged_cells != proposed.merged_cells:
            structure.append(sheet)
        live_values, proposed_values = cell_values(live), cell_values(proposed)
        for position in sorted(live_values.keys() | proposed_values.keys()):
            if (sheet, position) not in approved and live_values.get(position) != proposed_values.get(position):
                row, column = position
                unexpected.append(f"{sheet}!{get_column_letter(column)}{row}")
    return {
        "passed": not (conflicts or missing or unexpected or structure),
        "baseline_sha256": base_sha, "current_sha256": current_sha, "candidate_sha256": candidate_sha,
        "pending": pending, "already_applied": already_applied,
        "conflicts": conflicts, "missing": missing, "unexpected": unexpected, "sheet_structure": structure,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("approvals", type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.baseline, args.current, args.candidate, args.approvals)
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
