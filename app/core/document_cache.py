"""등록된 사양서/TC 문서를 매번 원본 PDF/DOCX/XLSX에서 다시 파싱하지 않도록,
document_id 기준으로 파싱 결과(Chunk/TestCase)를 `storage.index_dir`에 JSON으로
직렬화해 재사용한다.

실측(로컬 VXvue 데이터, 사양서 11개+TC 4개): `parse_document`+`parse_testcases`가
분석 1건당 약 7.2초인데 반해 그 결과로 BM25Retriever를 새로 만드는 비용은 약 0.3초에
불과하다 — 즉 비용은 BM25 인덱싱 자체가 아니라 원본 파일 파싱에 몰려 있다. 그래서
BM25Okapi 객체 자체를 직렬화하는 대신, 훨씬 단순하고 버전 호환 문제가 없는 방식으로
파싱 결과만 캐시하고 BM25 인덱스는 매번 그 결과로부터 가볍게 재구성한다.

새 리비전은 보통 새 document_id로 등록되지만, 리비전 표시가 없는 이름의 지식 폴더 파일은
같은 경로·같은 document_id에서 내용만 바뀔 수 있다. 그래서 이 모듈은 캐시를 스스로
무효화하지 않고, 등록 쪽(`app/core/product_knowledge.py::register_collected`)이 부가 정보의
sha256이 달라진 것을 보면 `delete`로 이 캐시를 지운다(REQ-KNOW-007). 있으면 쓰고 없으면
파싱 후 만들어 두면 된다."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.core.config import get_settings


def _cache_path(document_id: int) -> Path:
    return get_settings().path("storage.index_dir") / f"{document_id}.json"


def save(document_id: int, items: list[BaseModel]) -> bool:
    path = _cache_path(document_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        # 캐시는 성능 최적화일 뿐이므로 저장 실패가 문서 등록/분석 실패로 이어지면 안 된다.
        return False


def load(document_id: int, model: type[BaseModel]) -> list | None:
    """캐시가 없거나 손상됐으면 None을 반환한다 — 호출자가 원본을 다시 파싱하고
    다시 `save`하면 된다(자가 치유, 이 캐시 도입 이전에 등록된 문서도 자연스럽게 채워짐)."""
    path = _cache_path(document_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [model.model_validate(item) for item in data]
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def delete(document_id: int) -> None:
    for path in (_cache_path(document_id), _text_cache_path(document_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # 원본/DB 삭제가 캐시 파일 잠금 때문에 실패하지 않도록 한다.
            pass


def _text_cache_path(document_id: int) -> Path:
    return get_settings().path("storage.index_dir") / f"{document_id}.text"


def save_text(document_id: int, text: str) -> bool:
    """`extract_document_text`(Rule 기반 diff용 원문 전체) 결과 캐시. Chunk와 별개 캐시라서
    분리했다 — 사양서만 있고 TC엔 해당하지 않는다."""
    path = _text_cache_path(document_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def load_text(document_id: int) -> str | None:
    path = _text_cache_path(document_id)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
