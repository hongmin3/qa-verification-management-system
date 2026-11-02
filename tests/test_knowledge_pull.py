"""서버 화면 등록본을 지식 폴더로 내려받기와 업로드 때의 시각 비교 (TEST-SYNC-004).

Validates: REQ-SYNC-004, REQ-KNOW-012

서버는 실제 라우터를 TestClient 로 띄우고 DB·업로드 폴더는 tmp 에 둔다. 지식 폴더와 보관 폴더도 tmp 다.
시각은 테스트가 정한 값만 쓴다(파일 수정 시각은 os.utime, 등록 시각은 DB 에 직접 쓴다).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.modules.knowledge.router as knowledge_router
from app.core import knowledge_push
from app.core.knowledge_upload import commit, store_asset
from app.core.product_knowledge import parse_asset_name
from app.core.storage import Storage
from app.main import app

PRODUCT = "VXvue"
TARGET = "http://testserver"
REGISTERED = "2026-10-30T00:00:00+00:00"
BEFORE = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
AFTER = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()
STAMP = "20261103-100000"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "server" / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    folder = tmp_path / "지식 폴더"
    folder.mkdir()
    config = SimpleNamespace(product=PRODUCT, knowledge_source=SimpleNamespace(dir=str(folder), ignore=[], extensions=None, classify=None))
    return SimpleNamespace(root=tmp_path, storage=storage, folder=folder, backup=tmp_path / "replaced", config=config,
                           client=TestClient(app))


def _server_document(env, name: str, body: bytes, *, kind: str = "manual", source: str = "upload",
                     registered: str = REGISTERED, sha256: str | None = None) -> int:
    path = env.root / "server" / "uploads" / f"{len(list((env.root / 'server').glob('uploads/*')))}-{abs(hash(name))}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    env.storage.add_document(kind=kind, product=PRODUCT, version="1.0", revision="", name=name, path=path,
                             metadata={"source": source, "sha256": sha256 or _sha(body)})
    document_id = max(document["id"] for document in env.storage.active_documents(kind, PRODUCT))
    with sqlite3.connect(env.storage.db_path) as connection:
        connection.execute("UPDATE documents SET created_at=? WHERE id=?", (registered, document_id))
    return document_id


def _local(env, name: str, body: bytes, mtime: float = BEFORE) -> Path:
    path = env.folder / name
    path.write_bytes(body)
    os.utime(path, (mtime, mtime))
    return path


def _pull(env) -> dict:
    return knowledge_push.pull_person_documents(env.config, env.client, TARGET, backup_root=env.backup, stamp=STAMP)


def test_registered_list_has_only_documents_people_put_on_the_screen(env):
    """1. 사람이 화면에서 올린 등록만, sha256 과 등록 시각과 함께 돌려준다."""
    person = _server_document(env, "(매뉴얼) Person Guide_KO.txt", b"person")
    _server_document(env, "(매뉴얼) Folder Guide_KO.txt", b"folder", source="product_knowledge")

    response = env.client.get("/knowledge/product-knowledge/registered", params={"product": PRODUCT})

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert [document["id"] for document in documents] == [person]
    assert documents[0] == {"id": person, "kind": "manual", "name": "(매뉴얼) Person Guide_KO.txt",
                            "sha256": _sha(b"person"), "registered_at": REGISTERED}


def test_newer_revision_on_the_screen_is_downloaded_and_the_old_file_is_kept_aside(env):
    """2. 새 리비전을 내려받고 옛 리비전은 보관 폴더로 옮긴다."""
    old = _local(env, "(매뉴얼) VXvue Service Manual.V1.1.0W7_KO.txt", b"w7")
    _server_document(env, "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt", b"w8")

    result = _pull(env)

    new = env.folder / "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt"
    assert new.read_bytes() == b"w8"
    assert not old.exists()
    assert (env.backup / STAMP / old.name).read_bytes() == b"w7"
    assert result["downloaded"] == [new.name] and result["moved"] == [old.name]
    assert result["status"] == "SUCCESS"


def test_same_name_without_revision_follows_the_later_change(env):
    """3. 리비전이 없으면 화면 등록보다 먼저 바뀐 폴더 파일만 바꾼다."""
    name = "(매뉴얼) VXvue 회귀 가이드_KO.txt"
    _server_document(env, name, b"screen")
    _local(env, name, b"older folder", mtime=BEFORE)

    result = _pull(env)

    assert (env.folder / name).read_bytes() == b"screen"
    assert (env.backup / STAMP / name).read_bytes() == b"older folder"
    assert result["downloaded"] == [name]


def test_folder_file_changed_after_the_screen_upload_stays(env):
    name = "(매뉴얼) VXvue 회귀 가이드_KO.txt"
    _server_document(env, name, b"screen")
    _local(env, name, b"edited later in the folder", mtime=AFTER)

    result = _pull(env)

    assert (env.folder / name).read_bytes() == b"edited later in the folder"
    assert result["downloaded"] == [] and result["local_newer"] == [name]
    assert not env.backup.exists()


def test_nothing_changes_when_the_folder_already_has_it_or_something_newer(env):
    """4. 같은 내용이 있거나 폴더 리비전이 더 최신이면 아무것도 바꾸지 않는다."""
    _local(env, "(매뉴얼) Same Guide_KO-copy.txt", b"same bytes")
    _server_document(env, "(매뉴얼) Same Guide_KO.txt", b"same bytes")
    _local(env, "(매뉴얼) VXvue Service Manual.V1.1.0W9_KO.txt", b"w9")
    _server_document(env, "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt", b"w8")

    result = _pull(env)

    assert sorted(path.name for path in env.folder.iterdir()) == [
        "(매뉴얼) Same Guide_KO-copy.txt", "(매뉴얼) VXvue Service Manual.V1.1.0W9_KO.txt"]
    assert result["downloaded"] == [] and result["local_newer"] == ["(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt"]
    assert not env.backup.exists()


def test_damaged_download_leaves_the_folder_as_it_was(env):
    """5. 받은 내용이 목록의 sha256 과 다르면 임시 파일도 남기지 않고 기존 파일을 지킨다."""
    old = _local(env, "(매뉴얼) VXvue Service Manual.V1.1.0W7_KO.txt", b"w7")
    _server_document(env, "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt", b"w8", sha256="0" * 64)

    result = _pull(env)

    assert [path.name for path in env.folder.iterdir()] == [old.name]
    assert old.read_bytes() == b"w7"
    assert result["status"] == "PARTIAL" and result["failed"]


def test_server_name_with_a_path_is_not_downloaded(env):
    """7. 경로 조각이 든 이름은 파일 이름으로 쓰지 않는다 (NFR-SEC-003)."""
    _server_document(env, "../(매뉴얼) Escape Guide_KO.txt", b"escape")

    result = _pull(env)

    assert list(env.folder.iterdir()) == []
    assert not (env.root / "(매뉴얼) Escape Guide_KO.txt").exists()
    assert result["status"] == "PARTIAL" and result["failed"]


# --- 업로드 경로에서 실제로 부르는지 (REQ-SYNC-002 0번 단계) ------------------------


def _push_stubs(monkeypatch, env, client_factory):
    monkeypatch.setattr(knowledge_push, "resolve_config", lambda product: env.config)
    monkeypatch.setattr(knowledge_push, "source_available", lambda config: True)
    monkeypatch.setattr(knowledge_push, "sync_product", lambda config, dry_run=False: SimpleNamespace(status="SUCCESS", detail="수집 0건"))
    monkeypatch.setattr(knowledge_push, "load_manifest", lambda product: {"assets": []})
    monkeypatch.setattr(knowledge_push, "product_dir", lambda product: env.root / "pc-data" / "vxvue")
    monkeypatch.setattr(knowledge_push, "_client", client_factory)


def test_upload_downloads_screen_documents_first(env, monkeypatch):
    _server_document(env, "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt", b"w8")

    @contextmanager
    def client(timeout):
        yield env.client

    _push_stubs(monkeypatch, env, client)
    monkeypatch.setattr(knowledge_router, "server_state", lambda product: {"product": product, "assets": []})
    monkeypatch.setattr(knowledge_router, "commit_upload", lambda product, manifest, uploaded=None, storage=None: {"status": "SUCCESS", "detail": "확정"})

    result = knowledge_push.push_product(PRODUCT, TARGET)

    assert (env.folder / "(매뉴얼) VXvue Service Manual.V1.1.0W8_KO.txt").read_bytes() == b"w8"
    assert "서버 화면 등록본 내려받음 1건" in result["detail"]
    assert result["status"] == "SUCCESS"


def test_upload_goes_on_when_the_screen_list_cannot_be_read(env, monkeypatch):
    """6. 목록을 못 읽어도 업로드는 계속한다. 옛 서버(주소 없음)도 같다."""
    class OldServer:
        def get(self, url, params=None):
            if url.endswith("/registered"):
                raise ConnectionError("no such route")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"assets": []})

        def post(self, url, data=None, files=None):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"status": "SUCCESS", "detail": "확정"})

    @contextmanager
    def client(timeout):
        yield OldServer()

    _push_stubs(monkeypatch, env, client)

    result = knowledge_push.push_product(PRODUCT, TARGET)

    assert result["status"] == "SUCCESS"
    assert "서버 화면 등록본 확인 실패" in result["detail"]


def test_dry_run_does_not_call_the_server(env, monkeypatch):
    def no_client(timeout):
        raise AssertionError("미리보기에서 서버를 불렀습니다")

    _push_stubs(monkeypatch, env, no_client)

    assert knowledge_push.push_product(PRODUCT, TARGET, dry_run=True)["status"] == "DRY_RUN"


# --- 업로드 확정 때 시각 비교 (REQ-KNOW-012) ---------------------------------------


@pytest.fixture
def server(tmp_path):
    return SimpleNamespace(root=tmp_path, storage=Storage(db_path=tmp_path / "app.db"))


def _commit_folder_copy(server, name: str, body: bytes, modified: str, registered: str = REGISTERED) -> dict:
    screen = server.root / "screen" / "screen.txt"
    screen.parent.mkdir(parents=True, exist_ok=True)
    screen.write_bytes(b"screen version")
    server.storage.add_document(kind="manual", product=PRODUCT, version="1.0", revision="", name=name, path=screen,
                                metadata={"source": "upload", "sha256": _sha(b"screen version")})
    with sqlite3.connect(server.storage.db_path) as connection:
        connection.execute("UPDATE documents SET created_at=?", (registered,))
    record = store_asset(PRODUCT, "manual", name, body, root=server.root)
    asset = {**parse_asset_name(name), "file_name": record["file_name"], "kind": "manual", "sha256": record["sha256"], "modified": modified}
    return commit(PRODUCT, {"assets": [asset]}, uploaded={record["file_name"]: record}, storage=server.storage, root=server.root)


def test_folder_change_after_the_screen_upload_replaces_the_screen_document(server):
    """8. 화면 등록 뒤 지식 폴더에서 바뀐 파일이 등록되고 화면 등록본은 빠진다."""
    name = "(매뉴얼) VXvue 회귀 가이드_KO.txt"
    result = _commit_folder_copy(server, name, b"edited in the folder", modified="2026-11-02T00:00:00+00:00")

    documents = server.storage.active_documents("manual", PRODUCT)
    assert len(documents) == 1
    assert "product_knowledge" in str(documents[0]["path"]).replace("\\", "/")
    assert "화면 등록 뒤 지식 폴더에서 바뀜 1건" in result["detail"]


def test_folder_file_older_than_the_screen_upload_is_still_skipped(server):
    name = "(매뉴얼) VXvue 회귀 가이드_KO.txt"
    result = _commit_folder_copy(server, name, b"old folder content", modified="2026-10-20T00:00:00+00:00")

    documents = server.storage.active_documents("manual", PRODUCT)
    assert [str(document["path"]).endswith("screen.txt") for document in documents] == [True]
    assert "화면 등록본 우선으로 건너뜀 1건" in result["detail"]


def test_upload_cli_lists_downloaded_and_moved_files(monkeypatch, capsys):
    """REQ-SYNC-004 결과: 내려받은 파일과 옮긴 파일이 CLI 에 한 줄씩 나온다."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("sync_product_knowledge_cli_pull", Path(__file__).resolve().parents[1] / "scripts" / "sync_product_knowledge.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    pulled = {"downloaded": ["(TC) New.xlsx"], "moved": ["(TC) Old.xlsx"], "local_newer": ["(TC) Kept.xlsx"], "failed": ["(TC) Bad.xlsx (sha256 불일치)"]}
    monkeypatch.setattr(knowledge_push, "push_product", lambda product, url, dry_run=False: {"status": "PARTIAL", "detail": "d", "pulled": pulled})
    monkeypatch.setattr(sys, "argv", ["sync_product_knowledge.py", "--product", PRODUCT, "--upload-to", "http://server"])

    cli.main()

    out = capsys.readouterr().out
    assert "  지식 폴더로 내려받음: (TC) New.xlsx" in out
    assert "  보관 폴더로 옮김: (TC) Old.xlsx" in out
    assert "  지식 폴더가 더 최신: (TC) Kept.xlsx" in out
    assert "  내려받기 실패: (TC) Bad.xlsx (sha256 불일치)" in out
