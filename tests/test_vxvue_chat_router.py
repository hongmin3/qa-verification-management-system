"""Validates: REQ-VXVUECHATBOT-001/002/003/004. Real multipart and owner boundaries."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.storage import Storage


@pytest.fixture
def web(tmp_path, monkeypatch):
    router = importlib.import_module("app.modules.vxvue_chatbot.router")
    store = Storage(tmp_path / "app.db")
    settings = SimpleNamespace(root=tmp_path, get=lambda key, default=None: default)
    monkeypatch.setattr(router, "get_storage", lambda: store)
    monkeypatch.setattr(router, "get_settings", lambda: settings)
    monkeypatch.setattr(router.service, "wake", lambda: None)
    app = FastAPI()
    app.include_router(router.router, prefix="/vxvue-chatbot")
    return TestClient(app), store, router


def make_chat(client):
    assert client.get("/vxvue-chatbot").status_code == 200
    return client.post("/vxvue-chatbot/conversations").json()["id"]


def test_ten_files_accepted_eleven_rejected_and_history_persists(web):
    client, store, router = web
    chat = make_chat(client)
    eleven = [("files", (f"{n}.txt", b"evidence", "text/plain")) for n in range(11)]
    assert client.post(f"/vxvue-chatbot/conversations/{chat}/messages", data={"text": "질문"}, files=eleven).status_code == 400
    accepted = client.post(f"/vxvue-chatbot/conversations/{chat}/messages", data={"text": "질문"}, files=eleven[:10])
    assert accepted.status_code == 202
    history = client.get(f"/vxvue-chatbot/conversations/{chat}").json()
    assert len(history["messages"][0]["attachments"]) == 10
    assert "path" not in history["messages"][0]["attachments"][0]
    assert "owner" not in history
    assert client.get("/vxvue-chatbot/conversations").json()[0]["title"] == "질문"


def test_other_browser_cannot_read_attachments_jobs_or_history(web):
    client, store, router = web
    chat = make_chat(client)
    job = client.post(f"/vxvue-chatbot/conversations/{chat}/messages", data={"text": "질문"}, files=[("files", ("x.txt", b"private", "text/plain"))]).json()
    attachment = client.get(f"/vxvue-chatbot/conversations/{chat}").json()["messages"][0]["attachments"][0]["id"]
    other = TestClient(client.app)
    other.get("/vxvue-chatbot")
    for path in (f"/conversations/{chat}", f"/jobs/{job['id']}", f"/attachments/{attachment}", f"/jobs/{job['id']}/answer", f"/jobs/{job['id']}/source?document_id=x.txt"):
        assert other.get("/vxvue-chatbot" + path).status_code == 404
    assert other.post(f"/vxvue-chatbot/jobs/{job['id']}/cancel").status_code == 404
    assert other.delete(f"/vxvue-chatbot/conversations/{chat}").status_code == 404
    assert other.get("/vxvue-chatbot/conversations").json() == []
    response = client.get(f"/vxvue-chatbot/attachments/{attachment}")
    assert response.content == b"private"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_oversize_unsupported_empty_and_cross_origin_are_rejected(web, monkeypatch):
    client, store, router = web
    chat = make_chat(client)
    monkeypatch.setattr(router, "file_limit", lambda: 3)
    url = f"/vxvue-chatbot/conversations/{chat}/messages"
    assert client.post(url, data={"text": ""}).status_code == 400
    assert client.post(url, data={"text": "q"}, files=[("files", ("x.exe", b"a"))]).status_code == 400
    assert client.post(url, files=[("files", ("x.txt", b"abcd"))]).status_code == 413
    assert client.post("/vxvue-chatbot/conversations", headers={"origin": "https://hostile.example"}).status_code == 403
    assert list((router.files_root()).glob("*")) == []


def test_page_has_history_composer_and_max_ten_files(web):
    client, _, _ = web
    text = client.get("/vxvue-chatbot").text
    assert "VXvue AI 챗봇" in text
    assert 'id="conversation-list"' in text
    assert 'id="messages"' in text
    assert 'id="file-input"' in text and "multiple" in text
    assert "최대 10개" in text


def test_sync_status_explains_partial_failure_exclusions_and_retry(web):
    client, store, _ = web
    run_id = store.sync_start("VXvue", "product_knowledge", "knowledge_folder")
    store.sync_finish(run_id, "PARTIAL", "broken.pdf 읽기 실패 / 제외 old.txt / 다음 재시도 2026-10-13T08:15:00+09:00")
    data = client.get("/vxvue-chatbot/knowledge-status").json()
    assert "broken.pdf" in data["last_attempt"]["detail"]
    assert "old.txt" in data["last_attempt"]["detail"]
    assert data["next_retry_at"] == "2026-10-13T08:15:00+09:00"
    assert data["last_success_at"] is None


def test_sync_status_lists_manifest_exclusions_and_read_failures(web):
    import json
    from app.core.product_knowledge import manifest_path
    client, _, router = web
    path = manifest_path("VXvue", router.get_settings().root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"excluded": [{"file_name": "old.md", "exclude_reason": "older revision"}], "assets": [{"file_name": "broken.pdf", "error": "parse failed"}]}), encoding="utf-8")
    response = client.get("/vxvue-chatbot/knowledge-status")
    assert response.json()["excluded"][0]["name"] == "old.md"
    assert response.json()["failed_documents"][0]["name"] == "broken.pdf"
    assert response.headers["cache-control"] == "no-store"
