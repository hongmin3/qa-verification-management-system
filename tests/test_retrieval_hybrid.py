"""Exact 검색과 Exact→BM25 단계 검색 테스트."""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.exact_retriever import ExactRetriever, is_identifier
from app.retrieval.hybrid import SOURCE_BM25, SOURCE_EXACT, HybridRetriever


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str


def _chunks() -> list[Chunk]:
    return [
        Chunk("c1", "SRS-1200 로그인 화면에서 계정을 추가한다. AP-5500 참조."),
        Chunk("c2", "SRS-1201 암호 변경 정책. 첫 로그인 시 변경을 강제한다."),
        Chunk("c3", "DICOM Attribute (0008,0018) SOP Instance UID 는 필수다."),
        Chunk("c4", "Command 0x1234 는 촬영 시작을 요청한다."),
        Chunk("c5", "AP-55 는 이전 릴리스에서 종료된 이슈다."),
        Chunk("c6", "환자 목록 정렬 규칙과 표시 항목."),
        Chunk("c7", "Save Dose 를 켜면 촬영 후 선량이 저장된다."),
        Chunk("c8", "계정 권한별 메뉴 노출 규칙."),
        Chunk("c9", "로그 내보내기 경로 설정."),
        Chunk("c10", "네트워크 재연결 시 상태 복원."),
    ]


def _hybrid() -> HybridRetriever[Chunk]:
    return HybridRetriever(_chunks(), text_getter=lambda chunk: chunk.text, key_getter=lambda chunk: chunk.chunk_id)


def _exact() -> ExactRetriever[Chunk]:
    return ExactRetriever(_chunks(), text_getter=lambda chunk: chunk.text)


# --- 식별자 판정 --------------------------------------------------------------


def test_identifier_patterns_are_recognised() -> None:
    for term in ("AP-5500", "VP-1234", "SRS-1200", "0x1234", "(0008,0018)"):
        assert is_identifier(term), term


def test_plain_words_are_not_identifiers() -> None:
    for term in ("계정 추가", "Save Dose", "로그인"):
        assert not is_identifier(term), term


# --- Exact 검색 ---------------------------------------------------------------


def test_identifier_match_respects_boundaries() -> None:
    """`AP-55` 가 `AP-5500` 에 걸리면 안 된다 — BM25 로는 막을 수 없는 오탐이다."""
    hits = _exact().search_terms(["AP-55"])
    assert [hit.item.chunk_id for hit in hits] == ["c5"]


def test_full_identifier_is_found() -> None:
    hits = _exact().search_terms(["AP-5500"])
    assert [hit.item.chunk_id for hit in hits] == ["c1"]


def test_dicom_tag_with_punctuation_is_found() -> None:
    """구두점이 있는 Tag 는 BM25 토크나이저에서 사라진다."""
    hits = _exact().search_terms(["(0008,0018)"])
    assert [hit.item.chunk_id for hit in hits] == ["c3"]


def test_phrase_match_is_case_insensitive() -> None:
    hits = _exact().search_terms(["save dose"])
    assert [hit.item.chunk_id for hit in hits] == ["c7"]


def test_identifier_hit_outranks_phrase_hit() -> None:
    hits = _exact().search_terms(["AP-5500", "계정"])
    assert hits[0].kind == "identifier"


def test_hit_records_why_it_matched() -> None:
    hit = _exact().search_terms(["AP-5500"])[0]
    assert (hit.term, hit.kind, hit.occurrences) == ("AP-5500", "identifier", 1)


def test_short_and_empty_terms_are_ignored() -> None:
    assert _exact().search_terms(["", " ", "a"]) == []


def test_same_item_matched_twice_keeps_the_stronger_reason() -> None:
    hits = _exact().search_terms(["로그인", "SRS-1200"])
    c1 = next(hit for hit in hits if hit.item.chunk_id == "c1")
    assert c1.kind == "identifier"


