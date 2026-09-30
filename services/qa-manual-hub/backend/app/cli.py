"""Administrative CLI.

    python -m app.cli bootstrap-admin            # create the first admin
    python -m app.cli seed-catalog               # default categories (+ product)
    python -m app.cli reset-password <login_id>  # rescue path if admin is locked out
    python -m app.cli list-users
    python -m app.cli check-storage              # verify every version's file
    python -m app.cli check-storage --verify-sha256   # ... and re-hash its bytes
    python -m app.cli warm-preview-cache         # pre-convert office docs to PDF
    python -m app.cli purge-sessions

The initial admin password is never a literal in this file, in the repository, or
in any committed config.  It is read from the ``BOOTSTRAP_ADMIN_PASSWORD``
environment variable or typed at an interactive prompt.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

from . import audit
from .config import settings
from .db import SessionLocal
from .models import (
    ROLE_ADMIN,
    Session as SessionModel,
    DocumentCategory,
    DocumentVersion,
    Product,
    User,
)
from .security import hash_password, validate_password_strength
from .storage import get_storage

DEFAULT_CATEGORIES: list[tuple[str, str, int]] = [
    ("Operation Manual", "제품 사용/운영 매뉴얼", 10),
    ("Service Manual", "서비스/유지보수 매뉴얼", 20),
    ("QC Manual", "품질관리(Quality Control) 매뉴얼", 30),
    ("DICOM Conformance Statement", "DICOM 적합성 선언서", 40),
    ("Installation Manual", "설치 매뉴얼", 50),
    ("User Manual", "사용자 매뉴얼", 60),
    ("Release Note", "릴리즈 노트", 70),
    ("Specification", "사양서", 80),
    ("Technical Manual", "기술 매뉴얼", 90),
    ("Other", "기타 문서", 999),
]


def _read_password(prompt: str, env_key: str | None = None) -> str:
    if env_key:
        from_env = os.environ.get(env_key)
        if from_env:
            return from_env
    if not sys.stdin.isatty():
        raise SystemExit(
            f"비밀번호를 입력할 수 없습니다. {env_key or 'PASSWORD'} 환경변수를 "
            "설정하거나 대화형 터미널에서 실행하세요."
        )
    first = getpass.getpass(prompt)
    second = getpass.getpass("확인을 위해 다시 입력: ")
    if first != second:
        raise SystemExit("두 번 입력한 비밀번호가 일치하지 않습니다.")
    return first


# --------------------------------------------------------------------------- #
def cmd_bootstrap_admin(args: argparse.Namespace) -> int:
    login_id = (
        args.login_id or settings.bootstrap_admin_login_id or "admin"
    ).strip()
    display_name = (
        args.display_name or settings.bootstrap_admin_display_name or "QA Admin"
    ).strip()

    with SessionLocal() as db:
        existing_admins = db.scalar(
            select(func.count(User.id)).where(User.role == ROLE_ADMIN)
        )
        existing = db.scalar(
            select(User).where(func.lower(User.login_id) == login_id.lower())
        )
        if existing is not None:
            print(
                f"'{login_id}' 계정이 이미 존재합니다. "
                "비밀번호를 바꾸려면 reset-password 를 사용하세요."
            )
            return 1
        if existing_admins and not args.force:
            print(
                f"관리자 계정이 이미 {existing_admins}개 있습니다. "
                "추가로 만들려면 --force 를 지정하세요."
            )
            return 1

        password = args.password or settings.bootstrap_admin_password or _read_password(
            f"'{login_id}' 초기 비밀번호: ", "BOOTSTRAP_ADMIN_PASSWORD"
        )
        if err := validate_password_strength(password):
            print(err)
            return 1

        user = User(
            login_id=login_id,
            display_name=display_name,
            password_hash=hash_password(password),
            role=ROLE_ADMIN,
            is_active=True,
            must_change_password=args.must_change_password,
        )
        db.add(user)
        db.flush()
        user.created_by = user.id
        user.updated_by = user.id
        audit.record(
            db,
            action=audit.USER_CREATE,
            actor=user,
            target_user=user,
            detail="CLI bootstrap-admin 으로 최초 관리자 생성",
            after={"login_id": login_id, "role": ROLE_ADMIN},
        )
        db.commit()

    print(f"관리자 계정을 생성했습니다: {login_id} ({display_name})")
    if args.must_change_password:
        print("최초 로그인 시 비밀번호 변경이 요구됩니다.")
    return 0


def cmd_seed_catalog(args: argparse.Namespace) -> int:
    created_categories: list[str] = []
    created_products: list[str] = []
    with SessionLocal() as db:
        actor = db.scalar(
            select(User).where(User.role == ROLE_ADMIN).order_by(User.created_at)
        )
        for name, description, order in DEFAULT_CATEGORIES:
            hit = db.scalar(
                select(DocumentCategory).where(
                    func.lower(DocumentCategory.name) == name.lower()
                )
            )
            if hit is None:
                db.add(
                    DocumentCategory(
                        name=name,
                        description=description,
                        sort_order=order,
                        created_by=actor.id if actor else None,
                        updated_by=actor.id if actor else None,
                    )
                )
                # Same event the Categories screen writes, so a catalogue seeded
                # from the CLI has a creation trail in Audit Logs too.
                audit.record(
                    db,
                    action=audit.CATEGORY_CREATE,
                    actor=None,
                    actor_login_id="cli",
                    target_label=name,
                    after={"name": name, "description": description},
                    detail="CLI seed-catalog",
                )
                created_categories.append(name)

        for product_name in args.product or []:
            hit = db.scalar(
                select(Product).where(func.lower(Product.name) == product_name.lower())
            )
            if hit is None:
                product = Product(
                    name=product_name,
                    sort_order=10 * (len(created_products) + 1),
                    created_by=actor.id if actor else None,
                    updated_by=actor.id if actor else None,
                )
                db.add(product)
                db.flush()  # assigns product.id for the audit row
                audit.record(
                    db,
                    action=audit.PRODUCT_CREATE,
                    actor=None,
                    actor_login_id="cli",
                    product=product,
                    after={"name": product_name},
                    detail="CLI seed-catalog",
                )
                created_products.append(product_name)
        db.commit()

    print(f"분류 {len(created_categories)}건 생성: {', '.join(created_categories) or '-'}")
    print(f"제품 {len(created_products)}건 생성: {', '.join(created_products) or '-'}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        user = db.scalar(
            select(User).where(func.lower(User.login_id) == args.login_id.lower())
        )
        if user is None:
            print(f"사용자를 찾을 수 없습니다: {args.login_id}")
            return 1
        password = args.password or _read_password(
            f"'{user.login_id}' 새 비밀번호: ", "NEW_PASSWORD"
        )
        if err := validate_password_strength(password):
            print(err)
            return 1
        user.password_hash = hash_password(password)
        user.must_change_password = args.must_change_password
        now = datetime.now(UTC)
        for record in db.scalars(
            select(SessionModel).where(
                SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None)
            )
        ):
            record.revoked_at = now
        audit.record(
            db,
            action=audit.USER_PASSWORD_RESET,
            actor=None,
            actor_login_id="cli",
            target_user=user,
            detail="CLI reset-password",
        )
        db.commit()
    print(f"'{args.login_id}' 비밀번호를 변경했습니다. 기존 세션은 모두 무효화되었습니다.")
    return 0


def cmd_list_users(_: argparse.Namespace) -> int:
    with SessionLocal() as db:
        rows = db.scalars(select(User).order_by(User.login_id)).all()
    print(f"{'LOGIN ID':<20} {'NAME':<20} {'ROLE':<8} {'ACTIVE':<7} LAST LOGIN")
    for u in rows:
        last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-"
        print(
            f"{u.login_id:<20} {u.display_name:<20} {u.role:<8} "
            f"{'Y' if u.is_active else 'N':<7} {last}"
        )
    print(f"\n총 {len(rows)}명")
    return 0


def _file_sha256(storage, storage_key: str) -> str:
    digest = hashlib.sha256()
    with storage.open(storage_key) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_check_storage(args: argparse.Namespace) -> int:
    verify_sha256 = bool(getattr(args, "verify_sha256", False))
    storage = get_storage()
    missing: list[str] = []
    mismatched: list[str] = []
    corrupted: list[str] = []
    total = 0
    with SessionLocal() as db:
        for version in db.scalars(select(DocumentVersion)).unique():
            total += 1
            stored = version.stored_file
            if not storage.exists(stored.storage_key):
                missing.append(f"{stored.storage_key} ({version.label})")
                continue
            if storage.size(stored.storage_key) != stored.byte_size:
                mismatched.append(f"{stored.storage_key} ({version.label})")
                continue
            # Same size but different bytes is only visible by re-hashing.
            if verify_sha256 and _file_sha256(storage, stored.storage_key) != stored.sha256:
                corrupted.append(f"{stored.storage_key} ({version.label})")
    print(f"검사한 버전: {total}")
    print(f"파일 없음: {len(missing)}")
    for item in missing:
        print(f"  - {item}")
    print(f"크기 불일치: {len(mismatched)}")
    for item in mismatched:
        print(f"  - {item}")
    if verify_sha256:
        print(f"SHA-256 불일치: {len(corrupted)}")
        for item in corrupted:
            print(f"  - {item}")
    return 0 if not missing and not mismatched and not corrupted else 2


def cmd_warm_preview_cache(_: argparse.Namespace) -> int:
    """Pre-convert every office-format version (doc/docx/xls/xlsx/ppt/pptx) to
    PDF so the in-browser viewer is a cache hit for everyone from now on.

    New uploads and Set-as-Current already trigger this automatically in the
    background; this command is the one-time catch-up for versions that were
    uploaded before that existed.
    """
    from time import monotonic

    from .storage import ConversionError, needs_conversion

    storage = get_storage()
    with SessionLocal() as db:
        versions = db.scalars(select(DocumentVersion)).unique().all()
        targets = [v for v in versions if needs_conversion(v.stored_file.file_extension)]

    print(f"오피스 문서 버전: {len(targets)}건")
    converted, cached, failed = 0, 0, []
    for v in targets:
        key = v.stored_file.storage_key
        if storage.has_cached_preview(key):
            cached += 1
            continue
        start = monotonic()
        try:
            storage.ensure_preview_pdf(key)
        except ConversionError as exc:
            failed.append(f"{v.label} ({key}): {exc}")
            continue
        converted += 1
        print(f"  변환 완료: {v.label} ({monotonic() - start:.1f}s)")

    print(f"\n이미 캐시됨: {cached}건 / 새로 변환: {converted}건 / 실패: {len(failed)}건")
    for item in failed:
        print(f"  - {item}")
    return 0 if not failed else 2


def cmd_purge_sessions(_: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        stale = db.scalars(
            select(SessionModel).where(SessionModel.expires_at < now)
        ).all()
        for record in stale:
            db.delete(record)
        db.commit()
    print(f"만료 세션 {len(stale)}건을 정리했습니다.")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap-admin", help="최초 관리자 계정 생성")
    p.add_argument("--login-id", default=None)
    p.add_argument("--display-name", default=None)
    p.add_argument(
        "--password",
        default=None,
        help="생략 시 BOOTSTRAP_ADMIN_PASSWORD 환경변수 또는 대화형 입력을 사용",
    )
    p.add_argument("--force", action="store_true", help="관리자가 이미 있어도 추가 생성")
    p.add_argument(
        "--no-must-change-password",
        dest="must_change_password",
        action="store_false",
    )
    p.set_defaults(func=cmd_bootstrap_admin, must_change_password=True)

    p = sub.add_parser("seed-catalog", help="기본 문서 분류(및 제품) 생성")
    p.add_argument("--product", action="append", help="생성할 제품 이름 (반복 지정 가능)")
    p.set_defaults(func=cmd_seed_catalog)

    p = sub.add_parser("reset-password", help="비밀번호 강제 변경 (관리자 잠김 복구용)")
    p.add_argument("login_id")
    p.add_argument("--password", default=None)
    p.add_argument(
        "--no-must-change-password",
        dest="must_change_password",
        action="store_false",
    )
    p.set_defaults(func=cmd_reset_password, must_change_password=True)

    sub.add_parser("list-users", help="사용자 목록").set_defaults(func=cmd_list_users)
    p = sub.add_parser(
        "check-storage", help="DB에 등록된 모든 버전 파일의 존재/크기 검증"
    )
    p.add_argument(
        "--verify-sha256",
        action="store_true",
        help="파일 내용을 다시 읽어 업로드 때 저장한 SHA-256 값과 비교 (느림)",
    )
    p.set_defaults(func=cmd_check_storage)
    sub.add_parser(
        "warm-preview-cache",
        help="오피스 문서(doc/docx/xls/xlsx/ppt/pptx) 버전을 미리 PDF로 변환해 캐시",
    ).set_defaults(func=cmd_warm_preview_cache)
    sub.add_parser("purge-sessions", help="만료 세션 정리").set_defaults(
        func=cmd_purge_sessions
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
