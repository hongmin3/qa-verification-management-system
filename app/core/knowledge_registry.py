"""사람이 올린(또는 ALM 동기화가 보낸) 사양서·매뉴얼·TC 의 등록과 안전한 교체 (SPEC REQ-KNOW-003·004).

순서: 출처 확인 → 새 파일 저장(라우터) → 파싱 확인 → 문서 표 등록 → 파싱 저장본 → 그 뒤 이전 등록 정리.
새 파일이 어느 단계에서 실패해도 기존 정상 문서는 분석 대상에 남는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from app.core import document_cache
from app.core.product_config import KIND_LABELS, ProductConfig, registration_refusal
from app.core.product_knowledge import document_logical_key, is_strictly_older_name, parse_asset_name

SPEC_EXTENSIONS = {".pdf", ".docx"}
TC_EXTENSIONS = {".xlsx"}


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
    return TC_EXTENSIONS if kind == "testcase" else SPEC_EXTENSIONS


def check_request(storage, config: ProductConfig, kind: str, declared_source: str = "", replace_id: int | None = None) -> dict | None:
    """파일을 저장하기 전에 받을 수 있는 요청인지 본다. 교체 대상 문서를 돌려준다."""
    refusal = registration_refusal(config, kind, declared_source)
    if refusal:
        raise RegistrationError(409, refusal)
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
    for kind in ("specification", "testcase", "manual"):
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

    replaced = []
    for target in targets.values():
        storage.delete_document(target["id"])
        document_cache.delete(target["id"])
        _remove_file_if_owned(storage, target, owned_dirs or [])
        replaced.append(target["name"])
    return Registration(document_id=document_id, name=name, replaced=replaced)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)
