"""Audit logging.

Every audit row is written inside the same transaction as the change it
describes, so an action can never be applied without its log entry (or the log
entry recorded for an action that rolled back).

Nothing in the application updates or deletes ``audit_logs``; this module only
inserts.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session as DbSession

from .models import AuditLog, Document, DocumentVersion, Product, User

# --- action names (stable strings; used by the UI filter) ------------------- #
LOGIN = "LOGIN"
LOGIN_FAILURE = "LOGIN_FAILURE"
LOGOUT = "LOGOUT"

USER_CREATE = "USER_CREATE"
USER_UPDATE = "USER_UPDATE"
USER_PASSWORD_RESET = "USER_PASSWORD_RESET"
USER_PASSWORD_CHANGE = "USER_PASSWORD_CHANGE"
USER_ENABLE = "USER_ENABLE"
USER_DISABLE = "USER_DISABLE"
USER_ROLE_CHANGE = "USER_ROLE_CHANGE"

PRODUCT_CREATE = "PRODUCT_CREATE"
PRODUCT_UPDATE = "PRODUCT_UPDATE"

CATEGORY_CREATE = "CATEGORY_CREATE"
CATEGORY_UPDATE = "CATEGORY_UPDATE"

DOCUMENT_CREATE = "DOCUMENT_CREATE"
DOCUMENT_UPDATE = "DOCUMENT_UPDATE"
DOCUMENT_ARCHIVE = "DOCUMENT_ARCHIVE"
DOCUMENT_RESTORE = "DOCUMENT_RESTORE"
DOCUMENT_DOWNLOAD = "DOCUMENT_DOWNLOAD"

VERSION_UPLOAD = "VERSION_UPLOAD"
VERSION_UPDATE = "VERSION_UPDATE"
VERSION_ARCHIVE = "VERSION_ARCHIVE"
VERSION_RESTORE = "VERSION_RESTORE"
CURRENT_VERSION_CHANGE = "CURRENT_VERSION_CHANGE"

SETTING_UPDATE = "SETTING_UPDATE"

ACTION_LABELS_KO: dict[str, str] = {
    LOGIN: "로그인",
    LOGIN_FAILURE: "로그인 실패",
    LOGOUT: "로그아웃",
    USER_CREATE: "사용자 생성",
    USER_UPDATE: "사용자 수정",
    USER_PASSWORD_RESET: "비밀번호 초기화",
    USER_PASSWORD_CHANGE: "비밀번호 변경",
    USER_ENABLE: "사용자 활성화",
    USER_DISABLE: "사용자 비활성화",
    USER_ROLE_CHANGE: "권한 변경",
    PRODUCT_CREATE: "제품 생성",
    PRODUCT_UPDATE: "제품 수정",
    CATEGORY_CREATE: "분류 생성",
    CATEGORY_UPDATE: "분류 수정",
    DOCUMENT_CREATE: "문서 생성",
    DOCUMENT_UPDATE: "문서 수정",
    DOCUMENT_ARCHIVE: "문서 보관",
    DOCUMENT_RESTORE: "문서 복원",
    DOCUMENT_DOWNLOAD: "문서 다운로드",
    VERSION_UPLOAD: "버전 업로드",
    VERSION_UPDATE: "버전 정보 수정",
    VERSION_ARCHIVE: "버전 보관",
    VERSION_RESTORE: "버전 복원",
    CURRENT_VERSION_CHANGE: "Current 버전 변경",
    SETTING_UPDATE: "설정 변경",
}


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Only trust what nginx itself wrote.  Both nginx configs (standalone and
    # platform) set ``X-Real-IP $remote_addr``, which replaces anything the
    # browser sent.  ``X-Forwarded-For`` uses ``$proxy_add_x_forwarded_for``,
    # which APPENDS the real peer to the browser's own header, so only its last
    # value is trustworthy -- the first one can be anything the user typed.
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:64]
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last[:64]
    if request.client:
        return request.client.host[:64]
    return None


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    return ua[:512] if ua else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def record(
    db: DbSession,
    *,
    action: str,
    actor: User | None = None,
    request: Request | None = None,
    product: Product | None = None,
    document: Document | None = None,
    version: DocumentVersion | None = None,
    target_user: User | None = None,
    target_label: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: str | None = None,
    actor_login_id: str | None = None,
) -> AuditLog:
    """Add an audit row to the current transaction (caller commits)."""
    if product is None and document is not None:
        product = document.product
    if document is None and version is not None:
        document = version.document
        if product is None and document is not None:
            product = document.product

    entry = AuditLog(
        action=action,
        actor_user_id=actor.id if actor else None,
        actor_login_id=(actor.login_id if actor else actor_login_id),
        actor_display_name=actor.display_name if actor else None,
        product_id=product.id if product else None,
        product_name=product.name if product else None,
        document_id=document.id if document else None,
        document_name=document.name if document else None,
        version_id=version.id if version else None,
        version_label=version.label if version else None,
        target_user_id=target_user.id if target_user else None,
        target_label=(
            target_label
            if target_label is not None
            else (
                f"{target_user.display_name}({target_user.login_id})"
                if target_user
                else None
            )
        ),
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        before_value=_jsonable(before) if before else None,
        after_value=_jsonable(after) if after else None,
        detail=detail,
    )
    db.add(entry)
    return entry
