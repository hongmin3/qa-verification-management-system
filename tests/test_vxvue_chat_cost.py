"""Validates: REQ-VXVUECHATBOT-007 without discarding QA evidence."""
import json
import threading
from types import SimpleNamespace
import sys

import pytest
from app.modules.vxvue_chatbot import agent, mcp_server
from app.modules.vxvue_chatbot import service
from tests.test_vxvue_chat_service import seeded
from app.modules.vxvue_chatbot.knowledge import prepare_knowledge


def test_compaction_removes_repeated_inventory_only():
    source = {"scope": {"documents": [{"huge": "x" * 20000}], "root": "private", "index_path": "private", "checked_utc": "now", "files": 1, "errors": [{"document_id": "bad"}], "warnings": ["review"]}, "results": [{"evidence": "unchanged", "location": {"sheet": "S09", "cell": "G7"}, "details": {"strike": True, "role": "expected"}, "sha256": "hash", "revision": "1.18", "evidence_truncated": True}], "limitations": ["unknown"]}
    packed = mcp_server.compact_response(source)
    assert packed["results"] == source["results"]
    assert packed["scope"]["errors"] == source["scope"]["errors"]
    assert packed["scope"]["warnings"] == ["review"]
    assert "documents" not in packed["scope"] and "private" not in json.dumps(packed)
    assert len(json.dumps(packed)) < len(json.dumps(source)) / 10
    assert "documents" in source["scope"]  # source remains intact


def test_batch_partial_failure_preserves_search_and_citation(tmp_path):
    store = seeded(tmp_path)
    snapshot = prepare_knowledge(store, tmp_path / "snapshots")
    session = mcp_server.KnowledgeTools(snapshot, [], tmp_path / "trace", "job")
    result = session.call("batch_lookup", {"requests": [{"tool": "search_documents", "arguments": {"query": "VP-1234", "limit": 3}}, {"tool": "get_document", "arguments": {"document_id": "missing.md"}}]})
    assert result["results"][0]["value"]["results"][0]["reference_url"]
    assert result["results"][1]["is_error"] is True
    assert result["results"][1]["evidence_status"] == "incomplete"
    assert "scope" in result and "documents" not in result["scope"]
    with pytest.raises(ValueError):
        session.call("batch_lookup", {"requests": [{"tool": "shell", "arguments": {}}]})
    with pytest.raises(ValueError):
        session.call("batch_lookup", {"requests": [{}] * 11})


def test_usage_distinguishes_cache_from_new_input_and_bill():
    metrics = agent.usage_metrics({"usage": {"input_tokens": 100, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 700, "output_tokens": 50, "cache_creation": {"ephemeral_1h_input_tokens": 200}}, "num_turns": 3, "total_cost_usd": .04, "duration_ms": 1200}, tool_calls=2)
    assert metrics["input_tokens"] == 100
    assert metrics["cache_read_input_tokens"] == 700
    assert metrics["cache_read_ratio"] == .7
    assert metrics["num_turns"] == 3 and metrics["tool_calls"] == 2
    assert metrics["estimated_cost_usd"] == .04 and metrics["cost_basis"] == "cli_estimate"
    assert agent.usage_metrics({}, 0)["estimated_cost_usd"] is None


def test_error_kind_does_not_treat_any_limit_word_as_quota():
    assert "사용량 한도" in agent.failure_message({"result": "You've hit your limit"})
    assert "호출 제한" in agent.failure_message({"result": "rate_limit_error", "api_error_status": 429})
    assert "혼잡" in agent.failure_message({"result": "overloaded_error", "api_error_status": 529})
    assert "사용량 한도" not in agent.failure_message({"result": "invalid usage policy"})


def test_limits_keep_opus_medium():
    args = agent.build_command("claude", "system", "mcp", model="claude-opus-5-5", effort="medium", max_turns=24, max_budget_usd=0)
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--effort") + 1] == "medium"
    assert args[args.index("--max-turns") + 1] == "24"
    assert "--max-budget-usd" not in args
    args = agent.build_command("claude", "system", "mcp", model="claude-opus-5-5", effort="medium", max_turns=24, max_budget_usd=2)
    assert args[args.index("--max-budget-usd") + 1] == "2"


def test_history_json_roundtrip_preserves_all_content():
    messages = [{"role": "user", "content": "원문 조건과 VP-1234\n" * 30, "attachments": [{"id": "one", "name": "S09.json"}]}, {"role": "assistant", "content": "# Expected\n| Step | Result |\n| 1 | Ready |", "attachments": []}]
    prompt, omitted = agent.history_prompt(messages)
    assert json.loads(prompt)["conversation"] == messages and omitted == 0
    assert len(prompt) < len(json.dumps(json.loads(prompt), ensure_ascii=False))


