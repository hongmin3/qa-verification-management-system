"""product의 기존 등록 사양서(SRS) 문서를 로컬에서 청크·BM25 검색해 매뉴얼 변경사항별
근거 후보를 찾는다.

Knowledge 가 관리하는 'specification' 문서
(`app/modules/knowledge/vxvue_spec_sync.py`로 최신화됨)를 그대로 재사용한다.
"""

from __future__ import annotations

from pathlib import Path

from app.core import document_cache
from app.core.storage import Storage
from app.core.document_schemas import SpecificationChunk
from app.parsers.document_parser import parse_document
from app.retrieval.bm25_retriever import BM25Retriever


def load_srs_chunks(storage: Storage, product: str) -> tuple[list[SpecificationChunk], dict[str, str]]:
    """product에 등록된 모든 'specification' 문서를 청크로 변환한다. 두번째 반환값은
    chunk.document_id -> 사람이 읽는 문서명 매핑(사양서 인용 표시용)."""
    chunks: list[SpecificationChunk] = []
    doc_labels: dict[str, str] = {}
    for doc in storage.active_documents("specification", product):
        path = Path(doc["path"])
        if not path.exists():
            continue
        cached_chunks = document_cache.load(doc["id"], SpecificationChunk)
        if cached_chunks is None:
            cached_chunks = parse_document(path, path.stem)
            document_cache.save(doc["id"], cached_chunks)
        chunks.extend(cached_chunks)
        doc_labels[path.stem] = doc["name"]
    return chunks, doc_labels


def search_candidates(chunks: list[SpecificationChunk], query: str, top_k: int = 6) -> list[SpecificationChunk]:
    """query와 관련성 높은 SRS chunk 상위 top_k개만 반환한다 (스펙 §28-G max_srs_candidates)."""
    if not query.strip() or not chunks:
        return []
    retriever = BM25Retriever(chunks, lambda chunk: f"{chunk.heading} {chunk.text}")
    return [chunk for chunk, _ in retriever.search(query, top_k)]