def test_exact_retriever_satisfies_retriever_protocol() -> None:
    results = _exact().search("AP-5500", top_k=3)
    assert results and results[0][0].chunk_id == "c1"


# --- 단계 검색 ----------------------------------------------------------------


def test_exact_hits_take_the_front_positions() -> None:
    result = _hybrid().search(terms=["SRS-1200", "SRS-1201"], query="계정 로그인", top_k=6)
    assert [entry.source for entry in result.items[:2]] == [SOURCE_EXACT, SOURCE_EXACT]
    assert {entry.item.chunk_id for entry in result.items[:2]} == {"c1", "c2"}


def test_bm25_fills_the_remaining_slots() -> None:
    result = _hybrid().search(terms=["SRS-1200"], query="계정 권한 메뉴", top_k=5, adaptive=False)
    assert len(result.items) == 5
    assert result.exact_count == 1
    assert result.bm25_count == 4


def test_no_duplicates_between_exact_and_bm25() -> None:
    result = _hybrid().search(terms=["SRS-1200"], query="SRS-1200 계정 추가", top_k=6, adaptive=False)
    ids = [entry.item.chunk_id for entry in result.items]
    assert len(ids) == len(set(ids))


def test_adaptive_shrinks_total_when_exact_evidence_exists() -> None:
    """확실한 근거가 있으면 LLM 에 보낼 후보 자체를 줄인다 (토큰 절약)."""
    wide = _hybrid().search(terms=["SRS-1200", "SRS-1201"], query="계정", top_k=8, adaptive=False)
    narrow = _hybrid().search(terms=["SRS-1200", "SRS-1201"], query="계정", top_k=8, adaptive=True, min_bm25=2)
    assert len(narrow.items) < len(wide.items)
    assert len(narrow.items) == 4  # exact 2 + min_bm25 2


def test_adaptive_does_not_shrink_when_no_exact_hit() -> None:
    """근거가 없을 때 후보를 줄이면 Recall 이 떨어진다."""
    result = _hybrid().search(terms=["없는식별자-9999"], query="계정 권한 메뉴 로그", top_k=6, adaptive=True)
    assert result.exact_count == 0
    assert len(result.items) == 6


def test_bm25_slots_are_reserved_even_with_many_exact_hits() -> None:
    """exact 히트가 한쪽에 몰려 있어도 다른 근거를 볼 여지를 남긴다."""
    result = _hybrid().search(terms=["SRS", "로그", "계정", "촬영", "상태"], query="계정", top_k=4, min_bm25=2, adaptive=False)
    assert result.exact_count <= 2
    assert result.bm25_count >= 1


def test_result_reports_why_each_item_was_selected() -> None:
    result = _hybrid().search(terms=["SRS-1200"], query="계정", top_k=3, adaptive=False)
    reasons = [entry.reason for entry in result.items]
    assert "'SRS-1200' 정확 일치" in reasons
    assert any("용어 유사도" in reason for reason in reasons)


def test_summary_records_the_search_budget_for_audit() -> None:
    result = _hybrid().search(terms=["SRS-1200"], query="계정", top_k=5, adaptive=True, min_bm25=2)
    summary = result.summary()
    assert summary["exact"] == 1
    assert summary["bm25_budget"] == 2
    assert summary["terms"] == ["SRS-1200"]
    assert summary["total"] == len(result.items)


def test_empty_terms_and_query_return_nothing() -> None:
    result = _hybrid().search(terms=[], query="", top_k=5)
    assert result.items == []


def test_empty_corpus_is_safe() -> None:
    retriever = HybridRetriever([], text_getter=lambda chunk: "", key_getter=lambda chunk: "")
    assert retriever.search(terms=["AP-1"], query="x", top_k=3).items == []


def test_values_exposes_plain_items_for_prompt_building() -> None:
    result = _hybrid().search(terms=["SRS-1200"], query="계정", top_k=3, adaptive=False)
    assert all(isinstance(value, Chunk) for value in result.values)
