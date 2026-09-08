"""등록된 제품 문서를 파싱 캐시와 함께 읽는다 (사양 Chunk · TC).

Regression 영향 분석과 QA Agent 가 같은 방식으로 같은 문서를 읽어야 한다. 두 기능이 서로
다른 문서 집합을 보면 같은 Issue 에 대해 다른 결론이 나오고 그 차이를 설명할 수 없다.

중요한 규칙 하나: **등록된 모든 문서가 항상 검색 대상이다** (`Storage.active_documents`).
제품·버전당 최신 리비전 1개만 고르면 안 된다 — 사양서1~5 처럼 서로 다른 문서가 같은 제품에
여러 개 등록되고, 새 문서가 이전 문서를 레거시로 만들지 않는다. 이 규칙은 과거에
`latest_documents` 방식으로 구현했다가 사용자 지적으로 뒤집힌 것이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.core import document_cache
from app.core.document_schemas import SpecificationChunk, TestCase
from app.core.storage import Storage
from app.parsers.document_parser import extract_document_text, parse_document
from app.parsers.excel_parser import parse_testcases


@dataclass
class LoadedKnowledge:
    """한 제품의 검색 대상 문서 전체."""

    product: str
    chunks: list[SpecificationChunk] = field(default_factory=list)
    cases: list[TestCase] = field(default_factory=list)
    baseline_texts: list[str] = field(default_factory=list)
    #: `parse_document` 가 만든 document_id(파일 stem) → 사람이 읽는 문서명.
    document_labels: dict[str, str] = field(default_factory=dict)
    specification_documents: list[dict] = field(default_factory=list)
    testcase_documents: list[dict] = field(default_factory=list)
    #: 읽지 못한 문서 (파일 없음·파싱 실패). 조용히 빠지면 "사양 없음" 오판으로 이어진다.
    failures: list[dict] = field(default_factory=list)

    @property
    def specification_label(self) -> str:
        return ", ".join(document["name"] for document in self.specification_documents)

    @property
    def testcase_label(self) -> str:
        return ", ".join(document["name"] for document in self.testcase_documents)

    @property
    def chunk_ids(self) -> set[str]:
        return {chunk.chunk_id for chunk in self.chunks}

    @property
    def tc_ids(self) -> set[str]:
        return {case.tc_id for case in self.cases}

    def knowledge_documents(self) -> list[dict]:
        """감사 기록에 남길 문서 목록 (경로·metadata 는 제외)."""
        return [
            {key: document.get(key) for key in ("id", "kind", "product", "version", "revision", "name", "created_at")}
            for document in (*self.specification_documents, *self.testcase_documents)
        ]


def load_specification_chunks(
    documents: list[dict], with_text: bool = False, failures: list[dict] | None = None
) -> tuple[list[SpecificationChunk], list[str], dict[str, str]]:
    """사양서 문서 목록 → (Chunk, 전문 텍스트, 문서명 표). 파싱 결과는 캐시를 재사용한다.

    문서 하나가 사라졌거나 파싱에 실패해도 예외를 올리지 않고 건너뛴다. 등록된 문서 중
    파일이 없는 것이 실제로 있었고(업로드 정리로 원본이 사라진 옛 등록), 그 하나 때문에
    분석 전체가 죽었다. 대신 무엇을 못 읽었는지 `failures` 에 남긴다 — 조용히 빠지면
    "사양 없음" 오판으로 이어진다.
    """
    chunks: list[SpecificationChunk] = []
    texts: list[str] = []
    labels: dict[str, str] = {}
    for document in documents:
        path = Path(document["path"])
        try:
            cached_chunks = document_cache.load(document["id"], SpecificationChunk)
            if cached_chunks is None:
                cached_chunks = parse_document(path, path.stem)
                document_cache.save(document["id"], cached_chunks)
            text = None
            if with_text:
                text = document_cache.load_text(document["id"])
                if text is None:
                    text = extract_document_text(path)
                    document_cache.save_text(document["id"], text)
        except Exception as exc:
            reason = "파일이 없습니다" if not path.is_file() else f"{type(exc).__name__}: {exc}"
            if failures is not None:
                failures.append({"kind": "specification", "id": document["id"], "name": document["name"], "reason": reason})
            continue
        chunks.extend(cached_chunks)
        labels[path.stem] = document["name"]
        if text is not None:
            texts.append(text)
    return chunks, texts, labels


def load_test_cases(documents: list[dict], failures: list[dict] | None = None) -> list[TestCase]:
    """TC 문서 목록 → TestCase. QA 가 수동 지정한 컬럼/시트 매핑을 존중한다."""
    cases: list[TestCase] = []
    for document in documents:
        # register_testcase 가 자동 탐지에 실패하면 QA 가 /knowledge/testcase/map 에서 지정한
        # 컬럼·시트·헤더 행이 metadata_json 에 들어 있다 (없으면 자동 탐지).
        metadata = json.loads(document.get("metadata_json") or "{}")
        path = Path(document["path"])
        try:
            cached_cases = document_cache.load(document["id"], TestCase)
            if cached_cases is None:
                cached_cases = parse_testcases(
                    path,
                    mapping=metadata.get("column_mapping"),
                    sheet_name=metadata.get("sheet_name"),
                    header_row=metadata.get("header_row"),
                )
                document_cache.save(document["id"], cached_cases)
        except Exception as exc:
            reason = "파일이 없습니다" if not path.is_file() else f"{type(exc).__name__}: {exc}"
            if failures is not None:
                failures.append({"kind": "testcase", "id": document["id"], "name": document["name"], "reason": reason})
            continue
        cases.extend(cached_cases)
    return cases


def load_for_product(product: str, storage: Storage | None = None, with_text: bool = False, require_both: bool = True) -> LoadedKnowledge:
    """제품에 등록된 사양서·TC 전체를 읽는다.

    `require_both=False` 면 한쪽이 없어도 예외를 올리지 않는다 — QA Agent 는 무엇이 없는지를
    Gate 판정(G1)으로 보고해야 하고, 예외로 끝내면 그 이유가 결과에 남지 않는다.
    """
    storage = storage or Storage()
    specification_documents = storage.active_documents("specification", product)
    testcase_documents = storage.active_documents("testcase", product)
    if require_both and (not specification_documents or not testcase_documents):
        raise ValueError(f"'{product}' 제품에 등록된 사양서 또는 TC가 없습니다.")

    failures: list[dict] = []
    chunks, texts, labels = load_specification_chunks(specification_documents, with_text=with_text, failures=failures)
    return LoadedKnowledge(
        product=product,
        chunks=chunks,
        cases=load_test_cases(testcase_documents, failures=failures),
        baseline_texts=texts,
        document_labels=labels,
        specification_documents=specification_documents,
        testcase_documents=testcase_documents,
        failures=failures,
    )
