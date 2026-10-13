"""모델 라우팅과 비용 추정 테스트."""

from __future__ import annotations

from app.core.model_router import (
    DEFAULT_MODELS,
    DEFAULT_PRICING,
    TIER_COMPLEX,
    TIER_LIGHT,
    TIER_STANDARD,
    estimate_cost_usd,
    estimate_from_usage,
    model_for,
    pricing_for,
)

# --- 모델·가격 설정 ----------------------------------------------------------


def test_model_ids_come_from_settings(monkeypatch) -> None:
    """모델 ID 는 config.yaml 이 원천이다. 특정 ID 를 강제하지 않는다 — 모델은 폐기된다.

    AI 제공자마다 읽는 곳이 다르다 (REQ-AICALL-005·007): Claude CLI 는 `ai.claude.models.*`, Gemini 는 `models.*`.
    """
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setitem(settings.raw, "ai", {**(settings.raw.get("ai") or {}), "provider": "claude_cli"})
    for tier in (TIER_LIGHT, TIER_STANDARD, TIER_COMPLEX):
        configured = str(settings.get(f"ai.claude.models.{tier}", "") or "")
        assert model_for(tier) == configured, f"claude {tier}: 설정값과 다르다"
        assert configured, f"claude {tier}: 모델이 설정되지 않았다"
    monkeypatch.setitem(settings.raw, "ai", {**(settings.raw.get("ai") or {}), "provider": "gemini"})
    for tier in (TIER_LIGHT, TIER_STANDARD, TIER_COMPLEX):
        configured = str(settings.get(f"models.{tier}", "") or "")
        assert model_for(tier) == configured, f"gemini {tier}: 설정값과 다르다"
        assert configured, f"gemini {tier}: 모델이 설정되지 않았다"


def test_pro_class_models_are_not_configured() -> None:
    """Pro 계열은 thinking 을 끌 수 없어 이 파이프라인에 맞지 않는다 (실측 400).

    설정에 들어가면 매 호출이 thinking 폴백을 타면서 토큰이 늘어난다.
    """
    for tier in (TIER_LIGHT, TIER_STANDARD, TIER_COMPLEX):
        assert "pro" not in model_for(tier), f"{tier} 에 Pro 계열이 설정됐다: {model_for(tier)}"


def test_unknown_tier_falls_back_to_standard(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.core.model_router import DEFAULT_CLAUDE_MODELS

    settings = get_settings()
    monkeypatch.setitem(settings.raw, "ai", {"provider": "claude_cli"})
    assert model_for("nonexistent") == DEFAULT_CLAUDE_MODELS[TIER_STANDARD]
    monkeypatch.setitem(settings.raw, "ai", {"provider": "gemini"})
    assert model_for("nonexistent") == DEFAULT_MODELS[TIER_STANDARD]


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
