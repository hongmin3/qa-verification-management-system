"""Validates: REQ-VXVUECHATBOT-002, REQ-VXVUECHATBOT-004, REQ-VXVUECHATBOT-006."""
import pytest
from app.core.storage import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "chat.db")


def test_browser_histories_are_separate_and_survive_restart(store):
    chat = store.vxchat_create("owner-a", "첫 대화")
    job = store.vxchat_submit("owner-a", chat["id"], "Exposure 질문", [])
    assert store.vxchat_get("owner-b", chat["id"]) is None
    assert store.vxchat_job("owner-b", job["id"]) is None
    store.vxchat_finish(job["id"], "DONE", "답변", usage={"total_tokens": 42})
    reopened = Storage(store.db_path)
    history = reopened.vxchat_get("owner-a", chat["id"])
    assert [m["content"] for m in history["messages"]] == ["Exposure 질문", "답변"]
    assert reopened.vxchat_list("owner-b") == []
    assert reopened.tokens_used_since("2000") == 42


def test_busy_conversation_and_queue_are_bounded(store):
    chat = store.vxchat_create("a")
    store.vxchat_submit("a", chat["id"], "질문", [], max_pending=1)
    with pytest.raises(ValueError, match="진행"):
        store.vxchat_submit("a", chat["id"], "중복", [])
    another = store.vxchat_create("b")
    with pytest.raises(ValueError, match="대기"):
        store.vxchat_submit("b", another["id"], "질문", [], max_pending=1)


def test_cancel_is_terminal_and_cannot_be_overwritten(store):
    chat = store.vxchat_create("a")
    job = store.vxchat_submit("a", chat["id"], "질문", [])
    assert not store.vxchat_cancel("b", job["id"])
    assert store.vxchat_cancel("a", job["id"])
    assert not store.vxchat_finish(job["id"], "DONE", "늦은 답변")
    assert store.vxchat_job("a", job["id"])["status"] == "CANCELLED"


def test_restart_marks_running_failed_and_preserves_queued(store):
    one, two = store.vxchat_create("a"), store.vxchat_create("b")
    running = store.vxchat_submit("a", one["id"], "실행", [])
    queued = store.vxchat_submit("b", two["id"], "대기", [])
    assert store.vxchat_start(running["id"])
    assert store.vxchat_recover() == 1
    assert store.vxchat_job("a", running["id"])["status"] == "FAILED"
    assert store.vxchat_pending()[0]["id"] == queued["id"]


def test_attachment_access_and_delete_are_owned(store):
    chat = store.vxchat_create("a")
    job = store.vxchat_submit("a", chat["id"], "첨부", [{"id": "f1", "name": "a.txt", "path": "/safe/f1.txt", "bytes": 4}])
    assert store.vxchat_attachment("b", "f1") is None
    assert store.vxchat_attachment("a", "f1")["name"] == "a.txt"
    assert store.vxchat_delete("b", chat["id"]) is None
    with pytest.raises(ValueError, match="중지"):
        store.vxchat_delete("a", chat["id"])
    store.vxchat_cancel("a", job["id"])
    assert store.vxchat_delete("a", chat["id"])
    assert store.vxchat_attachment("a", "f1") is None
    assert store.vxchat_job("a", job["id"]) is None


# Validates: REQ-VXVUECHATBOT-002
def test_auto_title_skips_user_title(store):
    chat = store.vxchat_create("owner")
    store.vxchat_rename("owner", chat["id"], "내가 정한 제목")
    assert store.vxchat_set_ai_title(chat["id"], "AI 제목") is False
    assert store.vxchat_get("owner", chat["id"])["title"] == "내가 정한 제목"


# Validates: REQ-VXVUECHATBOT-002
def test_ai_title_replaces_auto_title(store):
    chat = store.vxchat_create("owner")
    store.vxchat_submit("owner", chat["id"], "첫 질문", [])
    assert store.vxchat_get("owner", chat["id"])["title_source"] == "auto"
    assert store.vxchat_set_ai_title(chat["id"], "Theme 설정 사양") is True
    got = store.vxchat_get("owner", chat["id"])
    assert (got["title"], got["title_source"]) == ("Theme 설정 사양", "ai")


# Validates: REQ-VXVUECHATBOT-002
def test_existing_db_gets_title_source_column_in_place(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    Storage(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE vxchat_conversations DROP COLUMN title_source")
        db.execute("INSERT INTO vxchat_conversations VALUES('c1','o','옛 제목','t','t')")
    reopened = Storage(path)
    assert reopened.vxchat_get("o", "c1")["title_source"] == "auto"
