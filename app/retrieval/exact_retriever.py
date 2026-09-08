"""정확 일치 검색 (로드맵 Layer 3 — 1차 Exact Search).

규칙 §9.1 이 정한 조사 키 — SRS No, Issue No, Command No, DICOM Tag, 기능명, 메뉴명 — 는
대부분 **문자열 그대로 일치**하는 식별자다. 여기에 BM25 를 쓰면 손해다.

- `VP-5500` 을 BM25 로 찾으면 토큰이 `vp`, `5500` 으로 쪼개져 무관한 Chunk 가 섞인다.
- `(0008,0018)` 같은 DICOM Tag 는 구두점이 토크나이저에서 사라진다.
- 반대로 exact 로 찾히면 그것이 가장 강한 근거다 — BM25 점수와 섞어 순위를 흐릴 이유가 없다.

그래서 순서를 나눈다: **exact 로 찾히면 그것을 먼저 쓰고, 남은 자리만 BM25 로 채운다**
(`hybrid.py`). exact 히트가 충분하면 BM25 후보 수를 줄여 LLM 에 보내는 양도 줄어든다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

# 식별자로 취급할 패턴. 이 형태의 검색어는 부분 일치가 아니라 경계 일치로 찾는다.
IDENTIFIER_RE = re.compile(r"^(?:[A-Za-z]{2,6}-\d{1,6}|SRS-?\d{1,6}|0[xX][0-9A-Fa-f]+|\(\s*[0-9A-Fa-f]{4}\s*,\s*[0-9A-Fa-f]{4}\s*\))$")


def is_identifier(term: str) -> bool:
    return bool(IDENTIFIER_RE.match(term.strip()))


def _boundary_pattern(term: str) -> re.Pattern[str]:
    """식별자를 경계 일치로 찾는 정규식.

    `VP-55` 가 `VP-5500` 에 걸리면 안 되므로 앞뒤에 영숫자·하이픈이 붙지 않아야 한다.
    """
    return re.compile(rf"(?<![0-9A-Za-z-]){re.escape(term)}(?![0-9A-Za-z-])", re.IGNORECASE)


@dataclass(frozen=True)
class ExactHit(Generic[T]):
    item: T
    term: str
    #: `identifier` (경계 일치) 또는 `phrase` (부분 문자열 일치)
    kind: str
    occurrences: int

    @property
    def score(self) -> float:
        """식별자 일치를 문구 일치보다 강한 근거로 본다. 등장 횟수는 약한 보정만 한다."""
        base = 1.0 if self.kind == "identifier" else 0.6
        return base + min(self.occurrences - 1, 4) * 0.02


class ExactRetriever(Generic[T]):
    """검색어 목록으로 정확 일치 항목을 찾는다. 인덱스를 만들지 않는다 (선형 스캔).

    BM25 인덱스 구축 비용이 없어 후보 집합이 수천 건이어도 즉시 동작하고, 무엇보다
    **왜 이 항목이 걸렸는지**(`term`, `kind`, `occurrences`)를 그대로 근거로 남길 수 있다.
    """

    def __init__(self, items: Sequence[T], text_getter: Callable[[T], str]) -> None:
        self.items = list(items)
        self.texts = [text_getter(item) or "" for item in self.items]

    def search_terms(self, terms: Sequence[str], limit: int = 0) -> list[ExactHit[T]]:
        """검색어 여러 개로 한 번에 찾는다. 같은 항목이 여러 검색어에 걸리면 강한 쪽을 남긴다."""
        best: dict[int, ExactHit[T]] = {}
        for raw_term in terms:
            term = (raw_term or "").strip()
            if len(term) < 2:
                continue
            identifier = is_identifier(term)
            pattern = _boundary_pattern(term) if identifier else None
            needle = term.casefold()
            for index, text in enumerate(self.texts):
                if not text:
                    continue
                occurrences = len(pattern.findall(text)) if pattern else text.casefold().count(needle)
                if not occurrences:
                    continue
                hit = ExactHit(item=self.items[index], term=term, kind="identifier" if identifier else "phrase", occurrences=occurrences)
                current = best.get(index)
                if current is None or hit.score > current.score:
                    best[index] = hit
        ranked = sorted(best.values(), key=lambda hit: hit.score, reverse=True)
        return ranked[:limit] if limit else ranked

    def search(self, query: str, top_k: int) -> list[tuple[T, float]]:
        """`Retriever` Protocol 호환. 공백으로 나눈 각 낱말을 검색어로 본다."""
        hits = self.search_terms(query.split(), limit=top_k)
        return [(hit.item, hit.score) for hit in hits]
