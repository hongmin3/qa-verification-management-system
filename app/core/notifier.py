"""운영 중 사람이 개입해야 하는 상황을 메일로 알린다.

**무엇을 알리는가.** 앱이 스스로 복구할 수 없고 사람이 조치해야 끝나는 것만 보낸다.
지금은 AI 사용량 한도·인증 문제가 그렇다 — Claude 사용량 한도에 닿거나 Gemini 크레딧이
소진되면 모든 분석이 실패하는데, 사용자가 화면을 보고 있지 않으면 알 방법이 없다.

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
import re
import smtplib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.core.config import get_settings
from app.core.security_filter import mask_text
from app.core.storage import Storage

logger = logging.getLogger("regression_analyzer")

# 알림 종류. 쿨다운은 종류별로 따로 센다.
KIND_QUOTA_EXHAUSTED = "gemini_quota_exhausted"
KIND_MODEL_UNAVAILABLE = "gemini_model_unavailable"
KIND_CLAUDE_USAGE_LIMIT = "claude_usage_limit"
KIND_CLAUDE_AUTH_FAILED = "claude_auth_failed"

KIND_LABELS = {
    KIND_QUOTA_EXHAUSTED: "Gemini API 할당량·크레딧 소진",
    KIND_MODEL_UNAVAILABLE: "Gemini 모델 사용 불가",
    KIND_CLAUDE_USAGE_LIMIT: "Claude 사용량 한도 도달",
    KIND_CLAUDE_AUTH_FAILED: "Claude CLI 인증 실패",
}

#: Claude CLI 오류는 `app/core/claude_cli.py` 가 이 머리말로 시작하게 만든다. 머리말이 있으면
#: Claude 종류만 본다 — "429" 같은 표현이 Gemini 할당량 알림으로 잘못 가지 않게 하기 위함이다.
CLAUDE_ERROR_MARKER = "claude cli"
_CLAUDE_LIMIT_HINTS = ("session limit", "usage limit", "rate limit", "limit reached", "429")
_CLAUDE_AUTH_HINTS = ("not logged in", "invalid api key", "authentication", "401", "oauth token")

# 오류 메시지에서 이 알림 종류를 판별하는 표현. API 문구가 바뀔 수 있어 상태 코드와 함께 본다.
_QUOTA_HINTS = (
    "429",
    "resource_exhausted",
    "prepayment credits are depleted",
    "quota",
    "billing",
    "rate limit",
)


#: 모든 메일 제목의 머리말. 메일 프로그램의 분류 규칙이 이 머리말과 그 뒤의 제품 이름으로 폴더를 나눈다 (REQ-MAIL-001).
SUBJECT_PREFIX = "[QA Agent]"


def mail_subject(product: str, title: str) -> str:
    """모든 메일 제목의 단일 생성 지점. `[QA Agent] <제품> <제목>`, 제품이 없으면 `[QA Agent] <제목>`."""
    return " ".join(part for part in (SUBJECT_PREFIX, (product or "").strip(), (title or "").strip()) if part)


def mask_address(address: str) -> str:
    """메일 주소를 앞 세 글자만 남기고 가린다(`hon***@vieworks.com`). 아이디가 세 글자 이하면 첫 글자만 남긴다."""
    local, at, domain = str(address or "").strip().rpartition("@")
    if not at or not local or not domain:
        return "***"
    return f"{local[:3] if len(local) > 3 else local[:1]}***@{domain}"


def _message_id(sender: str) -> str:
    """발신 주소의 도메인으로 만든 메일 고유 번호. 발신 도메인을 알 수 없으면 이 서버 이름을 쓴다."""
    domain = parseaddr(sender or "")[1].rpartition("@")[2]
    return make_msgid(domain=domain or None)


def classify_error(message: str) -> str | None:
    """오류 메시지 → 알림 종류. 알림 대상이 아니면 None."""
    lowered = (message or "").casefold()
    if CLAUDE_ERROR_MARKER in lowered:
        if any(hint in lowered for hint in _CLAUDE_LIMIT_HINTS):
            return KIND_CLAUDE_USAGE_LIMIT
        if any(hint in lowered for hint in _CLAUDE_AUTH_HINTS):
            return KIND_CLAUDE_AUTH_FAILED
        return None
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


def admin_recipients(settings=None) -> tuple[str, ...]:
    """한도·오류 알림을 받는 관리자 주소 (REQ-QAINTEL-049). 없으면 빈 튜플.

    설정 `notifications.admin_email`이 있으면 그 주소, 없으면 `NOTIFY_EMAIL_TO`의 첫 주소다.
    """
    settings = settings or get_settings()
    configured = str(settings.get("notifications.admin_email", "") or "").strip()
    if configured:
        return (configured,)
    raw = str(getattr(getattr(settings, "secrets", None), "notify_email_to", "") or "")
    first = next((part.strip() for part in raw.replace(";", ",").split(",") if part.strip()), "")
    return (first,) if first else ()


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


def _build_message(config: EmailSettings, kind: str, detail: str, context: dict | None,
                   subject: str = "", once: bool = False) -> EmailMessage:
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
    elif kind == KIND_CLAUDE_USAGE_LIMIT:
        lines += [
            "  1. 오류 내용의 한도 초기화 시각까지 새 분석은 계속 실패합니다.",
            "  2. 자주 닿으면 config.yaml 의 ai.claude.models.* 를 더 가벼운 모델로 바꾸거나",
            "     회사 Claude 계정의 사용량 한도를 확인합니다.",
        ]
    elif kind == KIND_CLAUDE_AUTH_FAILED:
        lines += [
            "  1. 서버 secrets.txt 의 CLAUDE_CODE_OAUTH_TOKEN 이 있고 만료되지 않았는지 확인합니다.",
            "  2. 만료됐으면 브라우저가 있는 PC 에서 회사 Team 계정으로 claude setup-token 을 다시 합니다.",
        ]
    lines += [
        "",
        "같은 한도에는 다시 보내지 않습니다." if once else f"쿨다운: 같은 문제로 {config.cooldown_minutes}분 안에는 다시 보내지 않습니다.",
        "이 메일은 자동 발송되었습니다.",
    ]

    message = EmailMessage()
    message["Subject"] = subject or mail_subject("", label)
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = _message_id(config.sender)
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


def notify(kind: str, detail: str, context: dict | None = None, storage: Storage | None = None,
           recipients: tuple[str, ...] = (), subject: str = "", dedupe_key: str = "") -> dict:
    """알림을 보낸다. **어떤 경우에도 예외를 올리지 않는다.**

    `recipients` 를 주면 `NOTIFY_EMAIL_TO` 대신 그 주소로만 보낸다(주소가 없으면 `not_configured`).
    `dedupe_key` 를 주면 쿨다운 대신 "같은 키에는 한 번만"이다(한도 묶음 알림, REQ-QAINTEL-049).
    반환값은 무엇을 했는지 기록용이다: `sent` / `skipped_cooldown` / `not_configured` / `failed`.
    """
    result = {"kind": kind, "status": "not_configured"}
    try:
        config = load_email_settings()
        targets = tuple(item.strip() for item in recipients or () if item and item.strip())
        if targets:
            config = replace(config, recipients=targets)
        if not config.configured:
            return result
        storage = storage or Storage()
        claim = f"{kind}:{dedupe_key}" if dedupe_key else kind
        cooldown_until = "" if dedupe_key else (datetime.now(timezone.utc) - timedelta(minutes=config.cooldown_minutes)).isoformat()
        if not storage.claim_notification(claim, cooldown_until):
            result["status"] = "skipped_cooldown"
            return result
        message = _build_message(config, kind, detail, context, subject, once=bool(dedupe_key))
        try:
            _send(config, message)
        except BaseException:
            if dedupe_key:
                # 한 번만 보내는 알림은 못 보냈으면 선점을 풀어, 같은 묶음의 다음 멈춤이 다시 시도하게 한다.
                try:
                    storage.release_notification(claim)
                except Exception:  # noqa: BLE001
                    logger.warning("notification_release_failed kind=%s", kind)
            raise
        storage.mark_notification_sent(claim)
        result["status"] = "sent"
        result["recipients"] = len(config.recipients)
        result["message_id"] = message["Message-ID"]
        logger.info("notification_sent kind=%s recipients=%d", kind, len(config.recipients))
        return result
    except Exception as exc:
        # 알림 실패가 원래 작업을 실패시키지 않는다.
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("notification_failed kind=%s error_type=%s", kind, type(exc).__name__)
        return result


def send_report(subject: str, text_body: str, html_body: str = "", recipients: tuple[str, ...] = ()) -> dict:
    """정기 보고 메일 (일일 QA 점검 요약 등). 쿨다운 없이 한 통 보낸다. **예외를 올리지 않는다.**

    수신자를 넘기지 않으면 `NOTIFY_EMAIL_TO` 로 보낸다. 반환값: `sent`(수신자 수·가린 수신 주소·`message_id`) /
    `not_configured` / `failed`. 주소 원문은 돌려주지 않는다 (REQ-MAIL-002).
    """
    result: dict = {"status": "not_configured"}
    config = None
    try:
        config = load_email_settings()
        targets = tuple(recipients) or config.recipients
        if not (config.enabled and config.host and config.sender and targets):
            return result
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = config.sender
        message["To"] = ", ".join(targets)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = _message_id(config.sender)
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        _send(config, message)
        result = {"status": "sent", "recipients": len(targets), "recipient_masks": [mask_address(item) for item in targets],
                  "message_id": message["Message-ID"]}
        logger.info("report_sent recipients=%d", len(targets))
        return result
    except Exception as exc:
        detail = getattr(exc, "smtp_error", "")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        detail = str(detail)
        if config:
            for secret in (config.username, config.password, config.sender, *config.recipients):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
        detail = mask_text(detail)[0].replace("\r", " ").replace("\n", " ")[:500]
        error = ": ".join(str(part) for part in (type(exc).__name__, getattr(exc, "smtp_code", ""), detail) if part)
        result = {"status": "failed", "error": error}
        logger.warning("report_failed error=%s", error)
        return result


_TEST_NOTICE = "시험 발송입니다. 실제 오류가 아닙니다."
_SAMPLE_NOTICE = "아래 이슈·사양·숫자는 모두 예시 값입니다. 실제 점검 결과가 아닙니다."


def _sample_report() -> tuple[str, str, str]:
    """예약 점검 요약 시험 메일. 실제 요약 메일과 같은 구성(`report.build_email`)에 예시 값을 넣는다 (REQ-MAIL-003)."""
    from app.modules.daily_qa import report  # report 가 이 모듈의 mail_subject 를 쓰므로 부를 때 읽는다

    base = str(get_settings().get("daily_qa.review_base_url", "") or "").strip().rstrip("/")
    run_id = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-040000")
    review_url = f"{base}/qa-agent/runs/{run_id}" if base else f"/qa-agent/runs/{run_id}"
    summary = {
        "savings_enabled": True, "findings": 2, "questions": 0, "claude_calls": 2, "pending_analysis_events": 3,
        "scope_targets": {"spec": 1, "issues": 1}, "next_run": "", "by_skill": {"qa-issue-analysis": 2},
        "by_verdict": {"qa-issue-analysis": {"PARTIALLY_COVERED": 1, "REQUIREMENT_INFORMATION": 1}},
        "issue_results": [
            {"id": 0, "subject": "예시-이슈", "subject_title": "예시 이슈 제목", "reporter_id": "sample-writer",
             "reporter_name": "예시 작성자", "issue_updated_at": "", "verdict": "PARTIALLY_COVERED", "analysis_type": "NEW_ISSUE"}],
        "spec_results": [
            {"id": 0, "subject": "예시-사양", "subject_title": "예시 사양 제목", "verdict": "REQUIREMENT_INFORMATION",
             "analysis_type": "SPEC_COVERAGE"}],
    }
    usage = {"before": {"five_hour": None, "seven_day": None, "measured_at": ""},
             "after": {"five_hour": 10.0, "seven_day": 40.0, "five_hour_resets_kst": "", "seven_day_resets_kst": ""},
             "calls": 2, "total_tokens": 12345, "model": "예시", "by_target": []}
    stages = {"collect_srs": {"status": "ok", "note": ""}, "collect_issues": {"status": "ok", "note": ""},
              "email": {"status": "ok", "note": ""}}
    subject, text, html_body = report.build_email(run_id, "성공", stages, summary, review_url, "", product="예시", usage=usage)
    subject = mail_subject("", f"[시험] {subject.removeprefix(SUBJECT_PREFIX).strip()}")
    notice_html = (f"<div style='padding:10px 24px;background:#fef3c7;color:#92400e;font:14px sans-serif'>"
                   f"<b>{_TEST_NOTICE}</b> {_SAMPLE_NOTICE}</div>")
    html_body, inserted = re.subn(r"(<body[^>]*>)", lambda match: match.group(1) + notice_html, html_body, count=1)
    if not inserted:
        html_body = notice_html + html_body
    return subject, "\n".join([_TEST_NOTICE, _SAMPLE_NOTICE, "", text, "", "이 메일은 자동 발송되었습니다."]), html_body


_CHANGE_SAMPLE_NOTICE = "아래 내용은 모두 예시 자료입니다. 실제 변경 리포트가 아닙니다."


def _sample_change_report() -> tuple[str, str, str]:
    """변경 리포트 시험 메일. 실제 메일과 같은 구성(`change_mail.build_change_email`)에 꺾쇠 자리표시 값을 넣는다 (REQ-ALMCHG-004)."""
    from app.modules.daily_qa import change_mail
    from app.modules.daily_qa.holidays import KST

    base = str(get_settings().get("daily_qa.review_base_url", "") or "").strip().rstrip("/")
    today = datetime.now(KST).date()
    report = change_mail.sample_report(today)
    url = f"{base}/qa-agent/changes?product=예시&since={today.isoformat()}&until={today.isoformat()}"
    subject, text, html_body = change_mail.build_change_email(
        report, product="예시", page_url=url, ai_state={"label": "꺼짐", "deferred": 0})
    subject = mail_subject("", f"[시험] {subject.removeprefix(SUBJECT_PREFIX).strip()}")
    notice_html = (f"<div style='padding:10px 24px;background:#fef3c7;color:#92400e;font:14px sans-serif'>"
                   f"<b>{_TEST_NOTICE}</b> {_CHANGE_SAMPLE_NOTICE}</div>")
    html_body, inserted = re.subn(r"(<body[^>]*>)", lambda match: match.group(1) + notice_html, html_body, count=1)
    if not inserted:
        html_body = notice_html + html_body
    return subject, "\n".join([_TEST_NOTICE, _CHANGE_SAMPLE_NOTICE, "", text]), html_body


def send_test(kind: str, recipients: tuple[str, ...]) -> dict:
    """설정 화면의 시험 발송 (REQ-MAIL-003). 고른 수신자에게만 보내고 재발송 대기 기록은 건드리지 않는다.

    `kind` 는 알림 종류 값 또는 `report`(예약 점검 요약 예시)다. 예외를 올리지 않는다.
    """
    targets = tuple(item.strip() for item in recipients or () if item and item.strip())
    if kind in ("report", "change_report"):
        if not targets:
            return {"status": "failed", "error": "시험 수신자가 없습니다."}
        try:
            subject, text, html_body = _sample_report() if kind == "report" else _sample_change_report()
        except Exception as exc:  # noqa: BLE001 - 시험 발송은 예외를 올리지 않는다
            logger.warning("send_test_report_build_failed error_type=%s", type(exc).__name__)
            return {"status": "failed", "error": f"시험 메일을 만들지 못했습니다: {type(exc).__name__}"}
        return send_report(subject, text, html_body, targets)
    if kind in KIND_LABELS:
        label = KIND_LABELS[kind]
        body = [f"이 메일은 '{label}' 알림이 이 수신자에게 도착하는지 확인하는 시험입니다."]
    else:
        return {"status": "failed", "error": "알 수 없는 시험 종류입니다."}
    if not targets:
        return {"status": "failed", "error": "시험 수신자가 없습니다."}
    text = "\n".join([_TEST_NOTICE, "", *body, "", "이 메일은 자동 발송되었습니다."])
    return send_report(mail_subject("", f"[시험] {label}"), text, "", targets)


def notify_for_error(message: str, context: dict | None = None, storage: Storage | None = None) -> dict | None:
    """오류 메시지가 알림 대상이면 보낸다. 대상이 아니면 아무것도 하지 않는다."""
    kind = classify_error(message)
    if kind is None:
        return None
    return notify(kind, message, context=context, storage=storage)
