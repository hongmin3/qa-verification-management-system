"""설정 탭의 관리자 로그인 (REQ-WEB-008, NFR-SEC-004).

비밀번호 한 칸으로 들어가는 간단한 로그인이다. 비밀번호를 따로 정한 적이 없으면 처음 비밀번호 `1` 이다
(사용자 결정 2026-10-15). 로그인하면 서명한 쿠키(`qa_admin`)를 8시간 준다.

- 쿠키 값: `<만료 epoch>.<hmac_sha256(서버 키, 만료 epoch)>`. 서버 키는 `data/admin_session.key` 에
  처음 필요할 때 만든다(32바이트 무작위, 0600). 서버 키가 바뀌면 이미 준 쿠키는 모두 무효가 된다.
- 비밀번호: PBKDF2-SHA256 600,000회. `admin.password_hash` 설정에 저장한다.
- 로그인 실패 5회면 10분 동안 로그인을 받지 않는다(메모리 카운터, 서버 재시작하면 풀린다).
- 비밀번호 원문과 암호화 값은 로그에 쓰지 않는다 (NFR-SEC-002).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.setting_overrides import PASSWORD_KEY, SettingOverrides

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "qa_admin"
SESSION_SECONDS = 8 * 3600
INITIAL_PASSWORD = "1"
PBKDF2_ROUNDS = 600_000
MIN_PASSWORD, MAX_PASSWORD = 4, 128
MAX_FAILURES = 5
LOCK_SECONDS = 600

_state_lock = threading.Lock()
_failures = 0
_locked_until = 0.0


def _root(root: Path | None) -> Path:
    if root is not None:
        return root
    from app.core.config import get_settings
    return get_settings().root


def _key_file(root: Path | None) -> Path:
    return _root(root) / "data" / "admin_session.key"


def _read_key(root: Path | None, *, create: bool) -> bytes | None:
    path = _key_file(root)
    try:
        key = path.read_bytes()
        if len(key) == 32:
            return key
    except OSError:
        pass
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:                       # 다른 요청이 방금 만들었다
        return _read_key(root, create=False)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(secrets.token_bytes(32))
    return _read_key(root, create=False)


def _sign(key: bytes, expires: str) -> str:
    return hmac.new(key, expires.encode("ascii"), hashlib.sha256).hexdigest()


def issue(now: float, root: Path | None = None) -> str:
    """로그인 쿠키 값을 만든다. 만료는 `now` 로부터 8시간 뒤다."""
    key = _read_key(root, create=True)
    expires = str(int(now) + SESSION_SECONDS)
    return f"{expires}.{_sign(key, expires)}"


def verify(cookie: str | None, now: float, root: Path | None = None) -> bool:
    """쿠키가 우리가 서명했고 아직 만료 전이면 True. 서버 키가 없으면 만들지 않고 False."""
    if not isinstance(cookie, str) or cookie.count(".") != 1:
        return False
    expires, _, signature = cookie.partition(".")
    if not expires.isascii() or not expires.isdigit() or len(expires) > 12 or len(signature) != 64:
        return False
    key = _read_key(root, create=False)
    if key is None:
        return False
    if not hmac.compare_digest(_sign(key, expires), signature):
        return False
    return now < int(expires)


def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def check_password(plain: str, settings: Settings | None = None) -> bool:
    """저장한 비밀번호와 맞는지. 저장한 값이 없으면 처음 비밀번호(`1`)와 비교한다."""
    if not isinstance(plain, str):
        return False
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    stored = str(settings.get(PASSWORD_KEY, "") or "")
    if not stored:
        return hmac.compare_digest(plain.encode("utf-8"), INITIAL_PASSWORD.encode("utf-8"))
    try:
        scheme, rounds, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        if rounds != str(PBKDF2_ROUNDS):
            return False  # 비정상 반복 횟수로 계산을 오래 돌리지 않는다
        actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), digest)
    except ValueError:
        return False


def set_password(plain: str, actor: str, settings: Settings | None = None) -> None:
    """새 비밀번호를 저장하고 변경 이력에 남긴다(값은 남기지 않는다). 설정을 다시 읽는 일은 부른 쪽이 한다."""
    if not isinstance(plain, str) or not MIN_PASSWORD <= len(plain) <= MAX_PASSWORD:
        raise ValueError(f"비밀번호는 {MIN_PASSWORD}자 이상 {MAX_PASSWORD}자 이하로 입력해 주세요.")
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    SettingOverrides(settings.root / "data/app.db").save({PASSWORD_KEY: hash_password(plain)}, settings, actor)


def reset_lockout() -> None:
    global _failures, _locked_until
    with _state_lock:
        _failures, _locked_until = 0, 0.0


def attempt_login(plain: str, settings: Settings | None, now: float) -> str:
    """`ok`·`bad`·`locked` 중 하나. 5번 틀리면 10분 동안 맞는 비밀번호도 받지 않는다."""
    global _failures, _locked_until
    with _state_lock:
        if now < _locked_until:
            return "locked"
    matched = check_password(plain, settings)
    with _state_lock:
        if matched:
            _failures = 0
            return "ok"
        _failures += 1
        if _failures >= MAX_FAILURES:
            _failures, _locked_until = 0, now + LOCK_SECONDS
            logger.warning("admin_login_locked seconds=%d", LOCK_SECONDS)
        return "bad"
