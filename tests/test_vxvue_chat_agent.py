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
