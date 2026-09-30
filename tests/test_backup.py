import sqlite3

from app.core import config
from scripts.backup_data import create_backup, verify_backup


def test_backup_round_trip_verifies_sqlite_and_manifest(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.yaml").write_text("storage:\n  upload_dir: data/uploads\n  specification_dir: data/specifications\n  testcase_dir: data/testcases\n  index_dir: data/indexes\n  report_dir: output/reports\n  export_dir: output/exports\n  generated_tc_dir: output/generated_tc\n  log_dir: output/logs\n  manual_revision_dir: data/manual_revisions\n  manual_review_comment_dir: output/comments\n", encoding="utf-8")
    settings = config.build_settings(root)
    monkeypatch.setattr("scripts.backup_data.get_settings", lambda: settings)
    with sqlite3.connect(root / "data" / "app.db") as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.execute("INSERT INTO sample VALUES ('ok')")
    archive = create_backup(tmp_path / "backups")
    assert verify_backup(archive)["status"] == "ok"


# --- 백업 범위와 오래된 파일 정리 (OPEN_QUESTIONS 8-15, 8-16) -------------------------------

import zipfile  # noqa: E402
from contextlib import closing  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

_CONFIG = (
    "storage:\n  upload_dir: data/uploads\n  specification_dir: data/specifications\n  testcase_dir: data/testcases\n"
    "  index_dir: data/indexes\n  manual_revision_dir: data/manual_revisions\n"
)


def _project(tmp_path, extra_config: str = ""):
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.yaml").write_text(_CONFIG + extra_config, encoding="utf-8")
    (root / "data").mkdir()
    with closing(sqlite3.connect(root / "data" / "app.db")) as db:
        db.execute("CREATE TABLE sample(value TEXT)")
        db.commit()
    return root, config.build_settings(root)


def test_backup_includes_daily_qa_snapshots_but_not_knowledge_copy(monkeypatch, tmp_path):
    # Validates: REQ-OPS-002
    root, settings = _project(tmp_path)
    monkeypatch.setattr("scripts.backup_data.get_settings", lambda: settings)
    snapshot = root / "data" / "daily_qa" / "snapshots" / "2026-09-29.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("{}", encoding="utf-8")
    (root / "data" / "daily_qa" / "run.lock").write_text("123", encoding="utf-8")
    knowledge_copy = root / "data" / "product_knowledge" / "vxvue" / "original" / "specification" / "a.pdf"
    knowledge_copy.parent.mkdir(parents=True)
    knowledge_copy.write_bytes(b"pdf")

    archive = create_backup(tmp_path / "backups")

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert "data/daily_qa/snapshots/2026-09-29.json" in names
    assert "data/daily_qa/run.lock" not in names
    assert not any(name.startswith("data/product_knowledge/") for name in names)
    assert verify_backup(archive)["status"] == "ok"


def test_old_backups_are_pruned_but_the_newest_is_kept(tmp_path):
    # Validates: REQ-OPS-002
    from app.core.retention import prune_backups

    now = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)
    folder = tmp_path / "backups"
    folder.mkdir()
    old = folder / "qa-backup-20260801T021500Z.zip"
    recent = folder / "qa-backup-20260925T021500Z.zip"
    other = folder / "notes.zip"
    for path in (old, recent, other):
        path.write_bytes(b"x")

    removed = prune_backups(folder, keep_days=30, now=now)

    assert removed == [old]
    assert recent.exists() and other.exists()
    # 모두 오래됐어도 가장 새 백업 하나는 남긴다.
    assert prune_backups(folder, keep_days=1, now=now + timedelta(days=400)) == []
    assert recent.exists()
    # 0 이면 지우지 않는다.
    assert prune_backups(folder, keep_days=0, now=now + timedelta(days=400)) == []


def test_old_daily_qa_run_folders_are_pruned(tmp_path):
    # Validates: NFR-DAILY-002
    from app.core.retention import prune_run_folders

    now = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)
    base = tmp_path / "output" / "daily_qa"
    old = base / "20250101-073000"
    recent = base / "20260928-073000"
    unrelated = base / "keep-me"
    for folder in (old, recent, unrelated):
        (folder / "sub").mkdir(parents=True)
        (folder / "sub" / "f.json").write_text("{}", encoding="utf-8")

    removed = prune_run_folders(base, keep_days=365, now=now)

    assert removed == [old]
    assert not old.exists() and recent.exists() and unrelated.exists()
    assert prune_run_folders(tmp_path / "missing", keep_days=1, now=now) == []


def test_old_ai_cache_rows_are_pruned(tmp_path):
    # Validates: REQ-AICALL-003
    from app.core.retention import prune_ai_cache
    from app.core.storage import Storage

    storage = Storage(tmp_path / "app.db")
    storage.cache_set("old", {"a": 1})
    storage.cache_set("new", {"a": 2})
    with storage.connect() as db:
        db.execute("UPDATE ai_cache SET created_at=? WHERE cache_key='old'", ("2025-01-01T00:00:00+00:00",))

    assert prune_ai_cache(storage, keep_days=180) == 1
    assert storage.cache_get("old") is None
    assert storage.cache_get("new") == {"a": 2}
    assert prune_ai_cache(storage, keep_days=0) == 0


def test_retention_days_come_from_config(tmp_path):
    # Validates: REQ-CONF-001
    from app.core.retention import retention_days

    root, settings = _project(tmp_path, "retention:\n  backup_days: 7\n  ai_cache_days: 0\n")
    days = retention_days(settings)
    assert days["backup_days"] == 7
    assert days["ai_cache_days"] == 0
    assert days["daily_qa_run_days"] == 365
    assert days["daily_qa_workspace_run_days"] == 30


def test_real_config_declares_retention_and_drops_unused_keys():
    # Validates: REQ-CONF-001
    real = config.build_settings()
    assert real.get("retention.backup_days") == 30
    assert real.get("retention.ai_cache_days") == 180
    assert real.get("storage.database_url") is None
    assert real.get("app.locale") is None
    for key in ("max_retries", "retry_min_seconds", "retry_max_seconds"):
        assert real.get(f"analysis.{key}") is not None


def test_backup_cli_prunes_after_verifying(monkeypatch, tmp_path):
    # Validates: REQ-OPS-002
    import sys

    import scripts.backup_data as backup_cli

    root, settings = _project(tmp_path, "retention:\n  backup_days: 30\n  ai_cache_days: 0\n  daily_qa_run_days: 0\n  daily_qa_workspace_run_days: 0\n")
    monkeypatch.setattr("scripts.backup_data.get_settings", lambda: settings)
    folder = tmp_path / "backups"
    folder.mkdir()
    ancient = folder / "qa-backup-20000101T000000Z.zip"
    ancient.write_bytes(b"x")
    monkeypatch.setattr(sys, "argv", ["backup_data.py", "--destination", str(folder)])
    assert backup_cli.main() == 0
    assert not ancient.exists()
    assert len(list(folder.glob("qa-backup-*.zip"))) == 1

    ancient.write_bytes(b"x")
    monkeypatch.setattr(sys, "argv", ["backup_data.py", "--destination", str(folder), "--no-prune"])
    assert backup_cli.main() == 0
    assert ancient.exists()
