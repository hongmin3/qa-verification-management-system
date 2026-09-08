"""모델 라우팅과 비용 추정 테스트."""

from __future__ import annotations

import pytest

from app.core.model_router import (
    DEFAULT_PRICING,
    TIER_COMPLEX,
    TIER_LIGHT,
    TIER_STANDARD,
    estimate_cost_usd,
    estimate_from_usage,
    looks_like_integration,
    model_for,
    pricing_for,
    route_qa_analysis,
)


def _route(**overrides):
    defaults = {
        "linked_srs_count": 1,
        "exact_evidence_count": 2,
        "evidence_document_count": 1,
        "candidate_tc_count": 20,
        "root_cause_known": True,
        "has_integration_terms": False,
    }
    defaults.update(overrides)
    return route_qa_analysis(**defaults)


# --- 라우팅 ------------------------------------------------------------------


def test_ordinary_analysis_uses_the_standard_model() -> None:
    decision = _route()
    assert decision.tier == TIER_STANDARD
    assert decision.model == "gemini-2.5-flash"
    assert decision.reasons


def test_unknown_root_cause_escalates() -> None:
    decision = _route(root_cause_known=False)
    assert decision.tier == TIER_COMPLEX
    assert any("Root Cause" in reason for reason in decision.reasons)


def test_evidence_spread_across_documents_escalates() -> None:
    """근거가 여러 문서에 흩어져 있으면 사양 충돌 가능성이 있다."""
    decision = _route(evidence_document_count=3)
    assert decision.tier == TIER_COMPLEX


def test_integration_terms_escalate() -> None:
    decision = _route(has_integration_terms=True)
    assert decision.tier == TIER_COMPLEX
    assert any("API" in reason for reason in decision.reasons)


def test_many_linked_srs_without_exact_evidence_escalates() -> None:
    decision = _route(linked_srs_count=3, exact_evidence_count=0)
    assert decision.tier == TIER_COMPLEX


def test_many_linked_srs_with_exact_evidence_stays_standard() -> None:
    """근거를 실제로 찾았으면 올릴 이유가 없다."""
    assert _route(linked_srs_count=5, exact_evidence_count=4).tier == TIER_STANDARD


def test_forced_tier_wins() -> None:
    decision = route_qa_analysis(
        linked_srs_count=1,
        exact_evidence_count=0,
        evidence_document_count=9,
        candidate_tc_count=1,
        root_cause_known=False,
        has_integration_terms=True,
        force_tier=TIER_LIGHT,
    )
    assert decision.tier == TIER_LIGHT
    assert decision.model == "gemini-2.5-flash-lite"


def test_unknown_forced_tier_is_ignored() -> None:
    assert _route(force_tier="turbo").tier == TIER_STANDARD


def test_decision_serialises_with_reasons() -> None:
    payload = _route(root_cause_known=False).as_dict()
    assert set(payload) == {"tier", "model", "tier_label", "reasons"}
    assert payload["reasons"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Command 30402 응답 확인", True),
        ("DICOM Tag 확인", True),
        ("WebSocket 연결 해제", True),
        ("Generator Dose 값 잔존", True),
        ("환자 목록 정렬 순서", False),
    ],
)
def test_integration_term_detection(text: str, expected: bool) -> None:
    assert looks_like_integration(text) is expected


def test_integration_detection_is_safe_on_empty_text() -> None:
    assert looks_like_integration("") is False


# --- 모델·가격 설정 ----------------------------------------------------------


def test_model_ids_come_from_settings() -> None:
    """모델 ID 는 config.yaml 이 원천이다. 상위 등급은 계정에서 막힐 수 있어 값이 바뀔 수 있다
    (`gemini-2.5-pro` 는 실제로 "no longer available to new users" 를 돌려준다)."""
    assert model_for(TIER_STANDARD) == "gemini-2.5-flash"
    assert model_for(TIER_LIGHT) == "gemini-2.5-flash-lite"
    assert model_for(TIER_COMPLEX)  # 설정된 값이 있으면 된다 — 특정 ID 를 강제하지 않는다


def test_unknown_tier_falls_back_to_standard() -> None:
    assert model_for("nonexistent") == "gemini-2.5-flash"


def test_pricing_is_read_despite_dots_in_model_name() -> None:
    """모델명에 `.` 이 있어 점 표기 설정 조회로는 키가 갈라진다 — 표 전체를 인덱싱해야 한다."""
    price = pricing_for("gemini-2.5-flash")
    assert price == DEFAULT_PRICING["gemini-2.5-flash"]


def test_unknown_model_falls_back_to_flash_pricing() -> None:
    assert pricing_for("gemini-9.9-unknown") == DEFAULT_PRICING["gemini-2.5-flash"]


# --- 비용 추정 ----------------------------------------------------------------


def test_cost_estimate_matches_the_published_rate() -> None:
    # Flash: input $0.30 / 1M, output $2.50 / 1M
    assert estimate_cost_usd("gemini-2.5-flash", 1_000_000, 0) == 0.30
    assert estimate_cost_usd("gemini-2.5-flash", 0, 1_000_000) == 2.50


def test_pro_is_more_expensive_than_flash_for_the_same_tokens() -> None:
    flash = estimate_cost_usd("gemini-2.5-flash", 12_000, 3_000)
    pro = estimate_cost_usd("gemini-2.5-pro", 12_000, 3_000)
    assert pro > flash


def test_zero_usage_costs_nothing() -> None:
    assert estimate_cost_usd("gemini-2.5-flash", 0, 0) == 0.0


def test_estimate_from_usage_reads_the_client_token_shape() -> None:
    """GeminiClient 가 쌓는 키 이름(`prompt_tokens`/`candidate_tokens`)을 그대로 받는다."""
    estimate = estimate_from_usage("gemini-2.5-flash", {"prompt_tokens": 12_000, "candidate_tokens": 3_000, "total_tokens": 15_000})
    assert estimate["prompt_tokens"] == 12_000
    assert estimate["output_tokens"] == 3_000
    assert estimate["usd"] > 0
    assert estimate["pricing_per_1m"]["input"] == 0.30


def test_estimate_handles_missing_usage() -> None:
    assert estimate_from_usage("gemini-2.5-flash", {})["usd"] == 0.0


def test_monthly_scale_stays_small_for_flash() -> None:
    """참고 규모: 5명 × 하루 10건 × 20일 = 1,000건. 실측 입력 12.1K 토큰 기준."""
    per_request = estimate_cost_usd("gemini-2.5-flash", 12_100, 3_000)
    assert per_request * 1_000 < 12.0  # 월 12달러 미만
