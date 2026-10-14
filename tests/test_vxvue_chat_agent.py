"""Validates: REQ-VXVUECHATBOT-004 and dedicated restricted MCP execution."""
from pathlib import Path
import importlib
import json
import sys
import threading
import time
from types import SimpleNamespace
import pytest


def test_cli_is_restricted_and_uses_prompt_file_and_mcp():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    args = agent.build_command("claude", Path("prompt.txt"), Path("mcp.json"), model="test-model", effort="medium")
    assert args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args
    assert "--no-session-persistence" in args
    assert "--system-prompt-file" in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--allowedTools") + 1] == "mcp__vxvue_knowledge__*"
    assert "--dangerously-skip-permissions" not in args


def test_stream_events_keep_answer_and_tool_progress():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    assert agent.event_update({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__vxvue_knowledge__get_document"}]}})[0] == "원문 확인"
    stage, text = agent.event_update({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "안녕하세요"}}})
    assert (stage, text) == ("답변 작성", "안녕하세요")


def test_history_is_bounded_without_deleting_stored_messages():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    messages = [{"role": "user", "content": "a" * 100, "attachments": []}, {"role": "user", "content": "latest", "attachments": []}]
    prompt, omitted = agent.history_prompt(messages, max_chars=120)
    assert "latest" in prompt
    assert omitted == 1
    assert len(messages) == 2