def test_lower_search_default_can_be_expanded_without_losing_candidates(tmp_path):
    store = seeded(tmp_path)
    snapshot = prepare_knowledge(store, tmp_path / "snapshots")
    session = mcp_server.KnowledgeTools(snapshot, [], tmp_path / "trace", "job")
    limits = []
    def search(**arguments):
        limits.append(arguments["limit"])
        return {"results": [], "status": "not-found-in-readable-sources", "support_status": "unknown"}
    session.corpus.search_documents = search
    session.call("search_documents", {"query": "more"})
    result = session.call("search_documents", {"query": "more", "limit": 40})
    assert limits == [6, 40] and result["support_status"] == "unknown"


def test_cli_budget_error_records_usage_without_retry(tmp_path, monkeypatch):
    settings = SimpleNamespace(secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work")}.get(key, default))
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda c: sys.executable)
    script = "import sys,json;sys.stdin.read();print(json.dumps({'type':'result','is_error':True,'subtype':'error_max_turns','num_turns':24,'usage':{'input_tokens':50,'cache_read_input_tokens':100}}));sys.exit(1)"
    monkeypatch.setattr(agent, "build_command", lambda *a, **kw: [sys.executable, "-c", script])
    with pytest.raises(RuntimeError, match="진행 횟수") as caught:
        agent.run([{"role": "user", "content": "QA"}], {"rules": "full"}, [], "limited", settings, threading.Event(), lambda *args: None)
    assert caught.value.usage["total_tokens"] == 150
    assert caught.value.metrics["num_turns"] == 24


@pytest.mark.parametrize("reason", ["usage limit exceeded", "upstream failure"])
def test_final_cli_failure_persists_consumed_tokens_and_cost(tmp_path, monkeypatch, reason):
    store = seeded(tmp_path)
    chat = store.vxchat_create("owner")
    job = store.vxchat_submit("owner", chat["id"], "QA", [])
    settings = SimpleNamespace(root=tmp_path, secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work"), "ai.claude.models.standard": "claude-opus-5-5"}.get(key, default))
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda c: sys.executable)
    event = {"type": "result", "is_error": True, "subtype": "error_during_execution", "result": reason, "usage": {"input_tokens": 50, "cache_read_input_tokens": 100, "output_tokens": 25}, "total_cost_usd": .04}
    script = "import sys;sys.stdin.read();print(" + repr(json.dumps(event)) + ");sys.exit(1)"
    monkeypatch.setattr(agent, "build_command", lambda *a, **kw: [sys.executable, "-c", script])
    service.run_job(store, settings, {**job, "owner": "owner"}, threading.Event())
    result = store.get_analysis(job["id"])
    assert result["status"] == "FAILED"
    assert result["result"]["token_usage"]["total_tokens"] == 175
    assert result["result"]["cli_usage"]["estimated_cost_usd"] == .04


def test_runner_keeps_directory_and_system_stable_between_jobs(tmp_path, monkeypatch):
    settings = SimpleNamespace(secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work"), "ai.claude.models.standard": "claude-opus-5-5"}.get(key, default))
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda c: sys.executable)
    script = "import sys,json,os;sys.stdin.read();print(json.dumps({'type':'result','result':'ok','num_turns':1,'usage':{'input_tokens':10},'cwd':os.getcwd()}))"
    monkeypatch.setattr(agent, "build_command", lambda *a, **kw: [sys.executable, "-c", script])
    paths = []
    original = agent.subprocess.Popen
    def process(*args, **kwargs):
        paths.append(kwargs["cwd"])
        return original(*args, **kwargs)
    monkeypatch.setattr(agent.subprocess, "Popen", process)
    for job_id, failures in [("one", []), ("two", [{"name": "missing"}])]:
        result = agent.run([{"role": "user", "content": "VP-1234"}], {"rules": "full rules unchanged", "failures": failures}, [], job_id, settings, threading.Event(), lambda *args: None)
        assert result["metrics"]["num_turns"] == 1
    assert paths[0] == paths[1] == tmp_path / "work" / "vxvue-chatbot"
    assert (paths[0] / "one" / "system.txt").read_bytes() == (paths[1] / "two" / "system.txt").read_bytes()
    assert "missing" in (paths[1] / "two" / "input.json").read_text(encoding="utf-8")


