"""공용 AI 호출 클라이언트(app/core/gemini_client.py)의 재시도 설정과 대신 쓴 모델 기록."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.core import gemini_client as gemini_module
from app.core.gemini_client import GeminiClient
from app.core.storage import Storage


class _Answer(BaseModel):
    decision: str = "PASS"


PROMPT = "manual_revision_quick"


@pytest.fixture(autouse=True)
def _no_notifications(monkeypatch):
    """오류 알림 메일을 보내지 않는다."""
    monkeypatch.setattr(gemini_module, "notify_for_error", lambda *args, **kwargs: None)


def _use_gemini(client: GeminiClient, monkeypatch) -> None:
    """대신 쓸 모델·추론 켜기는 Gemini 에만 있는 동작이다 (REQ-AICALL-003)."""
    monkeypatch.setitem(client.settings.raw, "ai", {"provider": "gemini"})


def _analysis_settings(client: GeminiClient, monkeypatch, **values) -> None:
    analysis = dict(client.settings.raw.get("analysis") or {})
    analysis.update(values)
    monkeypatch.setitem(client.settings.raw, "analysis", analysis)


# Validates: REQ-AICALL-003, REQ-CONF-001
def test_retry_count_comes_from_config(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky(prompt: str) -> dict:
        calls["n"] += 1
        raise ConnectionError("네트워크 끊김")

    client = GeminiClient(storage=Storage(tmp_path / "t.db"), responder=flaky)
    _analysis_settings(client, monkeypatch, max_retries=2, retry_min_seconds=0, retry_max_seconds=0)
    with pytest.raises(ConnectionError):
        client.generate_structured("p", prompt_name=PROMPT, response_schema=_Answer)
    assert calls["n"] == 2

    calls["n"] = 0
    _analysis_settings(client, monkeypatch, max_retries=4, retry_min_seconds=0, retry_max_seconds=0)
    with pytest.raises(ConnectionError):
        client.generate_structured("q", prompt_name=PROMPT, response_schema=_Answer)
    assert calls["n"] == 4


# Validates: REQ-AICALL-003
def test_retry_recovers_after_transient_error(tmp_path, monkeypatch):
    calls = {"n": 0}

    def once_flaky(prompt: str) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("잠시 느림")
        return {"decision": "PASS", "token_usage": {"total_tokens": 5}}

    client = GeminiClient(storage=Storage(tmp_path / "t.db"), responder=once_flaky)
    _analysis_settings(client, monkeypatch, max_retries=3, retry_min_seconds=0, retry_max_seconds=0)
    assert client.generate_structured("p", prompt_name=PROMPT, response_schema=_Answer)["decision"] == "PASS"
    assert calls["n"] == 2


# Validates: REQ-AICALL-002, REQ-AICALL-003
def test_cached_answer_keeps_fallback_model_record(tmp_path, monkeypatch):
    """대신 쓴 모델로 받은 응답을 저장본에서 꺼낼 때도 대신 쓴 기록이 되살아난다 (MISMATCH 5-8)."""
    storage = Storage(tmp_path / "t.db")
    calls = {"n": 0}

    def unavailable_then_ok(prompt: str) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("404 NOT_FOUND: model is not found")
        return {"decision": "PASS", "token_usage": {"total_tokens": 7}}

    first = GeminiClient(storage=storage, responder=unavailable_then_ok)

    _use_gemini(first, monkeypatch)
    _analysis_settings(first, monkeypatch, retry_min_seconds=0, retry_max_seconds=0)
    requested = "gemini-requested-pro"
    fallback_model = str(first.settings.get("models.standard", "") or first.settings.secrets.gemini_model)
    assert fallback_model and fallback_model != requested
    first.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model=requested)
    assert first.model_fallback and first.model_fallback["used"] == fallback_model
    assert first.last_model == fallback_model

    second = GeminiClient(storage=storage, responder=lambda _: pytest.fail("저장본에서 꺼내야 한다"))

    _use_gemini(second, monkeypatch)
    answer = second.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model=requested)
    assert second.last_cache_hit
    assert second.last_model == fallback_model
    assert second.model_fallback == {"requested": requested, "used": fallback_model, "reason": first.model_fallback["reason"]}
    assert "_served" not in answer
    assert answer["decision"] == "PASS"


# Validates: REQ-AICALL-002
def test_cached_answer_keeps_thinking_override_record(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "t.db")
    calls = {"n": 0}

    def needs_thinking(prompt: str) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("400 INVALID_ARGUMENT: Budget 0 is invalid. This model only works in thinking mode")
        return {"decision": "PASS", "token_usage": {"total_tokens": 9}}

    first = GeminiClient(storage=storage, responder=needs_thinking)

    _use_gemini(first, monkeypatch)
    first.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model="gemini-pro-x")
    assert first.thinking_override

    second = GeminiClient(storage=storage, responder=lambda _: pytest.fail("저장본에서 꺼내야 한다"))

    _use_gemini(second, monkeypatch)
    second.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model="gemini-pro-x")
    assert second.last_cache_hit
    assert second.thinking_override == first.thinking_override
    assert second.last_model == "gemini-pro-x"


def test_plain_cached_answer_has_no_fallback_record(tmp_path):
    # Validates: REQ-AICALL-002
    storage = Storage(tmp_path / "t.db")
    first = GeminiClient(storage=storage, responder=lambda _: {"decision": "PASS", "token_usage": {"total_tokens": 1}})
    first.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model="gemini-a")
    second = GeminiClient(storage=storage, responder=lambda _: pytest.fail("저장본에서 꺼내야 한다"))
    answer = second.generate_structured("same", prompt_name=PROMPT, response_schema=_Answer, model="gemini-a")
    assert second.model_fallback is None and second.thinking_override is None
    assert second.last_model == "gemini-a"
    assert set(answer) == {"decision", "token_usage"}
