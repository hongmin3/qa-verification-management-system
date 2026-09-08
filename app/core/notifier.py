"""운영 중 사람이 개입해야 하는 상황을 메일로 알린다.

**무엇을 알리는가.** 앱이 스스로 복구할 수 없고 사람이 조치해야 끝나는 것만 보낸다.
지금은 Gemini API 할당량·결제 문제가 그렇다 — 크레딧이 소진되면 모든 분석이 실패하는데,
사용자가 화면을 보고 있지 않으면 알 방법이 없다.

**설계 원칙**

1. **알림 실패가 분석을 실패시키지 않는다.** 메일이 안 나가도 원래 오류가 그대로 보고된다.
2. **같은 문제로 반복해서 보내지 않는다.** 크레딧이 소진되면 실행마다 같은 오류가 나므로
   쿨다운 안에서는 한 번만 보낸다 (`notifications` 테이블에 마지막 발송 시각 기록).
3. **비밀정보와 개인정보를 본문에 담지 않는다.** API Key·payload 원문·환자 정보는 넣지
   않고, 오류 메시지는 마스킹 계층을 한 번 더 통과시킨다.
4. **설정이 없으면 조용히 끈다.** SMTP 설정이나 수신자가 없으면 알림 없이 동작한다 —
   개발 환경에서 메일이 나가지 않게 하기 위함이다.

**수신자와 SMTP 자격증명은 `secrets.txt` 에 둔다.** 이 저장소는 공개되므로 개인 메일 주소를
커밋하지 않는다 (`secrets.example.txt` 에 형식만 있다).
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate

from app.core.config import get_settings
from app.core.security_filter import mask_text
from app.core.storage import Storage

logger = logging.getLogger("regression_analyzer")

# 알림 종류. 쿨다운은 종류별로 따로 센다.
KIND_QUOTA_EXHAUSTED = "gemini_quota_exhausted"
KIND_MODEL_UNAVAILABLE = "gemini_model_unavailable"

KIND_LABELS = {
    KIND_QUOTA_EXHAUSTED: "Gemini API 할당량·크레딧 소진",
    KIND_MODEL_UNAVAILABLE: "Gemini 모델 사용 불가",
}

# 오류 메시지에서 이 알림 종류를 판별하는 표현. API 문구가 바뀔 수 있어 상태 코드와 함께 본다.
_QUOTA_HINTS = (
    "429",
    "resource_exhausted",
    "prepayment credits are depleted",
    "quota",
    "billing",
    "rate limit",
)


def classify_error(message: str) -> str | None:
    """오류 메시지 → 알림 종류. 알림 대상이 아니면 None."""
    lowered = (message or "").casefold()
    if any(hint in lowered for hint in _QUOTA_HINTS):
        return KIND_QUOTA_EXHAUSTED
    if "no longer available" in lowered or ("404" in lowered and "model" in lowered):
        return KIND_MODEL_UNAVAILABLE
    return None


@dataclass
class EmailSettings:
    host: str
    port: int
    use_tls: bool
    sender: str
    recipients: tuple[str, ...]
    username: str
    password: str
    cooldown_minutes: int
    enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.host and self.sender and self.recipients)


def load_email_settings() -> EmailSettings:
    settings = get_settings()
    secrets = settings.secrets
    raw_recipients = str(getattr(secrets, "notify_email_to", "") or "")
    recipients = tuple(part.strip() for part in raw_recipients.replace(";", ",").split(",") if part.strip())
    sender = str(getattr(secrets, "smtp_user", "") or "") or (recipients[0] if recipients else "")
    return EmailSettings(
        host=str(settings.get("notifications.email.host", "") or ""),
        port=int(settings.get("notifications.email.port", 587) or 587),
        use_tls=bool(settings.get("notifications.email.use_tls", True)),
        sender=str(settings.get("notifications.email.sender", "") or sender),
        recipients=recipients,
        username=str(getattr(secrets, "smtp_user", "") or ""),
        password=str(getattr(secrets, "smtp_password", "") or ""),
        cooldown_minutes=int(settings.get("notifications.email.cooldown_minutes", 180) or 180),
        enabled=bool(settings.get("notifications.email.enabled", True)),
    )


def status() -> dict:
    """설정 상태. **값 자체는 노출하지 않는다** — 수신자 수와 설정 여부만.

    `configured` 는 "보내기를 시도할 조건이 되었는가"이고 "성공한다"는 뜻이 아니다. 인증이
    필요 없는 사내 SMTP 릴레이도 있으므로 자격증명을 필수로 두지 않고, 대신 빠진 것이
    있으면 `warnings` 로 미리 알린다 — 발송이 실패할 때까지 기다리게 하지 않기 위함이다.
    """
    config = load_email_settings()
    credentials_set = bool(config.username and config.password)
    warnings: list[str] = []
    if config.configured and not credentials_set:
        warnings.append(
            "SMTP 자격증명(SMTP_USER / SMTP_PASSWORD)이 없습니다. 인증이 필요한 서버"
            "(Gmail 등)에서는 발송이 실패합니다. Gmail 은 2단계 인증 후 앱 비밀번호를 발급해 넣습니다."
        )
    if config.configured and not config.username and config.host.endswith("gmail.com"):
        warnings.append("발신자가 SMTP_USER 대신 수신자 주소로 설정됐습니다. Gmail 은 인증한 계정만 발신할 수 있습니다.")
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "host_set": bool(config.host),
        "sender_set": bool(config.sender),
        "recipient_count": len(config.recipients),
        "credentials_set": credentials_set,
        "cooldown_minutes": config.cooldown_minutes,
        "warnings": warnings,
    }


def _build_message(config: EmailSettings, kind: str, detail: str, context: dict | None) -> EmailMessage:
    label = KIND_LABELS.get(kind, kind)
    # 오류 메시지에 경로·계정이 섞여 나오는 경우가 있어 마스킹을 한 번 더 통과시킨다.
    safe_detail, _ = mask_text(detail or "")
    lines = [
        f"[QA 검증 관리 시스템] {label}",
        "",
        f"발생 시각: {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
    ]
    for key, value in (context or {}).items():
        lines.append(f"{key}: {value}")
    lines += [
        "",
        "오류 내용:",
        safe_detail[:1500],
        "",
        "조치 방법:",
    ]
    if kind == KIND_QUOTA_EXHAUSTED:
        lines += [
            "  1. https://ai.studio/projects 에서 프로젝트의 크레딧·결제 상태를 확인합니다.",
            "  2. 결제가 정상이면 일일 할당량 초과일 수 있습니다. 잠시 후 재시도하거나",
            "     config.yaml 의 analysis.daily_token_limit 을 확인합니다.",
            "  3. 해결 전까지 새 분석은 계속 실패합니다. Gate 에서 막힌 분석은 영향이 없습니다.",
        ]
    elif kind == KIND_MODEL_UNAVAILABLE:
        lines += [
            "  1. config.yaml 의 models.* 값이 이 계정에서 쓸 수 있는 모델인지 확인합니다.",
            "  2. 상위 등급 모델이 막힌 경우에는 기본 모델로 자동 폴백되며 분석은 계속됩니다.",
        ]
    lines += [
        "",
        f"쿨다운: 같은 문제로 {config.cooldown_minutes}분 안에는 다시 보내지 않습니다.",
        "이 메일은 자동 발송되었습니다.",
    ]

    message = EmailMessage()
    message["Subject"] = f"[QA 시스템] {label}"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content("\n".join(lines))
    return message


def _send(config: EmailSettings, message: EmailMessage) -> None:
    if config.use_tls:
        with smtplib.SMTP(config.host, config.port, timeout=20) as server:
            server.starttls()
            if config.username and config.password:
                server.login(config.username, config.password)
            server.send_message(message)
        return
    with smtplib.SMTP_SSL(config.host, config.port, timeout=20) as server:
        if config.username and config.password:
            server.login(config.username, config.password)
        server.send_message(message)


def notify(kind: str, detail: str, context: dict | None = None, storage: Storage | None = None) -> dict:
    """알림을 보낸다. **어떤 경우에도 예외를 올리지 않는다.**

    반환값은 무엇을 했는지 기록용이다: `sent` / `skipped_cooldown` / `not_configured` / `failed`.
    """
    result = {"kind": kind, "status": "not_configured"}
    try:
        config = load_email_settings()
        if not config.configured:
            return result
        storage = storage or Storage()
        cooldown_until = (datetime.now(timezone.utc) - timedelta(minutes=config.cooldown_minutes)).isoformat()
        if not storage.claim_notification(kind, cooldown_until):
            result["status"] = "skipped_cooldown"
            return result
        _send(config, _build_message(config, kind, detail, context))
        storage.mark_notification_sent(kind)
        result["status"] = "sent"
        result["recipients"] = len(config.recipients)
        logger.info("notification_sent kind=%s recipients=%d", kind, len(config.recipients))
        return result
    except Exception as exc:
        # 알림 실패가 원래 작업을 실패시키지 않는다.
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("notification_failed kind=%s error_type=%s", kind, type(exc).__name__)
        return result


def notify_for_error(message: str, context: dict | None = None, storage: Storage | None = None) -> dict | None:
    """오류 메시지가 알림 대상이면 보낸다. 대상이 아니면 아무것도 하지 않는다."""
    kind = classify_error(message)
    if kind is None:
        return None
    return notify(kind, message, context=context, storage=storage)
