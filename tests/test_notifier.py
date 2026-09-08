"""운영 알림 테스트.

실제 SMTP 로 보내지 않는다 — 발송 함수를 가로채 무엇을 보내려 했는지만 확인한다.
가장 중요한 성질 세 가지:

1. **알림 실패가 원래 작업을 실패시키지 않는다.**
2. **같은 문제로 반복해서 보내지 않는다** (크레딧 소진은 실행마다 같은 오류가 난다).
3. **본문에 비밀정보·개인정보가 남지 않는다.**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import notifier
from app.core.notifier import (
    KIND_MODEL_UNAVAILABLE,
    KIND_QUOTA_EXHAUSTED,
    EmailSettings,
    classify_error,
    notify,
    notify_for_error,
)
from app.core.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(db_path=tmp_path / "notify.db")


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> list:
    """SMTP 설정을 갖춘 상태로 만들고 발송을 가로챈다."""
    sent: list = []
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com", port=587, use_tls=True, sender="bot@example.com",
            recipients=("qa@example.com",), username="bot@example.com", password="app-password",
            cooldown_minutes=180, enabled=True,
        ),
    )
    monkeypatch.setattr(notifier, "_send", lambda config, message: sent.append(message))
    return sent


# --- 오류 분류 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.",
        "429 Quota exceeded for quota metric",
        "Please go to AI Studio to manage your project and billing",
        "rate limit exceeded",
    ],
)
def test_quota_errors_are_detected(message: str) -> None:
    assert classify_error(message) == KIND_QUOTA_EXHAUSTED


def test_model_unavailable_is_detected() -> None:
    message = "404 NOT_FOUND. This model models/gemini-2.5-pro is no longer available to new users."
    assert classify_error(message) == KIND_MODEL_UNAVAILABLE


@pytest.mark.parametrize(
    "message",
    [
        "Gemini 응답이 완전한 JSON이 아닙니다",
        "GEMINI_API_KEY가 설정되지 않았습니다.",
        "",
    ],
)
def test_unrelated_errors_are_not_notified(message: str) -> None:
    """앱이 알려줄 필요가 없거나 사람이 조치할 대상이 아닌 오류는 메일을 보내지 않는다."""
    assert classify_error(message) is None


def test_notify_for_error_does_nothing_for_unrelated_errors(configured, storage) -> None:
    assert notify_for_error("파싱 실패", storage=storage) is None
    assert configured == []


# --- 발송 ---------------------------------------------------------------------


def test_quota_error_sends_one_mail(configured, storage) -> None:
    result = notify_for_error("429 RESOURCE_EXHAUSTED. credits are depleted", storage=storage)

    assert result is not None and result["status"] == "sent"
    assert len(configured) == 1
    assert "할당량" in configured[0]["Subject"]


def test_mail_explains_what_to_do(configured, storage) -> None:
    notify(KIND_QUOTA_EXHAUSTED, "429 RESOURCE_EXHAUSTED", storage=storage)
    body = configured[0].get_content()
    assert "ai.studio" in body
    assert "daily_token_limit" in body


def test_mail_includes_context(configured, storage) -> None:
    notify(KIND_QUOTA_EXHAUSTED, "429", context={"모델": "gemini-2.5-flash"}, storage=storage)
    assert "gemini-2.5-flash" in configured[0].get_content()


def test_error_detail_is_masked_in_the_body(configured, storage) -> None:
    """오류 메시지에 경로·계정이 섞여 나오는 경우가 있다."""
    notify(KIND_QUOTA_EXHAUSTED, r"429 at C:\Users\2024980\Documents\secret\key.txt for user@example.com", storage=storage)
    body = configured[0].get_content()
    assert "[LOCAL_PATH]" in body and "[EMAIL]" in body
    assert "2024980" not in body


# --- 쿨다운 -------------------------------------------------------------------


def test_second_identical_error_is_suppressed(configured, storage) -> None:
    """크레딧이 소진되면 실행마다 같은 오류가 난다 — 쿨다운이 없으면 메일이 폭주한다."""
    first = notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)
    second = notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)

    assert first["status"] == "sent"
    assert second["status"] == "skipped_cooldown"
    assert len(configured) == 1


def test_different_kinds_have_separate_cooldowns(configured, storage) -> None:
    notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)
    result = notify(KIND_MODEL_UNAVAILABLE, "404 no longer available", storage=storage)

    assert result["status"] == "sent"
    assert len(configured) == 2


def test_cooldown_expires(configured, storage, monkeypatch: pytest.MonkeyPatch) -> None:
    notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com", port=587, use_tls=True, sender="bot@example.com",
            recipients=("qa@example.com",), username="", password="",
            cooldown_minutes=0, enabled=True,
        ),
    )
    assert notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)["status"] == "sent"
    assert len(configured) == 2


def test_send_failure_still_starts_the_cooldown(monkeypatch: pytest.MonkeyPatch, storage) -> None:
    """메일 서버가 죽어 있을 때 매 실행마다 SMTP 접속을 시도하지 않게 한다."""
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com", port=587, use_tls=True, sender="bot@example.com",
            recipients=("qa@example.com",), username="", password="",
            cooldown_minutes=180, enabled=True,
        ),
    )

    def boom(config, message):
        raise OSError("SMTP 접속 실패")

    monkeypatch.setattr(notifier, "_send", boom)

    first = notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)
    second = notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)

    assert first["status"] == "failed"
    assert second["status"] == "skipped_cooldown"


# --- 설정이 없을 때 -----------------------------------------------------------


def test_without_recipients_nothing_is_sent(monkeypatch: pytest.MonkeyPatch, storage) -> None:
    """개발 환경에서 메일이 나가지 않게 한다."""
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com", port=587, use_tls=True, sender="bot@example.com",
            recipients=(), username="", password="", cooldown_minutes=180, enabled=True,
        ),
    )
    assert notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)["status"] == "not_configured"


def test_disabled_by_config(monkeypatch: pytest.MonkeyPatch, storage) -> None:
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.example.com", port=587, use_tls=True, sender="bot@example.com",
            recipients=("qa@example.com",), username="", password="", cooldown_minutes=180, enabled=False,
        ),
    )
    assert notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)["status"] == "not_configured"


def test_status_does_not_expose_values() -> None:
    """수신자 주소와 비밀번호가 노출되면 안 된다."""
    payload = notifier.status()
    assert set(payload) == {
        "enabled", "configured", "host_set", "sender_set", "recipient_count",
        "credentials_set", "cooldown_minutes", "warnings",
    }
    assert "@example" not in str(payload)
    assert "gmail.com" not in str(payload.get("recipient_count", ""))


def test_status_warns_when_credentials_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """발송이 실패할 때까지 기다리게 하지 않는다 — 빠진 것을 미리 알린다."""
    monkeypatch.setattr(
        notifier,
        "load_email_settings",
        lambda: EmailSettings(
            host="smtp.gmail.com", port=587, use_tls=True, sender="qa@example.com",
            recipients=("qa@example.com",), username="", password="", cooldown_minutes=180, enabled=True,
        ),
    )
    payload = notifier.status()
    assert payload["configured"] is True
    assert payload["credentials_set"] is False
    assert any("SMTP_USER" in warning for warning in payload["warnings"])


def test_status_has_no_warnings_when_fully_configured(configured) -> None:
    assert notifier.status()["warnings"] == []


# --- Gemini 호출 실패과의 연결 -------------------------------------------------


def test_gemini_failure_triggers_notification(configured, storage, monkeypatch: pytest.MonkeyPatch) -> None:
    """할당량 오류가 실제 호출 경로에서 알림으로 이어지는지."""
    from pydantic import BaseModel

    from app.core.gemini_client import GeminiClient

    class Response(BaseModel):
        ok: bool = True

    def boom(prompt: str) -> dict:
        raise RuntimeError("429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted.")

    client = GeminiClient(storage=storage, responder=boom)
    with pytest.raises(RuntimeError):
        client.generate_structured("{}", prompt_name="qa_agent_issue_impact", response_schema=Response)

    assert len(configured) == 1
    assert "할당량" in configured[0]["Subject"]


def test_notification_log_records_what_was_sent(configured, storage) -> None:
    notify(KIND_QUOTA_EXHAUSTED, "429", storage=storage)
    rows = storage.notification_log()
    assert [row["kind"] for row in rows] == [KIND_QUOTA_EXHAUSTED]
    assert rows[0]["sent_count"] == 1
    assert rows[0]["last_sent_at"]
