"""Deploy scripts and Docker Compose files, checked without a server.

The shell helpers in ``deploy/scripts/common.sh`` are called one at a time
through bash, with stub commands (``pg_dump``) on PATH.  The wiring checks
read backup.sh / restore.sh / install.sh / deploy.sh as text to make sure each
script actually uses those helpers in the right order.  No PostgreSQL, no
root, no systemd is needed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = SERVICE_ROOT / "deploy"
SCRIPTS = DEPLOY / "scripts"
COMMON = SCRIPTS / "common.sh"

BASH = os.environ.get("QAMH_TEST_BASH") or shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not available")


@pytest.fixture(autouse=True)
def clean_tables():
    """No database here: replace the conftest fixture of the same name."""
    yield


def _p(path: Path | str) -> str:
    """Path as bash sees it (``C:\\x`` -> ``/c/x`` under Git Bash on Windows;
    GNU tar would otherwise read ``C:`` as a remote host name)."""
    text = str(path)
    if os.name == "nt" and re.match(r"^[A-Za-z]:[\\/]", text):
        text = "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text


def _run(func: str, *args: str, stub_bin: Path | None = None, check: bool = True):
    env = dict(os.environ)
    if stub_bin is not None:
        env["PATH"] = str(stub_bin) + os.pathsep + env.get("PATH", "")
    # DB_* come from a dummy URL; the stub pg_dump ignores them.
    script = (
        'set -Eeuo pipefail; source "$1"; shift; '
        'qamh_parse_db_url "postgresql+psycopg://u:p@127.0.0.1:5432/qa_manual_hub"; "$@"'
    )
    result = subprocess.run(
        [BASH, "-c", script, "_", _p(COMMON), func, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"{func} exited {result.returncode}\nstdout:{result.stdout}\nstderr:{result.stderr}"
        )
    return result


def _stub(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _pg_dump_ok(bin_dir: Path) -> None:
    # Writes a fake dump to the --file= argument, like the real one.
    _stub(
        bin_dir,
        "pg_dump",
        'for a in "$@"; do case "$a" in --file=*) printf "DUMP" > "${a#--file=}";; esac; done',
    )


def _make_storage(root: Path) -> None:
    (root / "p1" / "d1").mkdir(parents=True)
    (root / "p1" / "d1" / "a.pdf").write_bytes(b"%PDF-1.7 a")
    (root / "p1" / "d1" / "b.pdf").write_bytes(b"%PDF-1.7 b")


def _backup_dir(tmp_path: Path, storage_root: Path) -> Path:
    stub = tmp_path / "bin"
    _pg_dump_ok(stub)
    dest = tmp_path / "backup" / "20260929-023000"
    _run(
        "qamh_safety_backup", _p(dest), "qa_manual_hub", _p(storage_root), _p(tmp_path / "app"),
        stub_bin=stub,
    )
    return dest


# --------------------------------------------------------------------------- #
# shell syntax
# --------------------------------------------------------------------------- #
@needs_bash
@pytest.mark.parametrize(
    "name", ["common.sh", "backup.sh", "restore.sh", "install.sh", "deploy.sh", "qamh"]
)
def test_scripts_parse(name):
    result = subprocess.run([BASH, "-n", _p(SCRIPTS / name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# REQ-HUBOPS-010: manifest check and safety backup
# --------------------------------------------------------------------------- #
# Validates: REQ-HUBOPS-010
@needs_bash
def test_manifest_of_a_fresh_backup_verifies(tmp_path):
    storage = tmp_path / "srv" / "storage"
    _make_storage(storage)
    dest = _backup_dir(tmp_path, storage)
    manifest = (dest / "manifest.txt").read_text(encoding="utf-8")
    assert re.search(r"^[0-9a-f]{64} [ *]\./database\.dump$", manifest, re.M)
    assert re.search(r"^[0-9a-f]{64} [ *]\./storage\.tar\.gz$", manifest, re.M)
    assert "storage_file_count=2" in manifest
    assert _run("qamh_verify_manifest", _p(dest), check=False).returncode == 0


# Validates: REQ-HUBOPS-010
@needs_bash
def test_manifest_rejects_a_storage_archive_from_another_backup(tmp_path):
    storage = tmp_path / "srv" / "storage"
    _make_storage(storage)
    dest = _backup_dir(tmp_path, storage)
    # Same name, different bytes: what a mixed-up copy looks like.
    (storage / "p1" / "d1" / "c.pdf").write_bytes(b"%PDF-1.7 c")
    other = tmp_path / "other"
    other.mkdir()
    _run("qamh_archive_storage", _p(storage), _p(other / "storage.tar.gz"))
    shutil.copyfile(other / "storage.tar.gz", dest / "storage.tar.gz")

    result = _run("qamh_verify_manifest", _p(dest), check=False)
    assert result.returncode != 0
    assert "SHA-256" in result.stdout


# Validates: REQ-HUBOPS-010
@needs_bash
def test_manifest_missing_or_incomplete_is_refused(tmp_path):
    storage = tmp_path / "srv" / "storage"
    _make_storage(storage)
    dest = _backup_dir(tmp_path, storage)

    lines = (dest / "manifest.txt").read_text(encoding="utf-8").splitlines()
    (dest / "manifest.txt").write_text(
        "\n".join(l for l in lines if "storage.tar.gz" not in l) + "\n", encoding="utf-8"
    )
    unlisted = _run("qamh_verify_manifest", _p(dest), check=False)
    assert unlisted.returncode != 0
    assert "storage.tar.gz" in unlisted.stdout

    (dest / "manifest.txt").unlink()
    missing = _run("qamh_verify_manifest", _p(dest), check=False)
    assert missing.returncode != 0
    assert "manifest.txt" in missing.stdout


# Validates: REQ-HUBOPS-010
@needs_bash
def test_safety_backup_fails_when_the_database_dump_fails(tmp_path):
    stub = tmp_path / "bin"
    _stub(stub, "pg_dump", "exit 1")
    storage = tmp_path / "srv" / "storage"
    _make_storage(storage)
    result = _run(
        "qamh_safety_backup", _p(tmp_path / "pre"), "qa_manual_hub", _p(storage),
        _p(tmp_path / "app"), stub_bin=stub, check=False,
    )
    assert result.returncode != 0
    assert "안전 백업 실패" in result.stderr


# Validates: REQ-HUBOPS-010
def test_restore_stops_before_touching_data_when_a_check_fails():
    text = (SCRIPTS / "restore.sh").read_text(encoding="utf-8")
    assert "계속 진행합니다" not in text, "safety-backup failures must not be warnings"
    assert re.search(r"qamh_verify_manifest .*\|\|", text)
    safety = re.search(r"qamh_safety_backup .*\n?.*\|\| *die", text)
    assert safety, "restore.sh must die when the safety backup fails"
    stop = text.index('systemctl stop "$SERVICE"')
    drop = text.index("DROP SCHEMA")
    assert text.index("qamh_verify_manifest") < stop
    assert safety.start() < stop < drop
    assert "check-storage --verify-sha256" in text


# --------------------------------------------------------------------------- #
# REQ-HUBOPS-009 / 010: STORAGE_ROOT is followed
# --------------------------------------------------------------------------- #
# Validates: REQ-HUBOPS-009
@needs_bash
def test_storage_root_comes_from_env_with_data_root_default(tmp_path):
    env = tmp_path / ".env"
    env.write_text('DATABASE_URL=x\nSTORAGE_ROOT="/mnt/nas/qamh-docs/"\n', encoding="utf-8")
    assert _run("qamh_storage_root", _p(env), "/srv/qa-manual-hub").stdout == "/mnt/nas/qamh-docs"
    env.write_text("DATABASE_URL=x\n", encoding="utf-8")
    assert _run("qamh_storage_root", _p(env), "/srv/qa-manual-hub").stdout == "/srv/qa-manual-hub/storage"


# Validates: REQ-HUBOPS-010
@needs_bash
def test_swap_storage_restores_into_a_custom_storage_root(tmp_path):
    source = tmp_path / "old-host" / "docs"
    _make_storage(source)
    tarball = tmp_path / "storage.tar.gz"
    _run("qamh_archive_storage", _p(source), _p(tarball))

    live = tmp_path / "nas" / "manuals"
    live.mkdir(parents=True)
    (live / "current.pdf").write_bytes(b"live")

    result = _run("qamh_swap_storage", _p(tarball), _p(live), "20260929-100000")
    assert (live / "p1" / "d1" / "a.pdf").read_bytes() == b"%PDF-1.7 a"
    replaced = tmp_path / "nas" / "manuals.replaced-20260929-100000"
    assert (replaced / "current.pdf").read_bytes() == b"live"
    assert result.stdout.strip().endswith("manuals.replaced-20260929-100000")
    assert not list((tmp_path / "nas").glob(".restore-*"))


# Validates: REQ-HUBOPS-009
def test_backup_and_restore_use_the_configured_storage_root():
    for name in ("backup.sh", "restore.sh"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "common.sh" in text
        assert "qamh_storage_root" in text, name
        assert '-C "$DATA_ROOT" storage' not in text, name
    deploy = (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
    assert "deploy/scripts/common.sh" in deploy


# --------------------------------------------------------------------------- #
# REQ-HUBOPS-009: cron entry
# --------------------------------------------------------------------------- #
# Validates: REQ-HUBOPS-009
@needs_bash
def test_backup_cron_is_created_once_and_never_overwritten(tmp_path):
    cron = tmp_path / "qa-manual-hub-backup"
    first = _run("qamh_write_backup_cron", _p(cron), "ubuntu", "/opt/qa-manual-hub", "/srv/qa-manual-hub")
    assert first.stdout.strip() == "created"
    text = cron.read_text(encoding="utf-8")
    assert re.search(
        r"^30 2 \* \* \*\s+ubuntu\s+/opt/qa-manual-hub/scripts/backup\.sh >> /opt/qa-manual-hub/logs/backup\.log 2>&1$",
        text,
        re.M,
    )
    assert "DATA_ROOT=/srv/qa-manual-hub" in text

    cron.write_text("# edited by hand\n", encoding="utf-8")
    second = _run("qamh_write_backup_cron", _p(cron), "ubuntu", "/opt/x", "/srv/x")
    assert second.stdout.strip() == "exists"
    assert cron.read_text(encoding="utf-8") == "# edited by hand\n"


# Validates: REQ-HUBOPS-009
def test_install_creates_the_backup_cron():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "qamh_write_backup_cron" in text
    assert "/etc/cron.d/qa-manual-hub-backup" in text


# --------------------------------------------------------------------------- #
# REQ-HUBOPS-001: existing DB role password
# --------------------------------------------------------------------------- #
# Validates: REQ-HUBOPS-001
@needs_bash
@pytest.mark.parametrize(
    ("role_exists", "env_has_password", "reset", "plan"),
    [
        ("0", "0", "0", "create"),
        ("0", "1", "0", "create"),
        ("1", "1", "0", "keep"),
        ("1", "0", "0", "refuse"),
        ("1", "0", "1", "reset"),
        ("1", "1", "1", "reset"),
    ],
)
def test_role_password_plan(role_exists, env_has_password, reset, plan):
    assert _run("qamh_role_password_plan", role_exists, env_has_password, reset).stdout.strip() == plan


# Validates: REQ-HUBOPS-001
def test_install_only_alters_an_existing_role_when_asked():
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    assert "qamh_role_password_plan" in text
    assert "RESET_DB_PASSWORD" in text
    # The only ALTER ROLE left is the one inside the explicit reset branch.
    assert text.count("ALTER ROLE") == 1
    reset_branch = text[text.index("reset)") : text.index(";;", text.index("reset)"))]
    assert "ALTER ROLE" in reset_branch


# --------------------------------------------------------------------------- #
# REQ-HUBOPS-012: Docker Compose points at files that exist
# --------------------------------------------------------------------------- #
# Validates: REQ-HUBOPS-012
def test_compose_references_only_existing_files():
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    context = re.search(r"context:\s*(\S+)", compose).group(1)
    dockerfile = re.search(r"dockerfile:\s*(\S+)", compose).group(1)
    assert (DEPLOY / context / dockerfile).is_file(), dockerfile
    for mount in re.findall(r"-\s*(\./[^:\s]+):", compose):
        assert (DEPLOY / mount).is_file(), mount


# Validates: REQ-HUBOPS-012
def test_compose_uses_the_same_password_minimum_as_the_app(app_settings):
    from app.config import Settings

    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    default = re.search(r"PASSWORD_MIN_LENGTH:\s*\$\{PASSWORD_MIN_LENGTH:-(\d+)\}", compose)
    assert default, "PASSWORD_MIN_LENGTH missing from compose"
    assert int(default.group(1)) == Settings.model_fields["password_min_length"].default


# Validates: REQ-HUBOPS-012
def test_docker_nginx_passes_the_real_client_address():
    conf = (DEPLOY / "nginx" / "docker.conf").read_text(encoding="utf-8")
    assert "proxy_set_header X-Real-IP $remote_addr;" in conf
    assert "location /api/" in conf


# Validates: REQ-HUBOPS-009
@needs_bash
def test_backup_script_archives_the_configured_storage_root_end_to_end(tmp_path):
    """Run backup.sh itself (stub pg_dump) with STORAGE_ROOT outside DATA_ROOT."""
    stub = tmp_path / "bin"
    _pg_dump_ok(stub)
    storage = tmp_path / "nas" / "manuals"
    _make_storage(storage)
    data_root = tmp_path / "srv"
    data_root.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://u:p@127.0.0.1:5432/qa_manual_hub\n"
        f'STORAGE_ROOT="{_p(storage)}"\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = str(stub) + os.pathsep + env.get("PATH", "")
    env.update(
        APP_ROOT=_p(tmp_path / "app"), DATA_ROOT=_p(data_root), ENV_FILE=_p(env_file)
    )
    result = subprocess.run(
        [BASH, _p(SCRIPTS / "backup.sh")], capture_output=True, text=True,
        encoding="utf-8", env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (dest,) = [p for p in (data_root / "backup").iterdir() if p.is_dir()]
    manifest = (dest / "manifest.txt").read_text(encoding="utf-8")
    assert f"storage_root={_p(storage)}" in manifest
    assert "storage_file_count=2" in manifest
    assert _run("qamh_verify_manifest", _p(dest), check=False).returncode == 0
    listing = subprocess.run(
        [BASH, "-c", 'tar -tzf "$1"', "_", _p(dest / "storage.tar.gz")],
        capture_output=True, text=True,
    )
    assert "manuals/p1/d1/a.pdf" in listing.stdout
