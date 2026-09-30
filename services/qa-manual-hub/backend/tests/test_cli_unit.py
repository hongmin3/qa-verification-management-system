"""Admin CLI checks that run without PostgreSQL.

``app.cli`` opens its own ``SessionLocal()``; these tests swap it for a small
stand-in so the command logic can be checked without a database.  The
PostgreSQL-backed counterparts are in test_cli.py.
"""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def clean_tables():
    """No database here: replace the conftest fixture of the same name."""
    yield


class _Rows(list):
    def unique(self):
        return self

    def all(self):
        return list(self)


class _FakeDb:
    def __init__(self, versions=()):
        self.added: list = []
        self.versions = _Rows(versions)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scalar(self, *_args, **_kwargs):
        return None  # no existing admin, category or product

    def scalars(self, *_args, **_kwargs):
        return self.versions

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass


# Validates: REQ-HUBOPS-004
def test_seed_catalog_writes_create_audit_rows(app_settings, monkeypatch):
    from app import audit, cli
    from app.models import AuditLog, DocumentCategory, Product

    fake = _FakeDb()
    monkeypatch.setattr(cli, "SessionLocal", lambda: fake)

    assert cli.main(["seed-catalog", "--product", "Bellalun Viewer"]) == 0

    categories = [o for o in fake.added if isinstance(o, DocumentCategory)]
    products = [o for o in fake.added if isinstance(o, Product)]
    logs = [o for o in fake.added if isinstance(o, AuditLog)]
    assert len(categories) == len(cli.DEFAULT_CATEGORIES)
    assert len(products) == 1

    category_logs = [l for l in logs if l.action == audit.CATEGORY_CREATE]
    product_logs = [l for l in logs if l.action == audit.PRODUCT_CREATE]
    assert sorted(l.target_label for l in category_logs) == sorted(
        name for name, _, _ in cli.DEFAULT_CATEGORIES
    )
    assert [l.product_name for l in product_logs] == ["Bellalun Viewer"]
    assert {l.actor_login_id for l in logs} == {"cli"}
    assert all("seed-catalog" in (l.detail or "") for l in logs)


def _version(storage_root, name: str, content: bytes, *, recorded: bytes | None = None):
    key = f"{uuid.uuid4()}/{name}"
    path = storage_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    expected = recorded if recorded is not None else content
    return SimpleNamespace(
        label=name,
        stored_file=SimpleNamespace(
            storage_key=key,
            byte_size=len(expected),
            sha256=hashlib.sha256(expected).hexdigest(),
        ),
    )


# Validates: REQ-HUBOPS-006
def test_check_storage_verify_sha256_finds_same_size_corruption(
    app_settings, monkeypatch, tmp_path, capsys
):
    from app import cli
    from app.storage import LocalDiskStorage

    good = _version(tmp_path, "good.pdf", b"%PDF-1.7 good")
    # Same length as what was recorded, different bytes: size check cannot see it.
    bad = _version(tmp_path, "bad.pdf", b"%PDF-1.7 BAD!", recorded=b"%PDF-1.7 bad!")
    monkeypatch.setattr(cli, "SessionLocal", lambda: _FakeDb([good, bad]))
    monkeypatch.setattr(cli, "get_storage", lambda: LocalDiskStorage(tmp_path))

    # Without the option the old, cheap check runs and passes.
    assert cli.main(["check-storage"]) == 0
    plain = capsys.readouterr().out
    assert "크기 불일치: 0" in plain
    assert "SHA-256" not in plain

    assert cli.main(["check-storage", "--verify-sha256"]) == 2
    out = capsys.readouterr().out
    assert "SHA-256 불일치: 1" in out
    assert "bad.pdf" in out
    assert "good.pdf" not in out.split("SHA-256 불일치")[1]
