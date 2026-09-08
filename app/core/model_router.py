"""요청 난이도에 따라 Gemini 모델을 고른다 (Rule-based).

전부 Pro 로 보내면 비싸고, 전부 Flash-Lite 로 보내면 어려운 판단이 부정확하다. 그런데
"어려운 요청인가"를 판단하려고 또 LLM 을 부르면 그것이 비용이다. 그래서 **입력에서 세어지는
신호로만** 고른다.

    Flash-Lite  요약·분류·정형 추출처럼 판단이 거의 없는 작업
    Flash       기본 QA 판단 (Issue 분석 / 사양 추적 / TC Coverage / 일반 Regression)
    Pro         근거가 여러 문서에 흩어져 있거나 Root Cause 가 불명확한 경우

가격은 `config.yaml` `models.pricing` 에 둔다 — 값이 바뀌면 코드를 고치지 않는다.
비용은 **추정치**다. 실제 청구는 Google 콘솔이 기준이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings

TIER_LIGHT = "light"
TIER_STANDARD = "standard"
TIER_COMPLEX = "complex"

# 이 저장소가 쓰는 모델. `config.yaml` `models.<tier>` 로 덮어쓸 수 있다.
DEFAULT_MODELS = {
    TIER_LIGHT: "gemini-2.5-flash-lite",
    TIER_STANDARD: "gemini-2.5-flash",
    TIER_COMPLEX: "gemini-2.5-pro",
}

# 1M 토큰당 USD. `config.yaml` `models.pricing.<model>` 로 덮어쓴다.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}

TIER_LABELS = {TIER_LIGHT: "경량 (요약·분류)", TIER_STANDARD: "기본 QA 판단", TIER_COMPLEX: "복잡한 교차검증"}


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    reasons: tuple[str, ...] = ()

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier)

    def as_dict(self) -> dict:
        return {"tier": self.tier, "model": self.model, "tier_label": self.tier_label, "reasons": list(self.reasons)}


def model_for(tier: str) -> str:
    configured = get_settings().get(f"models.{tier}", "")
    return str(configured or DEFAULT_MODELS.get(tier, DEFAULT_MODELS[TIER_STANDARD]))


def pricing_for(model: str) -> dict[str, float]:
    """모델별 1M 토큰당 단가. 설정에 없으면 코드 기본값을 쓴다.

    `settings.get("models.pricing.<model>")` 을 쓰지 않는다 — 점 표기 조회가 `.` 로 경로를
    쪼개는데 모델명 자체에 점이 있어(`gemini-2.5-flash`) 키가 갈라진다. 표 전체를 받아
    직접 인덱싱한다.
    """
    table = get_settings().get("models.pricing") or {}
    configured = table.get(model) or {} if isinstance(table, dict) else {}
    default = DEFAULT_PRICING.get(model, DEFAULT_PRICING["gemini-2.5-flash"])
    return {"input": float(configured.get("input", default["input"])), "output": float(configured.get("output", default["output"]))}


def estimate_cost_usd(model: str, prompt_tokens: int, output_tokens: int) -> float:
    """토큰 수 → USD 추정. 실제 청구는 Google 콘솔이 기준이다."""
    price = pricing_for(model)
    return round(prompt_tokens / 1_000_000 * price["input"] + output_tokens / 1_000_000 * price["output"], 6)


def estimate_from_usage(model: str, token_usage: dict) -> dict:
    prompt_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(token_usage.get("candidate_tokens", 0) or 0)
    return {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "usd": estimate_cost_usd(model, prompt_tokens, output_tokens),
        "pricing_per_1m": pricing_for(model),
    }


def route_qa_analysis(
    linked_srs_count: int,
    exact_evidence_count: int,
    evidence_document_count: int,
    candidate_tc_count: int,
    root_cause_known: bool,
    has_integration_terms: bool,
    force_tier: str = "",
) -> RoutingDecision:
    """QA Agent 분석 1건의 모델을 고른다. 전부 입력에서 세어지는 신호다.

    Pro 로 올리는 조건은 규칙이 "복잡하다"고 본 상황과 맞춘다 — 근거가 여러 문서에 흩어져
    있거나(사양 충돌 가능), Root Cause 가 불명확하거나, 연동(API/DICOM/WebSocket) 의미
    판단이 필요한 경우다.
    """
    if force_tier in DEFAULT_MODELS:
        return RoutingDecision(tier=force_tier, model=model_for(force_tier), reasons=("사용자가 모델 등급을 지정했습니다",))

    reasons: list[str] = []
    if not root_cause_known:
        reasons.append("Root Cause 가 문서에 명시돼 있지 않습니다")
    if evidence_document_count >= 3:
        reasons.append(f"근거가 {evidence_document_count}개 문서에 흩어져 있습니다")
    if has_integration_terms:
        reasons.append("API/DICOM/WebSocket 연동 의미 판단이 필요합니다")
    if linked_srs_count >= 3 and exact_evidence_count == 0:
        reasons.append("연결된 SRS 가 여러 건인데 사양 본문에서 정확 일치를 찾지 못했습니다")

    if reasons:
        return RoutingDecision(tier=TIER_COMPLEX, model=model_for(TIER_COMPLEX), reasons=tuple(reasons))
    return RoutingDecision(
        tier=TIER_STANDARD,
        model=model_for(TIER_STANDARD),
        reasons=(f"근거 문서 {evidence_document_count}개, TC 후보 {candidate_tc_count}건 — 기본 판단 범위",),
    )


INTEGRATION_TERMS = ("api", "websocket", "dicom", "command", "protocol", "sop", "generator")


def looks_like_integration(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term in lowered for term in INTEGRATION_TERMS)