def fake_search_session(tmp_path, evidence="가" * 1800, results_per_search=6):
    store = seeded(tmp_path)
    snapshot = prepare_knowledge(store, tmp_path / "snapshots")
    session = mcp_server.KnowledgeTools(snapshot, [], tmp_path / "trace", "job")
    def search(**arguments):
        return {"query": arguments["query"], "limitations": ["same limitation"], "scope": {"files": 1},
                "results": [{"document_id": f"doc-{arguments['query']}.md", "chunk_id": f"doc-{arguments['query']}.md::p{n}", "evidence": evidence, "evidence_truncated": False}
                            for n in range(results_per_search)]}
    session.corpus.search_documents = search
    return session


def search_requests(count):
    return [{"tool": "search_documents", "arguments": {"query": f"q{n}"}} for n in range(count)]


# Validates: REQ-VXVUECHATBOT-007
def test_batch_respects_token_budget(tmp_path):
    session = fake_search_session(tmp_path)
    requests = search_requests(10)
    result = session.call("batch_lookup", {"requests": requests})
    assert mcp_server.estimate_tokens(result) <= 30000
    assert result["budget"]["limit_tokens"] == 30000 and result["budget"]["used_tokens"] <= 30000
    assert result["budget"]["trimmed"] == [1,2,3,4,5,6,7,8,9]
    for number, entry in enumerate(result["results"]):
        if number == 0:
            assert not entry.get("deferred") and entry["value"]["results"][0]["evidence_truncated"] is False
        elif number in {5, 6, 7, 8, 9}:
            assert entry["deferred"] is True and entry["is_error"] is False
            assert entry["tool"] == "search_documents" and entry["arguments"] == requests[number]["arguments"]
            assert "—" not in entry["reason"]
        else:
            assert all(r["evidence_truncated"] is True and len(r["evidence"]) == 300 and r["chunk_id"] for r in entry["value"]["results"])
    assert mcp_server.estimate_tokens("abcdabcd가") == 4


# Validates: REQ-VXVUECHATBOT-005
def test_deferred_request_documents_are_not_traced(tmp_path):
    session = fake_search_session(tmp_path)
    result = session.call("batch_lookup", {"requests": search_requests(10)})
    traced = {json.loads(line)["document_id"] for line in (tmp_path / "trace").read_text(encoding="utf-8").splitlines()}
    returned = {r["document_id"] for e in result["results"] if not e.get("deferred") for r in e["value"]["results"]}
    deferred = {f"doc-q{n}.md" for n, e in enumerate(result["results"]) if e.get("deferred")}
    assert deferred and traced == returned and not traced & deferred


# Validates: REQ-VXVUECHATBOT-007
def test_shortened_evidence_keeps_text_around_query_match(tmp_path):
    evidence = "가" * 900 + "NEEDLE문장" + "나" * 900
    session = fake_search_session(tmp_path, evidence=evidence)
    requests = [{"tool": "search_documents", "arguments": {"query": "needle"}}] * 4
    result = session.call("batch_lookup", {"requests": requests})
    shortened = [e for e in result["results"] if not e.get("deferred") and e["value"]["results"][0]["evidence_truncated"]]
    assert shortened and all("NEEDLE" in r["evidence"] for e in shortened for r in e["value"]["results"])


# Validates: REQ-VXVUECHATBOT-007
def test_batch_drops_repeated_limitations(tmp_path):
    session = fake_search_session(tmp_path, evidence="x" * 10, results_per_search=1)
    result = session.call("batch_lookup", {"requests": search_requests(3)})
    assert result["limitations"] == ["same limitation"]
    assert all("limitations" not in r["value"] for r in result["results"])
    assert result["budget"]["trimmed"] == []


# Validates: REQ-VXVUECHATBOT-007
def test_cli_env_sets_mcp_output_tokens(tmp_path, monkeypatch):
    captured = {}
    class Stop(Exception):
        pass
    def popen(args, **kwargs):
        captured.update(kwargs["env"])
        raise Stop
    monkeypatch.setattr(agent.subprocess, "Popen", popen)
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda c: sys.executable)
    settings = SimpleNamespace(secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work")}.get(key, default))
    snapshot = {"id": "s", "root": str(tmp_path), "index": str(tmp_path / "i"), "documents": [], "rules": "RULES"}
    with pytest.raises(Stop):
        agent.run([{"role": "user", "content": "hi"}], snapshot, [], "job1", settings, threading.Event(), lambda *a: None)
    assert captured["MAX_MCP_OUTPUT_TOKENS"] == "40000"
