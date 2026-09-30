from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core.config import get_settings


def create_backup(destination: Path) -> Path:
    settings = get_settings()
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = destination / f"qa-backup-{timestamp}.zip"
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        db_copy = temp / "app.db"
        with closing(sqlite3.connect(settings.root / "data" / "app.db")) as source, closing(sqlite3.connect(db_copy)) as target:
            source.backup(target)
        entries = [db_copy]
        for dotted in ("storage.upload_dir", "storage.specification_dir", "storage.testcase_dir", "storage.index_dir", "storage.manual_revision_dir"):
            root = settings.path(dotted)
            if root.exists():
                entries.extend(path for path in root.rglob("*") if path.is_file())
        # 일일 QA 스냅샷은 전날·지난주 비교 기준이라 다시 만들 수 없어 넣는다(OPEN_QUESTIONS 8-15).
        # 서버 지식 사본(data/product_knowledge/)은 넣지 않는다. 원본이 PC 지식 폴더에 있어 다시 올리면 된다.
        snapshots = settings.root / "data" / "daily_qa" / "snapshots"
        if snapshots.exists():
            entries.extend(path for path in snapshots.rglob("*") if path.is_file())
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in entries:
                arcname = "data/app.db" if path == db_copy else path.relative_to(settings.root).as_posix()
                archive.write(path, arcname)
                manifest["files"][arcname] = hashlib.sha256(path.read_bytes()).hexdigest()
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return archive_path


def verify_backup(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as temp_name:
        manifest = json.loads(archive.read("manifest.json"))
        for name, expected in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError(f"checksum mismatch: {name}")
        archive.extract("data/app.db", temp_name)
        with closing(sqlite3.connect(Path(temp_name) / "data" / "app.db")) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        return {"status": "ok", "files": len(manifest["files"]), "created_at": manifest["created_at"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=Path("backups"))
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--no-prune", action="store_true", help="보관 일수를 넘은 백업·실행 폴더·AI 응답 저장본을 지우지 않는다")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_backup(args.verify), ensure_ascii=False))
    else:
        path = create_backup(args.destination)
        print(path)
        print(json.dumps(verify_backup(path), ensure_ascii=False))
        # 새 백업을 검증한 뒤에만 오래된 것을 지운다. 검증이 실패하면 위에서 예외로 끝나 아무것도 지우지 않는다.
        if not args.no_prune:
            from app.core.retention import run_retention

            pruned = run_retention(get_settings(), args.destination)
            print(json.dumps({"pruned": pruned}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
