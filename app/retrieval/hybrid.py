"""Exact → BM25 단계 검색 (로드맵 Layer 3 — 1차 Exact, 2차 BM25).

두 검색기를 **점수로 합치지 않고 자리로 나눈다.** 점수를 정규화해 섞으면 "왜 이 순위인지"를
설명할 수 없고, exact 히트라는 가장 강한 근거가 BM25 점수에 묻힌다.

    ┌ exact 히트 (식별자·문구 정확 일치)      → 앞자리를 먼저 차지
    └ BM25 상위 (의미·용어 유사)              → 남은 자리만 채움

**토큰 절약이 이 설계의 목적이다.** exact 로 근거가 확보되면 BM25 후보를 그만큼 덜 보낸다
(`adaptive` 옵션). Issue 에 linked SRS 가 붙어 있으면 그것이 가장 확실한 근거이므로, 사양
Chunk 를 8개 보내는 대신 3~4개로 줄여도 판정 품질이 떨어지지 않는다.

로드맵의 3차 Semantic Search·4차 AI Re-ranking 은 이 계층에 구현하지 않았다. 지금 필요한
Recall 이 exact + BM25 로 확보되는지 먼저 측정한 뒤 결정할 문제이고, Embedding 을 쓰면
사내 문서를 외부로 보내야 해서 이 프로젝트의 전제와 충돌한다 (로컬 임베딩 모델이 준비되면
`Retriever` Protocol 구현체를 하나 더 추가하면 된다).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.retrieval.bm25_retriever import BM25Retriever
from app.retrieval.exact_retriever import ExactHit, ExactRetriever

T = TypeVar("T")

SOURCE_EXACT = "exact"
SOURCE_BM25 = "bm25"


@dataclass(frozen=True)
class RetrievedItem(Generic[T]):
    """검색 결과 하나. 어떤 검색으로 어떤 근거로 걸렸는지 함께 남긴다."""

    item: T
    source: str
    score: float
    #: exact 로 걸렸을 때 일치한 검색어. BM25 결과는 빈 문자열.
    matched_term: str = ""

    @property
    def reason(self) -> str:
        if self.source == SOURCE_EXACT:
            return f"'{self.matched_term}' 정확 일치"
        return f"용어 유사도 {self.score:.2f}"


@dataclass
class RetrievalResult(Generic[T]):
    items: list[RetrievedItem[T]]
    exact_count: int
    bm25_count: int
    bm25_budget: int
    terms: list[str]

    @property
    def values(self) -> list[T]:
        return [entry.item for entry in self.items]

    def summary(self) -> dict:
        """감사 화면·Evidence 에 남길 검색 요약. 왜 이만큼만 보냈는지가 남아야 한다."""
        return {
            "exact": self.exact_count,
            "bm25": self.bm25_count,
            "bm25_budget": self.bm25_budget,
            "terms": self.terms[:20],
            "total": len(self.items),
        }


class HybridRetriever(Generic[T]):
    def __init__(self, items: Sequence[T], text_getter: Callable[[T], str], key_getter: Callable[[T], str]) -> None:
        self.items = list(items)
        self.key_getter = key_getter
        self.exact = ExactRetriever(self.items, text_getter)
        self.bm25 = BM25Retriever(self.items, text_getter)

    def search(
        self,
        terms: Sequence[str],
        query: str = "",
        top_k: int = 8,
        exact_limit: int = 0,
        adaptive: bool = True,
        min_bm25: int = 2,
    ) -> RetrievalResult[T]:
        """exact 히트를 먼저 채우고 남은 자리를 BM25 로 채운다.

        `top_k` 는 **총 반환 개수 상한**이다. BM25 자리를 `min_bm25` 만큼 남겨 두므로 exact
        히트가 많아도 전부 exact 로 채우지 않는다 — exact 히트가 한 문서에 몰려 있을 때
        다른 근거를 놓치지 않기 위함이다.

        `adaptive=True` 이고 exact 히트가 있으면 **총 개수 자체를 줄인다**
        (`len(exact) + min_bm25`). 확실한 근거가 확보됐으면 LLM 에 보낼 후보를 늘릴 이유가
        없다. exact 히트가 없으면 줄이지 않고 `top_k` 를 BM25 로 채운다.
        """
        exact_hits: list[ExactHit[T]] = self.exact.search_terms(terms, limit=exact_limit or top_k)
        exact_cap = max(top_k - min_bm25, 1)
        selected: list[RetrievedItem[T]] = []
        seen: set[str] = set()
        for hit in exact_hits:
            key = self.key_getter(hit.item)
            if key in seen or len(selected) >= exact_cap:
                continue
            seen.add(key)
            selected.append(RetrievedItem(item=hit.item, source=SOURCE_EXACT, score=hit.score, matched_term=hit.term))

        total_target = min(top_k, len(selected) + min_bm25) if adaptive and selected else top_k
        bm25_budget = max(total_target - len(selected), 0)
        bm25_query = query or " ".join(terms)
        bm25_count = 0
        if bm25_budget and bm25_query.strip():
            # 중복 제거 후 예산을 채우려면 넉넉히 받아온 뒤 걸러낸다.
            for item, score in self.bm25.search(bm25_query, top_k=bm25_budget + len(seen)):
                key = self.key_getter(item)
                if key in seen or bm25_count >= bm25_budget:
                    continue
                seen.add(key)
                selected.append(RetrievedItem(item=item, source=SOURCE_BM25, score=float(score)))
                bm25_count += 1

        return RetrievalResult(
            items=selected,
            exact_count=sum(entry.source == SOURCE_EXACT for entry in selected),
            bm25_count=bm25_count,
            bm25_budget=bm25_budget,
            terms=[term for term in terms if term],
        )
