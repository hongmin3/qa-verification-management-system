"""사람이 올린(또는 ALM 동기화가 보낸) 사양서·매뉴얼·TC 의 등록과 안전한 교체 (SPEC REQ-KNOW-003·004).

순서: 출처 확인 → 새 파일 저장(라우터) → 파싱 확인 → 문서 표 등록 → 파싱 저장본 → 그 뒤 이전 등록 정리.
새 파일이 어느 단계에서 실패해도 기존 정상 문서는 분석 대상에 남는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.core import document_cache
from app.core.product_config import KIND_LABELS, SOURCE_ALM, SOURCE_EXTERNAL, ProductConfig, registration_refusal
from app.core.product_knowledge import (
    DATE_REVISION_RE,
    REGISTERABLE_KINDS,
    REV_REVISION_RE,
    VERSION_REVISION_RE,
    classify,
    document_logical_key,
    is_strictly_older_name,
    parse_asset_name,
)

SPEC_EXTENSIONS = {".pdf", ".docx"}
#: 사양서는 Excel 로 관리하는 문서(VSS Specification 등)도 있다. 파서가 `.xlsx` 본문을 읽는다 (REQ-KNOW-018).
SPECIFICATION_EXTENSIONS = SPEC_EXTENSIONS | {".xlsx"}
TC_EXTENSIONS = {".xlsx"}
#: 등록 부가 정보의 출처 가운데 사람이 올리지 않은 것. 나머지(`upload`, 빈 값)는 사람이 올린 등록이다 (REQ-KNOW-005).
AUTOMATIC_DOCUMENT_SOURCES = frozenset({"product_knowledge", SOURCE_ALM, SOURCE_EXTERNAL})


class RegistrationError(Exception):
    """화면에 그대로 보일 이유와 HTTP 상태. 메시지는 SPEC REQ-KNOW-003 표의 문구다."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class NeedsColumnMapping(Exception):
    """TC ID 열을 자동으로 찾지 못했다. 파일은 지우지 않고 열 지정 화면으로 보낸다 (REQ-KNOW-004)."""


@dataclass
class Registration:
    document_id: int
    name: str
    replaced: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"등록 1건, 교체 {len(self.replaced)}건"


def extensions_for(kind: str) -> set[str]:
    if kind == "testcase":
        return TC_EXTENSIONS
    return SPECIFICATION_EXTENSIONS if kind == "specification" else SPEC_EXTENSIONS


_REVISION_PATTERNS = {"date": DATE_REVISION_RE, "version": VERSION_REVISION_RE, "rev": REV_REVISION_RE}


def _revision_value(revision: str, revision_kind: str) -> str:
    """`parse_asset_name` 의 리비전 값을 정규식 첫 묶음에 들어갈 글자로 바꾼다 (`Rev1.18` → `1.18`)."""
    return revision[3:] if revision_kind == "rev" else revision


def replacement_name(target_name: str, uploaded_name: str, today: date) -> str:
    """`교체` 로 올린 파일의 등록 이름. 대상 이름에서 리비전만 바꾸고 확장자는 올린 파일을 따른다 (REQ-KNOW-023)."""
    target_path, uploaded_path = Path(target_name), Path(uploaded_name)
    stem, extension = target_path.stem, uploaded_path.suffix.lower() or target_path.suffix
    target, uploaded = parse_asset_name(target_name), parse_asset_name(uploaded_name)
    kind = target["revision_kind"]
    pattern = _REVISION_PATTERNS.get(kind)
    match = pattern.search(stem) if pattern else None
    if match is None:
        return stem + extension
    if uploaded["revision_kind"] == kind:
        value = _revision_value(uploaded["revision"], kind)
    elif kind == "date":
        value = today.strftime("%y%m%d")
    else:
        return stem + extension
    return stem[: match.start(1)] + value + stem[match.end(1):] + extension


def kind_mismatch(config: ProductConfig, kind: str, file_name: str) -> str | None:
    """파일 이름으로 보면 문서 표의 다른 종류일 때 그 종류. 규약에 맞지 않는 이름은 None (REQ-KNOW-003·019)."""
    named = classify(re.split(r"[\\/]", file_name)[-1], config.knowledge_source.classify or None)
    return named if named in REGISTERABLE_KINDS and named != kind else None


def registered_by_person(document: dict) -> bool:
    """사람이 올린 등록인가. 자동 출처 구역에 있어도 지울 수 있다 (REQ-KNOW-005)."""
    return str(_metadata(document).get("source") or "") not in AUTOMATIC_DOCUMENT_SOURCES


