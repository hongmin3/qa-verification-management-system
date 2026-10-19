"""Validates: REQ-WEB-008, NFR-SEC-004, NFR-SEC-002."""
import os
import shutil
import sqlite3
import stat
import sys

import pytest

from app.core import admin_auth
from app.core.config import ROOT, build_settings

NOW = 1_800_000_000


@pytest.fixture
def settings(tmp_path):
    shutil.copyfile(ROOT / "config.yaml", tmp_path / "config.yaml")
    admin_auth.reset_lockout()
    return build_settings(tmp_path)


def test_session_cookie_round_trip_and_key_file(settings):
    cookie = admin_auth.issue(NOW, settings.root)
    expires, _, signature = cookie.partition(".")
    assert expires == str(NOW + 8 * 3600) and len(signature) == 64
    assert admin_auth.verify(cookie, NOW, settings.root) is True
    key_file = settings.root / "data/admin_session.key"
    assert key_file.is_file() and len(key_file.read_bytes()) == 32
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600


def test_session_cookie_expires_after_eight_hours(settings):
    cookie = admin_auth.issue(NOW, settings.root)
    assert admin_auth.verify(cookie, NOW + 8 * 3600 - 1, settings.root) is True
    assert admin_auth.verify(cookie, NOW + 8 * 3600, settings.root) is False


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3", "x.y", ".", "99999999999.00", None])
def test_malformed_cookie_is_refused(settings, bad):
    assert admin_auth.verify(bad, NOW, settings.root) is False


def test_tampered_expiry_or_signature_is_refused(settings):
    cookie = admin_auth.issue(NOW, settings.root)
    expires, _, signature = cookie.partition(".")
    assert admin_auth.verify(f"{int(expires) + 3600}.{signature}", NOW, settings.root) is False
    assert admin_auth.verify(f"{expires}.{'0' * 64}", NOW, settings.root) is False


def test_cookie_from_another_key_is_refused(settings, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    assert admin_auth.verify(admin_auth.issue(NOW, other), NOW, settings.root) is False


def test_verify_does_not_create_a_key(settings):
    assert admin_auth.verify(f"{NOW + 10}.{'0' * 64}", NOW, settings.root) is False
    assert not (settings.root / "data/admin_session.key").exists()


def test_initial_password_is_one_and_hash_is_never_stored_for_it(settings):
    assert admin_auth.check_password("1", settings) is True
    assert admin_auth.check_password("2", settings) is False
    assert admin_auth.check_password("", settings) is False
    assert "admin.password_hash" not in settings.overrides


def test_set_password_stores_pbkdf2_hash_and_records_history(settings):
    admin_auth.set_password("a-new-password", "tester", settings)
    stored = sqlite3.connect(settings.root / "data/app.db").execute(
        "SELECT value FROM app_setting_overrides WHERE key='admin.password_hash'").fetchone()[0]
    scheme, rounds, salt, digest = stored.split("$")
    assert (scheme, rounds) == ("pbkdf2_sha256", "600000") and len(salt) == 32 and len(digest) == 64
    reloaded = build_settings(settings.root)
    assert admin_auth.check_password("a-new-password", reloaded) is True
    assert admin_auth.check_password("1", reloaded) is False
    change = sqlite3.connect(settings.root / "data/app.db").execute(
        "SELECT old_value,new_value,client FROM app_setting_changes WHERE key='admin.password_hash'").fetchone()
    assert "pbkdf2" not in "".join(change) and "a-new-password" not in "".join(change)
    assert change[2] == "tester"


def test_two_hashes_of_the_same_password_differ(settings):
    assert admin_auth.hash_password("same-password") != admin_auth.hash_password("same-password")


def test_password_length_limits(settings):
    for bad in ("", "abc", "x" * 129, 5):
        with pytest.raises(ValueError):
            admin_auth.set_password(bad, "tester", settings)


def test_lockout_after_five_failures_for_ten_minutes(settings):
    for _ in range(5):
        assert admin_auth.attempt_login("wrong", settings, NOW) == "bad"
    assert admin_auth.attempt_login("1", settings, NOW + 1) == "locked"
    assert admin_auth.attempt_login("1", settings, NOW + 599) == "locked"
    assert admin_auth.attempt_login("1", settings, NOW + 601) == "ok"


def test_success_resets_failure_count(settings):
    for _ in range(4):
        assert admin_auth.attempt_login("wrong", settings, NOW) == "bad"
    assert admin_auth.attempt_login("1", settings, NOW) == "ok"
    for _ in range(4):
        assert admin_auth.attempt_login("wrong", settings, NOW) == "bad"
    assert admin_auth.attempt_login("1", settings, NOW) == "ok"


def test_stored_password_text_is_not_logged(settings, caplog):
    caplog.set_level("DEBUG")
    admin_auth.set_password("log-me-never", "tester", settings)
    admin_auth.attempt_login("log-me-never", build_settings(settings.root), NOW)
    admin_auth.attempt_login("also-secret", build_settings(settings.root), NOW)
    assert "log-me-never" not in caplog.text and "also-secret" not in caplog.text and "pbkdf2" not in caplog.text


def test_session_key_file_is_git_ignored():
    """저장소에 올라가면 누구나 관리자 쿠키를 만들 수 있으므로 `.gitignore` 가 막아야 한다 (REQ-WEB-008)."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git 이 없습니다")
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("git 저장소가 아닙니다")
    result = subprocess.run(["git", "check-ignore", "-q", "data/admin_session.key"], cwd=ROOT)
    assert result.returncode == 0


def test_abnormal_rounds_hash_is_refused_without_running_pbkdf2(settings, monkeypatch):
    forged = "pbkdf2_sha256$999999999$" + "ab" * 16 + "$" + "cd" * 32
    monkeypatch.setattr(type(settings), "get", lambda self, key, default=None: forged if key == "admin.password_hash" else default)
    calls = []
    monkeypatch.setattr(admin_auth.hashlib, "pbkdf2_hmac", lambda *a, **k: calls.append(a) or b"")
    assert admin_auth.check_password("1", settings) is False
    assert calls == []
