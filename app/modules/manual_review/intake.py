"""파일 이름으로 검증 정보 채우기 (REQ-MANUAL-019).

QA 가 개정 매뉴얼을 고르면 파일 이름만 보고 제품·매뉴얼 종류·제품 버전·이어받을 이전 검증을 제안한다.
제안은 저장하지 않는다. 등록은 "검증 시작"(REQ-MANUAL-002)에서만 한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from app.core.product_knowledge import parse_asset_name

SUPPORTED_SUFFIXES = (".docx", ".pdf")
PARENT_STATUSES = ("REVIEWED", "BASELINE")
_ROUND_RE = re.compile(r"W(\d+)$", re.IGNORECASE)


def _fold(value: str) -> str:
    """대소문자와 공백 차이를 무시하고 비교하기 위한 형태."""
    return re.sub(r"\s+", "", value or "").casefold()


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version or "")) or (0,)


def revision_label(target_version: str, parent_round: int | None, pdf_baseline: bool = False) -> str:
    """리비전 표기 (REQ-MANUAL-003). 화면 등록과 제안이 같은 함수를 쓴다."""
    suffix = "Baseline" if pdf_baseline else f"W{(parent_round + 2) if parent_round is not None else 1}"
    return f"V{target_version} · {suffix}"


def _pick_product(document_name: str, products: list[str], notes: list[str]) -> str:
    folded = _fold(document_name)
    matches = [product for product in products if _fold(product) and _fold(product) in folded]
    if matches:
        return max(matches, key=len)
    if len(products) == 1:
        return products[0]
    notes.append("파일 이름에서 제품을 찾지 못했습니다.")
    return ""


def _pick_manual(document_name: str, manual_types: list[str], notes: list[str]) -> str:
    folded = _fold(document_name)
    for name in manual_types:
        if _fold(name) == folded:
            return name
    contained = [name for name in manual_types if _fold(name) and _fold(name) in folded]
    if contained:
        return max(contained, key=lambda name: len(_fold(name)))
    if document_name:
        notes.append("목록에 없는 매뉴얼 이름입니다. 새 매뉴얼이면 그대로 두세요.")
    return document_name


def _pick_version(file_version: str, registered: list[str], notes: list[str]) -> str:
    """REQ-MANUAL-019 4번 표."""
    newest = max(registered, key=_version_key) if registered else ""
    if not file_version:
        notes.append("파일 이름에 버전이 없습니다.")
        return newest
    if not registered or file_version in registered:
        return file_version
    file_key, newest_key = _version_key(file_version), _version_key(newest)
    if file_key > newest_key and file_key[0] == newest_key[0]:
        notes.append(f"새 제품 버전 V{file_version}으로 시작합니다.")
        return file_version
    notes.append(f"파일 이름의 V{file_version}은 등록된 제품 버전이 아니어서 V{newest}을 제안합니다. 다르면 고치세요.")
    return newest


def _pick_parent(revisions: list[dict], product: str, manual_name: str, target_version: str, is_pdf: bool) -> dict | None:
    """같은 제품·매뉴얼·버전의 완료·Baseline 리비전 가운데 가장 최근 것. PDF 면 PDF 리비전만."""
    candidates = [
        revision for revision in revisions
        if revision.get("product") == product
        and revision.get("manual_name") == manual_name
        and (revision.get("target_version") or "") == target_version
        and revision.get("status") in PARENT_STATUSES
        and (not is_pdf or str(revision.get("source_path") or "").lower().endswith(".pdf"))
    ]
    return max(candidates, key=lambda revision: int(revision["id"])) if candidates else None


def suggest_revision_inputs(
    file_name: str,
    products: list[str],
    manual_types_by_product: dict[str, list[str]],
    versions_by_product: dict[str, list[str]],
    revisions: list[dict],
    open_comment_count: Callable[[int], int] | None = None,
) -> dict:
    """파일 이름 하나로 검증 입력값을 제안한다 (REQ-MANUAL-019 순서 1~7번)."""
    parsed = parse_asset_name(file_name)
    document_name = parsed["base_name"]
    notes: list[str] = []
    is_pdf = Path(file_name).suffix.lower() == ".pdf"

    product = _pick_product(document_name, products, notes)
    manual_name = _pick_manual(document_name, manual_types_by_product.get(product, []), notes) if product else document_name

    file_revision = parsed["revision"] if parsed["revision_kind"] == "version" else ""
    round_match = _ROUND_RE.search(file_revision)
    file_version = _ROUND_RE.sub("", file_revision).removeprefix("V")
    target_version = _pick_version(file_version, versions_by_product.get(product, []), notes) if product else file_version

    parent = _pick_parent(revisions, product, manual_name, target_version, is_pdf) if product and target_version else None
    label = ""
    if target_version:
        label = revision_label(target_version, int(parent["round_number"]) if parent else None, is_pdf and not parent)
        if round_match and not label.endswith(f"W{int(round_match.group(1))}"):
            notes.append(f"파일 이름은 W{int(round_match.group(1))}이지만 검증 회차는 {label.split(' · ')[-1]}입니다.")

    parent_id = int(parent["id"]) if parent else None
    return {
        "product": product,
        "manual_name": manual_name,
        "target_version": target_version,
        "parent_revision_id": parent_id,
        "parent_label": f"#{parent_id} {parent['manual_name']} {parent['revision_label']}" if parent else "",
        "open_comment_count": open_comment_count(parent_id) if parent_id and open_comment_count else 0,
        "revision_label": label,
        "file_revision": parsed["revision"],
        "notes": notes,
        "complete": bool(product and manual_name and target_version),
    }
