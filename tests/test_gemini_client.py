"""공용 Gemini 클라이언트의 응답 캐시와 토큰 누적 (NFR-IMPACT-001).

Regression 영향 분석을 없애면서(2026-10-01) 그 기능의 테스트 파일에 있던 공용 클라이언트 시험만 옮겼다.
"""

from app.core.storage import Storage


def test_gemini_client_accumulates_token_usage_and_cache_hits_across_calls(tmp_path):
    """Validates: NFR-IMPACT-001 — 같은 입력은 캐시로 처리하고, 캐시분 토큰은 더하지 않는다."""
    from app.core.gemini_client import GeminiClient
    from app.modules.manual_review.schemas import QuickJudgmentResponse

    storage = Storage(tmp_path / "test.db")
    calls = {"n": 0}

    def responder(prompt: str) -> dict:
        calls["n"] += 1
        return {"decision": "PASS", "confidence": 0.9, "reason_codes": [], "requires_detail_generation": False, "token_usage": {"total_tokens": 10}}

    client = GeminiClient(storage=storage, responder=responder)
    client.generate_structured("prompt-a", prompt_name="manual_revision_quick", response_schema=QuickJudgmentResponse)
    client.generate_structured("prompt-b", prompt_name="manual_revision_quick", response_schema=QuickJudgmentResponse)
    client.generate_structured("prompt-a", prompt_name="manual_revision_quick", response_schema=QuickJudgmentResponse)

    assert calls["n"] == 2  # 세 번째 호출은 동일 prompt라 캐시로 처리되어 responder가 다시 불리지 않음
    assert client.request_count == 2
    assert client.cache_hit_count == 1
    assert client.token_usage == {"total_tokens": 20}  # 실제 호출 2회분만 누적, 캐시 재사용분은 중복 합산하지 않음