def test_mcp_text_masks_sensitive_values_but_keeps_qa_identifiers():
    mcp = importlib.import_module("app.modules.vxvue_chatbot.mcp_server")
    value = mcp.text_content({"text": "Patient ID: 123456 Patient Name: Alice token=abcdefghijk VP-1234"})
    assert "123456" not in value[0]["text"]
    assert "Alice" not in value[0]["text"]
    assert "abcdefghijk" not in value[0]["text"]
    assert "VP-1234" in value[0]["text"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_runner_stops_even_when_cli_does_not_read_large_stdin(tmp_path, monkeypatch, cancelled):
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    settings = SimpleNamespace(secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work"), "vxvue_chatbot.timeout_seconds": 1}.get(key, default))
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda command: sys.executable)
    monkeypatch.setattr(agent, "build_command", lambda *args, **kwargs: [sys.executable, "-c", "import time; time.sleep(60)"])
    cancel = threading.Event()
    if cancelled:
        threading.Timer(.3, cancel.set).start()
    start = time.monotonic()
    with pytest.raises(InterruptedError if cancelled else TimeoutError):
        agent.run([{"role": "user", "content": "x" * 120000}], {"rules": "Patient ID: 123456 VP-1234"}, [], "fake", settings, cancel, lambda *args: None)
    assert time.monotonic() - start < 8
    system = (tmp_path / "work" / "vxvue-chatbot" / "fake" / "system.txt").read_text(encoding="utf-8")
    assert "123456" not in system and "VP-1234" in system


# Validates: REQ-VXVUECHATBOT-002
def test_split_title_extracts_marker():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    assert agent.split_title("답\n<!-- title: Theme 설정 사양 -->") == ("답", "Theme 설정 사양")


# Validates: REQ-VXVUECHATBOT-002
def test_title_marker_missing_or_invalid():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    assert agent.split_title("답변만") == ("답변만", None)
    for marker in ("<!-- title: " + "가" * 41 + " -->", "<!-- title: 가\n나 -->", "<!-- title:   -->"):
        body, title = agent.split_title("답\n" + marker)
        assert title is None and "<!--" not in body and body == "답"


# Validates: REQ-VXVUECHATBOT-002
def test_split_title_handles_unclosed_trailing_and_quoted_markers():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    assert agent.split_title("답\n<!-- title: abc") == ("답", None)
    assert agent.split_title("답\n<!-- title: 제목 -->  \n\n") == ("답", "제목")
    assert agent.split_title("답\n<!--title:제목-->") == ("답", "제목")
    quoted = "예: `<!-- title: 예시 -->` 처럼 쓴다.\n\n본문 계속"
    assert agent.split_title(quoted + "\n<!-- title: 진짜 -->") == (quoted, "진짜")
    assert agent.split_title(quoted) == (quoted, None)


# Validates: REQ-VXVUECHATBOT-002
def test_history_prompt_requests_title_once():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    first = [{"role": "user", "content": "q", "attachments": []}, {"role": "assistant", "content": "", "attachments": []}]
    later = [{"role": "user", "content": "q", "attachments": []}, {"role": "assistant", "content": "a", "attachments": []},
             {"role": "user", "content": "q2", "attachments": []}, {"role": "assistant", "content": "", "attachments": []}]
    assert json.loads(agent.history_prompt(first)[0])["title_needed"] is True
    assert json.loads(agent.history_prompt(later)[0])["title_needed"] is False


# Validates: REQ-VXVUECHATBOT-002
def test_streaming_hides_title_marker():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    assert agent.hide_title_marker("본문\n<!-- title: 제목") == "본문"
    assert agent.hide_title_marker("본문\n<!-- ti") == "본문"
    assert agent.hide_title_marker("본문 <b") == "본문 <b"
    assert agent.hide_title_marker("본문\n<!--title:제") == "본문"
    assert agent.hide_title_marker("본문\n<!--   title: 제목 -->\n") == "본문"
    assert agent.hide_title_marker("본문\n<!--  ") == "본문"


# Validates: REQ-VXVUECHATBOT-002
def test_history_prompt_title_needed_counts_only_done_answers_and_skips_user_title():
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    messages = [{"id": "u1", "role": "user", "content": "q", "attachments": []}, {"id": "a1", "role": "assistant", "content": "답변 생성을 중지했습니다.", "attachments": []},
                {"id": "u2", "role": "user", "content": "q2", "attachments": []}, {"id": "a2", "role": "assistant", "content": "", "attachments": []}]
    jobs = [{"assistant_message_id": "a1", "status": "CANCELLED"}, {"assistant_message_id": "a2", "status": "RUNNING"}]
    assert json.loads(agent.history_prompt(messages, jobs=jobs)[0])["title_needed"] is True
    done = [{"assistant_message_id": "a1", "status": "DONE"}, *jobs[1:]]
    assert json.loads(agent.history_prompt(messages, jobs=done)[0])["title_needed"] is False
    assert json.loads(agent.history_prompt(messages, jobs=jobs, title_source="user")[0])["title_needed"] is False


def _run_fake_cli(tmp_path, monkeypatch, events):
    agent = importlib.import_module("app.modules.vxvue_chatbot.agent")
    script = tmp_path / "fake_cli.py"
    script.write_text("import sys\nfor line in %r:\n    print(line, flush=True)\n" % [json.dumps(e) for e in events], encoding="utf-8")
    settings = SimpleNamespace(secrets=SimpleNamespace(claude_code_oauth_token=""), get=lambda key, default=None: {"ai.claude.workdir": str(tmp_path / "work")}.get(key, default))
    monkeypatch.setattr(agent.claude_cli, "resolve_command", lambda command: sys.executable)
    monkeypatch.setattr(agent, "build_command", lambda *args, **kwargs: [sys.executable, str(script)])
    return agent, lambda: agent.run([{"role": "user", "content": "q"}], {"rules": "rules"}, [], "fake", settings, threading.Event(), lambda *args: None)


# Validates: REQ-AICALL-006
def test_run_returns_rate_limit_event(tmp_path, monkeypatch):
    first = {"unifiedWindows": {"five_hour": {"utilization": 0.1, "resets_at": 1}}}
    last = {"unifiedWindows": {"five_hour": {"utilization": 0.35, "resets_at": 2}, "seven_day": {"utilization": 0.62, "resets_at": 3}}}
    events = [{"type": "rate_limit_event", "rate_limit_info": first}, {"type": "rate_limit_event", "rate_limit_info": last},
              {"type": "rate_limit_event", "rate_limit_info": "not-a-dict"},
              {"type": "result", "subtype": "success", "is_error": False, "result": "답변", "usage": {}}]
    _, call = _run_fake_cli(tmp_path, monkeypatch, events)
    assert call()["rate_limit"] == last


# Validates: REQ-AICALL-006
def test_run_rate_limit_is_none_without_event(tmp_path, monkeypatch):
    _, call = _run_fake_cli(tmp_path, monkeypatch, [{"type": "result", "subtype": "success", "is_error": False, "result": "답변", "usage": {}}])
    assert call()["rate_limit"] is None


# Validates: REQ-AICALL-006
def test_failed_run_keeps_rate_limit_on_exception(tmp_path, monkeypatch):
    info = {"unifiedWindows": {"five_hour": {"utilization": 1.0, "resets_at": 2}}}
    events = [{"type": "rate_limit_event", "rate_limit_info": info},
              {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "You've hit your limit", "usage": {}}]
    _, call = _run_fake_cli(tmp_path, monkeypatch, events)
    with pytest.raises(RuntimeError) as caught:
        call()
    assert caught.value.rate_limit == info
