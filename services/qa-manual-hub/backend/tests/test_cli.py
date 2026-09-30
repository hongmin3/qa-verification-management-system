"""Admin CLI against the real PostgreSQL test database."""
from __future__ import annotations


# Validates: REQ-HUBOPS-004
def test_seed_catalog_leaves_creation_rows_in_audit_logs(engine, make_user):
    from sqlalchemy import select

    from app import audit, cli
    from app.db import SessionLocal
    from app.models import AuditLog

    make_user("admin", "QA Admin", admin=True)
    assert cli.main(["seed-catalog", "--product", "Bellalun Viewer"]) == 0
    # A second run creates nothing, so it must not add rows either.
    assert cli.main(["seed-catalog", "--product", "Bellalun Viewer"]) == 0

    with SessionLocal() as db:
        rows = db.scalars(
            select(AuditLog).where(
                AuditLog.action.in_([audit.CATEGORY_CREATE, audit.PRODUCT_CREATE])
            )
        ).all()
    categories = [r for r in rows if r.action == audit.CATEGORY_CREATE]
    products = [r for r in rows if r.action == audit.PRODUCT_CREATE]
    assert len(categories) == len(cli.DEFAULT_CATEGORIES)
    assert [p.product_name for p in products] == ["Bellalun Viewer"]
    assert products[0].product_id is not None
    assert {r.actor_login_id for r in rows} == {"cli"}


# Validates: REQ-HUBOPS-006
def test_check_storage_verify_sha256_flags_a_rewritten_file(
    admin_client, catalog, capsys
):
    from app import cli

    document = admin_client.post(
        "/api/documents",
        json={
            "product_id": catalog["product"]["id"],
            "category_id": catalog["category"]["id"],
            "name": "Operation Manual",
        },
    ).json()
    uploaded = admin_client.post(
        f"/api/documents/{document['id']}/versions",
        files={"file": ("m.pdf", b"%PDF-1.7\nabc\n", "application/pdf")},
        data={"version": "V1.0"},
    )
    assert uploaded.status_code == 201, uploaded.text

    assert cli.main(["check-storage", "--verify-sha256"]) == 0
    capsys.readouterr()

    # Flip bytes without changing the length.  The storage tree is shared by
    # the whole session, so find this upload's file through its DB row.
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import StoredFile
    from app.storage import get_storage

    with SessionLocal() as db:
        [key] = db.scalars(select(StoredFile.storage_key)).all()
    get_storage().path(key).write_bytes(b"%PDF-1.7\nxyz\n")

    assert cli.main(["check-storage"]) == 0
    assert cli.main(["check-storage", "--verify-sha256"]) == 2
    assert "SHA-256 불일치: 1" in capsys.readouterr().out
