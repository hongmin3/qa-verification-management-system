"""Validates: REQ-VXVUECHATBOT-003/004/005/006. Full queue persistence and MCP transport."""
import io
import json
import threading
from types import SimpleNamespace

from app.core.storage import Storage
from app.modules.vxvue_chatbot import service
from app.modules.vxvue_chatbot.knowledge import prepare_knowledge
from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools, serve


def seeded(tmp_path):
    store = Storage(tmp_path / "app.db")
    for kind, name, text in [("instruction_prompt", "VXvue_prompt_Rev1.18.txt", "Preserve observed facts."), ("qa_rules", "[QA 작성 규칙] VXvue_Rev1.18.md", "# QA\nCite original source."), ("specification", "(사양서) Exposure.md", "# Exposure\nVP-1234: Ready before exposure.")]:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        store.add_document(kind, "VXvue", "1", "", name, path)
    return store


def test_job_reads_prior_attachments_and_persists_citations_and_usage(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    path = tmp_path / "log.txt"
    path.write_text("Patient ID: 123456\nVP-1234 observed Ready", encoding="utf-8")
    attachment = {"id": "attached", "path": str(path), "name": "log.txt", "bytes": path.stat().st_size}
    first = store.vxchat_submit("owner", chat["id"], "first", [attachment])
    store.vxchat_finish(first["id"], "DONE", "old response")
    job = store.vxchat_submit("owner", chat["id"], "follow up", [])
    def fake(messages, snapshot, attachments, job_id, settings, cancel, progress):
        assert [a["id"] for a in attachments] == ["attached"]
        assert any(m["content"] == "old response" for m in messages)
        trace = tmp_path / "references.jsonl"
        tools = KnowledgeTools(snapshot, attachments, trace, job_id)
        tools.call("search_documents", {"query": "VP-1234"})
        result = tools.call("get_document", {"document_id": "(사양서) Exposure.md"})
        assert "reference_url" in json.dumps(result)
        text = tools.call("read_attachment", {"attachment_id": "attached"})[0]["text"]
        assert "123456" not in text and "VP-1234" in text
        progress("답변 작성", "partial")
        return {"text": "final", "citations": [json.loads(trace.read_text(encoding="utf-8").splitlines()[0])], "usage": {"total_tokens": 123}, "model": "fake", "metrics": {"cache_read_input_tokens": 100, "input_tokens": 23, "cost_basis": "cli_estimate"}}
    monkeypatch.setattr(service.agent, "run", fake)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    assert store.vxchat_job("owner", job["id"])["status"] == "DONE"
    message = store.vxchat_get("owner", chat["id"])["messages"][-1]
    assert message["content"] == "final" and message["citations"][0]["reference_url"]
    assert store.get_analysis(job["id"])["result"]["token_usage"]["total_tokens"] == 123
    assert store.get_analysis(job["id"])["result"]["cli_usage"]["cache_read_input_tokens"] == 100


def test_stdio_mcp_and_image_attachment(tmp_path):
    import fitz
    store = seeded(tmp_path)
    snapshot = prepare_knowledge(store, tmp_path / "snapshots")
    image = tmp_path / "image.png"
    fitz.Pixmap(fitz.csRGB, (0, 0, 50, 50), False).save(image)
    session = KnowledgeTools(snapshot, [{"id": "image", "path": str(image), "name": "image.png"}], tmp_path / "trace", "job")
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_attachment", "arguments": {"attachment_id": "image"}}}, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "shell", "arguments": {}}}]
    output = io.StringIO()
    serve(session, io.StringIO("\n".join(json.dumps(r) for r in requests)), output)
    replies = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
    assert replies[0]["capabilities"] == {"tools": {}}
    assert all(t["annotations"]["readOnlyHint"] for t in replies[1]["tools"])
    assert replies[2]["content"][1]["type"] == "image"
    assert replies[3]["isError"] is True


def test_queue_rechecks_daily_budget_before_calling_claude(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    job = store.vxchat_submit("owner", chat["id"], "queued earlier", [])
    monkeypatch.setattr(service, "daily_token_status", lambda store: {"exceeded": True}, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Budget exceeded: no Claude call is allowed")
    monkeypatch.setattr(service.agent, "run", forbidden)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    stored = store.vxchat_job("owner", job["id"])
    assert stored["status"] == "FAILED" and "오늘 AI 사용량" in stored["error"]


# Validates: REQ-VXVUECHATBOT-002
def test_service_stores_ai_title(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    job = store.vxchat_submit("owner", chat["id"], "첫 질문", [])
    result = {"text": "답변", "title": "Theme 설정 사양", "citations": [], "usage": {"total_tokens": 1}, "model": "fake", "metrics": {}}
    monkeypatch.setattr(service.agent, "run", lambda *a, **k: result)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    got = store.vxchat_get("owner", chat["id"])
    assert got["messages"][-1]["content"] == "답변" and "<!--" not in got["messages"][-1]["content"]
    assert got["title"] == "Theme 설정 사양"


# Validates: REQ-VXVUECHATBOT-002
def test_title_write_failure_keeps_job_done(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    job = store.vxchat_submit("owner", chat["id"], "첫 질문", [])
    result = {"text": "답변", "title": "제목", "citations": [], "usage": {"total_tokens": 1}, "model": "fake", "metrics": {}}
    # 제목 저장 실패만 확인한다. 지식 사본의 파일 이동은 별도 지식 시험에서 확인한다.
    monkeypatch.setattr(service, "prepare_knowledge", lambda *a, **k: {})
    monkeypatch.setattr(service.agent, "run", lambda *a, **k: result)
    title_attempts = []
    def boom(*a, **k):
        title_attempts.append(a)
        raise RuntimeError("db locked")
    monkeypatch.setattr(store, "vxchat_set_ai_title", boom)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    assert store.vxchat_job("owner", job["id"])["status"] == "DONE"
    assert store.vxchat_get("owner", chat["id"])["messages"][-1]["content"] == "답변"
    assert title_attempts == [(chat["id"], "제목")]


# Validates: REQ-AICALL-006
def test_service_records_rate_limit_for_done_and_failed(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    recorded = []
    monkeypatch.setattr(service.claude_usage, "record_rate_limit_event", lambda info: recorded.append(info))
    info = {"unifiedWindows": {"five_hour": {"utilization": 0.35, "resets_at": 2}}}
    job = store.vxchat_submit("owner", chat["id"], "질문", [])
    monkeypatch.setattr(service.agent, "run", lambda *a, **k: {"text": "답변", "citations": [], "usage": {"total_tokens": 1}, "model": "fake", "metrics": {}, "rate_limit": info})
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    assert store.vxchat_job("owner", job["id"])["status"] == "DONE" and recorded == [info]
    failed = store.vxchat_submit("owner", chat["id"], "또 질문", [])
    def limit_hit(*a, **k):
        error = RuntimeError("한도 도달")
        error.rate_limit = info
        raise error
    monkeypatch.setattr(service.agent, "run", limit_hit)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**failed, "owner": "owner"}, threading.Event())
    assert store.vxchat_job("owner", failed["id"])["status"] == "FAILED" and recorded == [info, info]


# Validates: REQ-AICALL-006
def test_rate_limit_record_failure_keeps_job_done_and_failed_status(tmp_path, monkeypatch):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    def boom(info):
        raise OSError("disk full")
    monkeypatch.setattr(service.claude_usage, "record_rate_limit_event", boom)
    info = {"unifiedWindows": {}}
    job = store.vxchat_submit("owner", chat["id"], "질문", [])
    monkeypatch.setattr(service.agent, "run", lambda *a, **k: {"text": "답변", "citations": [], "usage": {"total_tokens": 1}, "model": "fake", "metrics": {}, "rate_limit": info})
    service.run_job(store, SimpleNamespace(root=tmp_path), {**job, "owner": "owner"}, threading.Event())
    assert store.vxchat_job("owner", job["id"])["status"] == "DONE"
    failed = store.vxchat_submit("owner", chat["id"], "또 질문", [])
    def limit_hit(*a, **k):
        error = RuntimeError("한도 도달")
        error.rate_limit = info
        raise error
    monkeypatch.setattr(service.agent, "run", limit_hit)
    service.run_job(store, SimpleNamespace(root=tmp_path), {**failed, "owner": "owner"}, threading.Event())
    stored = store.vxchat_job("owner", failed["id"])
    assert stored["status"] == "FAILED" and "한도 도달" in stored["error"]