def check_request(storage, config: ProductConfig, kind: str, declared_source: str = "", replace_id: int | None = None,
                  *, file_name: str = "") -> dict | None:
    """파일을 저장하기 전에 받을 수 있는 요청인지 본다. 교체 대상 문서를 돌려준다."""
    refusal = registration_refusal(config, kind, declared_source)
    if refusal:
        raise RegistrationError(409, refusal)
    named = kind_mismatch(config, kind, file_name) if file_name else None
    if named:
        raise RegistrationError(400, f"파일 이름으로 보면 {KIND_LABELS[named]} 문서라 {KIND_LABELS.get(kind, kind)}(으)로 "
                                     f"등록할 수 없습니다: {file_name}")
    if replace_id is None:
        return None
    target = storage.get_document(replace_id)
    if not target or target["product"] != config.product or target["kind"] != kind:
        raise RegistrationError(400, "교체할 문서를 찾을 수 없습니다.")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse(kind: str, path: Path, tc_mapping: dict | None):
    """(파싱 저장본에 넣을 목록, 전문 텍스트 또는 None)."""
    if kind == "testcase":
        from app.parsers.excel_parser import parse_testcases

        try:
            if tc_mapping is None:
                return parse_testcases(path), None
            return parse_testcases(path, mapping=tc_mapping["mapping"], sheet_name=tc_mapping["sheet"],
                                   header_row=tc_mapping["header_row"]), None
        except ValueError as exc:
            if tc_mapping is None:
                raise NeedsColumnMapping(str(exc)) from exc
            raise
    from app.parsers.document_parser import extract_document_text, parse_document

    try:
        chunks = parse_document(path, path.stem)
        text = extract_document_text(path)
    except Exception as exc:  # 파서마다 예외 종류가 다르다. 어떤 실패든 기존 문서를 지킨다.
        raise RegistrationError(422, f"파일을 읽지 못해 등록하지 않았습니다. 기존 문서는 그대로 분석에 쓰입니다: {exc}") from exc
    if not chunks:
        raise RegistrationError(422, "파일을 읽지 못해 등록하지 않았습니다. 기존 문서는 그대로 분석에 쓰입니다: 읽을 수 있는 내용이 없습니다")
    return chunks, text


def _metadata(document: dict) -> dict:
    import json

    try:
        return json.loads(document.get("metadata_json") or "{}")
    except ValueError:
        return {}


def _remove_file_if_owned(storage, document: dict, owned_dirs: list[Path]) -> None:
    """사람이 올린 파일 폴더 안에 있고 다른 등록이 같은 파일을 쓰지 않을 때만 원본을 지운다."""
    path = Path(document["path"])
    try:
        resolved = path.resolve()
    except OSError:
        return
    if not any(resolved.is_relative_to(directory.resolve()) for directory in owned_dirs):
        return
    for kind in REGISTERABLE_KINDS:
        for other in storage.list_documents(kind):
            if other["id"] != document["id"] and Path(other["path"]).resolve() == resolved:
                return
    path.unlink(missing_ok=True)


def register_uploaded(storage, config: ProductConfig, kind: str, saved_path: Path, original_name: str, *,
                      replace_target: dict | None = None, declared_source: str = "", version: str = "",
                      tc_mapping: dict | None = None, owned_dirs: list[Path] | None = None) -> Registration:
    """저장된 새 파일을 등록하고, 등록이 끝난 뒤에 같은 논리 문서의 이전 등록을 뺀다.

    실패하면 새 파일을 지우고 `RegistrationError` 를 낸다. TC 자동 탐지 실패만 `NeedsColumnMapping` 으로
    알리고 파일을 남긴다. 기존 등록은 어느 경우에도 건드리지 않는다.
    """
    name = original_name or saved_path.name
    try:
        items, text = _parse(kind, saved_path, tc_mapping)
    except NeedsColumnMapping:
        raise
    except RegistrationError:
        saved_path.unlink(missing_ok=True)
        raise

    new_key = document_logical_key(kind, name)
    existing = storage.active_documents(kind, config.product)
    same = [doc for doc in existing if document_logical_key(kind, doc["name"], _metadata(doc)) == new_key]
    targets = {doc["id"]: doc for doc in same}
    if replace_target is not None:
        targets[replace_target["id"]] = replace_target
    elif same:
        newer = [doc for doc in same if is_strictly_older_name(name, doc["name"])]
        if newer:
            saved_path.unlink(missing_ok=True)
            raise RegistrationError(409, f"같은 문서의 더 최신 판이 이미 등록돼 있습니다: {newer[0]['name']}. "
                                         "이 파일로 바꾸려면 그 문서의 교체를 쓰세요.")

    parsed = parse_asset_name(name)
    metadata = {
        "source": declared_source.strip() or "upload", "base_name": parsed["base_name"],
        "revision_kind": parsed["revision_kind"], "language": parsed["language"], "doc_number": parsed["doc_number"],
        "sha256": _sha256(saved_path),
    }
    if kind == "testcase":
        if tc_mapping is not None:
            metadata.update({"column_mapping": tc_mapping["mapping"], "sheet_name": tc_mapping["sheet"], "header_row": tc_mapping["header_row"]})
    else:
        metadata["chunk_count"] = len(items)

    storage.ensure_product(config.product)
    version = version.strip() or config.version
    storage.ensure_version(config.product, version)
    document_id = None
    try:
        document_id = storage.add_document(kind, config.product, version, parsed["revision"], name, saved_path, metadata)
        if not document_cache.save(document_id, items) or (text is not None and not document_cache.save_text(document_id, text)):
            raise OSError("파싱 저장본을 쓰지 못했습니다")
    except Exception as exc:
        if document_id is not None:
            storage.delete_document(document_id)
            document_cache.delete(document_id)
        saved_path.unlink(missing_ok=True)
        raise RegistrationError(500, f"등록 중 오류가 나 되돌렸습니다: {exc}") from exc

    storage.forget_deleted(config.product, kind, name)
    replaced = []
    for target in targets.values():
        storage.delete_document(target["id"])
        document_cache.delete(target["id"])
        _remove_file_if_owned(storage, target, owned_dirs or [])
        replaced.append(target["name"])
    return Registration(document_id=document_id, name=name, replaced=replaced)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
