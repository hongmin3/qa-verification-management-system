"""Admin-only user management (spec sections 8, 9, 49)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import audit
from ..db import get_db
from ..deps import require_admin, require_admin_password_current
from ..models import ROLE_ADMIN, Session as SessionModel, User
from ..schemas import (
    Message,
    PasswordResetRequest,
    UserCreate,
    UserOut,
    UserUpdate,
)
from ..security import hash_password, validate_password_strength

router = APIRouter(prefix="/api/users", tags=["users"])


def _get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다."
        )
    return user


def _revoke_all_sessions(db: DbSession, user_id: uuid.UUID) -> None:
    """Called when an account is disabled or its password is reset, so an open
    tab cannot keep working with stale credentials."""
    now = datetime.now(UTC)
    for record in db.scalars(
        select(SessionModel).where(
            SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)
        )
    ):
        record.revoked_at = now


@router.get("", response_model=list[UserOut])
def list_users(
    q: str | None = Query(default=None, max_length=128),
    include_inactive: bool = Query(default=True),
    db: DbSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[UserOut]:
    stmt = select(User)
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.login_id).like(needle)
            | func.lower(User.display_name).like(needle)
        )
    if not include_inactive:
        stmt = stmt.where(User.is_active.is_(True))
    stmt = stmt.order_by(User.is_active.desc(), User.login_id)
    return [UserOut.model_validate(u) for u in db.scalars(stmt)]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> UserOut:
    if err := validate_password_strength(payload.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    login_id = payload.login_id.strip()
    exists = db.scalar(
        select(User.id).where(func.lower(User.login_id) == login_id.lower())
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 사용 중인 로그인 ID입니다: {login_id}",
        )

    user = User(
        login_id=login_id,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        must_change_password=payload.must_change_password,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 사용 중인 로그인 ID입니다: {login_id}",
        ) from None

    audit.record(
        db,
        action=audit.USER_CREATE,
        actor=admin,
        request=request,
        target_user=user,
        after={
            "login_id": user.login_id,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
        },
    )
    db.commit()
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: uuid.UUID,
    db: DbSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> UserOut:
    return UserOut.model_validate(_get_user(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> UserOut:
    user = _get_user(db, user_id)
    before = {
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
    }

    data = payload.model_dump(exclude_unset=True)

    # Guard rails so an admin cannot lock the whole system out of admin access.
    if user.id == admin.id:
        if data.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="자신의 계정을 비활성화할 수 없습니다.",
            )
        if data.get("role") not in (None, ROLE_ADMIN):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="자신의 관리자 권한을 해제할 수 없습니다.",
            )

    losing_admin = user.role == ROLE_ADMIN and (
        data.get("role") not in (None, ROLE_ADMIN) or data.get("is_active") is False
    )
    if losing_admin:
        remaining = db.scalar(
            select(func.count(User.id)).where(
                User.role == ROLE_ADMIN,
                User.is_active.is_(True),
                User.id != user.id,
            )
        )
        if not remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="활성 관리자가 최소 1명 있어야 합니다.",
            )

    if "display_name" in data and data["display_name"] is not None:
        name = data["display_name"].strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="표시 이름을 입력하세요."
            )
        user.display_name = name
    if data.get("role") is not None:
        user.role = data["role"]
    if data.get("must_change_password") is not None:
        user.must_change_password = data["must_change_password"]
    if data.get("is_active") is not None:
        user.is_active = data["is_active"]
        if not user.is_active:
            _revoke_all_sessions(db, user.id)

    user.updated_by = admin.id
    after = {
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
    }

    # Emit the specific action so the audit list reads naturally, plus the
    # generic update when other fields moved.
    if before["is_active"] != after["is_active"]:
        audit.record(
            db,
            action=audit.USER_ENABLE if user.is_active else audit.USER_DISABLE,
            actor=admin,
            request=request,
            target_user=user,
            before={"is_active": before["is_active"]},
            after={"is_active": after["is_active"]},
        )
    if before["role"] != after["role"]:
        audit.record(
            db,
            action=audit.USER_ROLE_CHANGE,
            actor=admin,
            request=request,
            target_user=user,
            before={"role": before["role"]},
            after={"role": after["role"]},
        )
    if before["display_name"] != after["display_name"] or (
        before["must_change_password"] != after["must_change_password"]
    ):
        audit.record(
            db,
            action=audit.USER_UPDATE,
            actor=admin,
            request=request,
            target_user=user,
            before=before,
            after=after,
        )

    db.commit()
    return UserOut.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=Message)
def reset_password(
    user_id: uuid.UUID,
    payload: PasswordResetRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    admin: User = Depends(require_admin_password_current),
) -> Message:
    if err := validate_password_strength(payload.new_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)

    user = _get_user(db, user_id)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = payload.must_change_password
    user.updated_by = admin.id
    _revoke_all_sessions(db, user.id)

    audit.record(
        db,
        action=audit.USER_PASSWORD_RESET,
        actor=admin,
        request=request,
        target_user=user,
        detail=(
            "관리자 비밀번호 초기화"
            + (" (최초 로그인 시 변경 요구)" if payload.must_change_password else "")
        ),
    )
    db.commit()
    return Message(
        detail=f"{user.display_name}({user.login_id}) 비밀번호가 초기화되었습니다."
    )
