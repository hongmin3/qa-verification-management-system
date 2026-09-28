"""Validates: NFR-SEC-001, REQ-DAILY-008, REQ-DAILY-010 (TEST-DAILY-006)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.modules.daily_qa import agent_runner, rules
from app.modules.daily_qa.agent_runner import ClaudeRunner, build_command, build_env, build_prompt
from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.report import build_email
from app.modules.daily_qa.workspace import (
    ALLOWED_TOOLS,
    WorkspaceError,
    prepare,
    validate_location,
    write_task_input,
)


def _option_values(command: list[str], option: str) -> list[str]:
    start = command.index(option) + 1
    values = []
    for item in command[start:]:
        if item.startswith("--"):
            break
        values.append(item)
    return values


def test_command_restricts_tools_settings_and_mcp():
    command = build_command("claude")
    assert command[:2] == ["claude", "-p"]
    assert _option_values(command, "--output-format") == ["stream-json"] and "--verbose" in command  # 도구 호출 기록용
    assert _option_values(command, "--permission-mode") == ["dontAsk"]
    assert _option_values(command, "--setting-sources") == ["project"]
    assert "--strict-mcp-config" in command and "--no-session-persistence" in command
    assert "--bare" not in command                      # bare 는 OAuth 토큰을 읽지 않는다
    allowed = _option_values(command, "--allowedTools")
    denied = _option_values(command, "--disallowedTools")
    assert "Bash" not in " ".join(allowed) and not any(tool.startswith("Web") for tool in allowed)
    assert {"Bash", "WebFetch", "WebSearch"} <= set(denied)
    assert all(tool in ("Read", "Glob", "Grep", "Skill") or "runs/**/out/**" in tool for tool in allowed)


def test_env_passes_only_token_and_quiet_flags():
    base = {"PATH": "/usr/bin", "HOME": "/home/qa", "GEMINI_API_KEY": "g-secret", "SMTP_PASSWORD": "s-secret", "POLARION_TOKEN": "p"}
    env = build_env("tok", base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    assert env["DISABLE_TELEMETRY"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert not {"GEMINI_API_KEY", "SMTP_PASSWORD", "POLARION_TOKEN"} & env.keys()


def test_workspace_inside_repo_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(WorkspaceError):
        validate_location(repo / "workspace", repo)


def test_workspace_under_folder_with_agent_doc_is_refused(tmp_path):
    parent = tmp_path / "outer"
    parent.mkdir()
    (parent / "CLAUDE.md").write_text("@AGENTS.md", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        validate_location(parent / "ws", tmp_path / "repo")


def test_prepare_writes_settings_skills_and_rules(tmp_path):
    guide = tmp_path / "guide.md"
    guide.write_text("규칙 본문 담당자 hong@example.com", encoding="utf-8")
    run = prepare(tmp_path / "ws", tmp_path / "repo", "20260928-090000", guide, None)
    settings = json.loads((run.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["deny"][0] == "Bash"
    assert settings["permissions"]["allow"] == list(ALLOWED_TOOLS)
    skills = {path.parent.name for path in (run.root / ".claude" / "skills").glob("*/SKILL.md")}
    assert {"vxvue-qa-rules", "vxvue-spec-change-impact", "vxvue-issue-verification", "vxvue-manual-completeness"} <= skills
    copied = (run.root / "rules" / "qa-guide.md").read_text(encoding="utf-8")
    assert "hong@example.com" not in copied                   # 규칙 사본도 마스킹된다
    assert run.out_dir.is_dir() and run.in_dir.is_dir()


def test_task_input_is_masked_before_it_reaches_the_workspace(tmp_path):
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    path, report = write_task_input(run, "B-001", {"note": "담당자 hong@example.com 확인", "srs": "VP-1108"})
    text = path.read_text(encoding="utf-8")
    assert "hong@example.com" not in text and "VP-1108" in text
    assert report["total"] >= 1


def test_prompt_names_skill_and_result_file(tmp_path):
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    prompt = build_prompt(Task("B-001", "vxvue-spec-change-impact", {}), run)
    assert prompt.startswith("/vxvue-spec-change-impact runs/r1/in/B-001.json")
    assert "runs/r1/out/B-001.json" in prompt


def test_runner_hides_token_in_errors_and_records_usage(tmp_path, monkeypatch):
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 1, stdout=json.dumps({"is_error": True, "subtype": "error", "total_cost_usd": 0.1}),
                                           stderr="auth failed for tok-secret-123")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
    outcome = ClaudeRunner("claude", "tok-secret-123", 30).run(Task("B-001", "vxvue-spec-change-impact", {}), run)
    assert not outcome.ok and "tok-secret-123" not in outcome.error
    assert captured["cwd"] == run.root and captured["input"].startswith("/vxvue-spec-change-impact")
    log = json.loads((run.run_dir / "logs" / "B-001.claude.json").read_text(encoding="utf-8"))
    assert log["total_cost_usd"] == 0.1 and "result" not in log


def test_rules_revision_mismatch_blocks(tmp_path, monkeypatch):
    guide = tmp_path / "[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드_Rev1.12.md"
    guide.write_text("x", encoding="utf-8")
    monkeypatch.setattr(rules, "collected_assets", lambda product, kind, root=None: [{"file_name": guide.name}] if kind == rules.KIND_QA_RULES else [])
    monkeypatch.setattr(rules, "collected_path", lambda product, asset, root=None: tmp_path / asset["file_name"])
    state = rules.check("VXvue")
    assert not state.ok and state.found_rev == "1.12" and "1.17" in state.reason
    guide.rename(tmp_path / guide.name.replace("1.12", "1.17"))
    monkeypatch.setattr(rules, "collected_assets", lambda product, kind, root=None: [{"file_name": guide.name.replace("1.12", "1.17")}] if kind == rules.KIND_QA_RULES else [])
    assert rules.check("VXvue").ok


def test_email_contains_counts_and_link_but_no_spec_text():
    stages = {"collect_srs": {"status": "ok", "note": ""}, "B": {"status": "ok", "note": ""}}
    summary = {"findings": 2, "by_skill": {"vxvue-spec-change-impact": 2}, "by_verdict": {"vxvue-spec-change-impact": {"수정 필수": 2}}, "questions": 1}
    subject, text, body = build_email("20260928-090000", "성공", stages, summary, "http://qa/daily-qa/runs/1", "")
    assert "Finding 2건" in subject and "질문 1건" in subject
    assert "http://qa/daily-qa/runs/1" in text and "수정 필수 2" in text
    assert "http://qa/daily-qa/runs/1" in body


def test_stream_output_records_tool_calls_without_written_content():
    """Validates: NFR-SEC-001 — Claude 가 읽고 검색한 것을 감사 기록에 남긴다."""
    from app.modules.daily_qa.agent_runner import parse_output

    lines = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "Viewer", "path": "runs/r1/context"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "runs/r1/context/srs_current.jsonl", "offset": 10, "limit": 40}},
        ]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "본문 내용"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "runs/r1/out/B-001.json", "content": "{\"findings\": []}"}},
        ]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "total_cost_usd": 0.05, "result": "완료"},
    ]
    meta, calls = parse_output("\n".join(json.dumps(line, ensure_ascii=False) for line in lines))
    assert meta["num_turns"] == 3 and meta["tool_calls"] == 3 and "error_text" not in meta
    assert calls[0] == {"tool": "Grep", "pattern": "Viewer", "path": "runs/r1/context"}
    assert calls[1]["file_path"].endswith("srs_current.jsonl") and calls[1]["limit"] == 40
    assert calls[2] == {"tool": "Write", "file_path": "runs/r1/out/B-001.json", "content_chars": 16}
    assert "본문 내용" not in json.dumps(calls, ensure_ascii=False)


def test_usage_limit_is_a_failure_with_its_reason_recorded(tmp_path, monkeypatch):
    """실제로 관찰한 한도 초과 출력: subtype 은 success 인데 is_error 가 참이다 (2026-09-28)."""
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    limit_line = json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                             "result": "You've hit your session limit · resets 7pm"})

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout='{"type":"system","subtype":"init"}\n' + limit_line, stderr="")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
    outcome = ClaudeRunner("claude", "tok", 30).run(Task("B-001", "vxvue-spec-change-impact", {}), run)
    assert not outcome.ok
    assert "session limit" in outcome.error
    log = json.loads((run.run_dir / "logs" / "B-001.claude.json").read_text(encoding="utf-8"))
    assert log["is_error"] is True and log["tool_call_log"] == []
