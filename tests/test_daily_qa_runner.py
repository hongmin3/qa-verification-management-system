"""Validates: NFR-SEC-001, REQ-DAILY-008, REQ-DAILY-010 (TEST-DAILY-006), REQ-QAINTEL-023, REQ-QAINTEL-025."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.modules.daily_qa import agent_runner, claude_limits, rules
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
    assert all(tool in ("Read(./**)", "Glob(./**)", "Grep(./**)", "Skill") or "runs/**/out/**" in tool for tool in allowed)


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
    product_skills = Path(__file__).resolve().parents[1] / "config" / "products" / "vxvue" / "skills"
    (tmp_path / "ws").mkdir()
    # 개편 전에 작업 폴더 뿌리에 이 모듈이 쓴 CLAUDE.md 는 제품 폴더로 옮기며 지운다.
    (tmp_path / "ws" / "CLAUDE.md").write_text("# 무인 실행 규칙 (VXvue 일일 QA 점검)", encoding="utf-8")
    run = prepare(tmp_path / "ws" / "vxvue", tmp_path / "repo", "20260928-090000", guide, None, "VXvue", product_skills, "vxvue-qa-rules")
    assert not (tmp_path / "ws" / "CLAUDE.md").exists()
    assert "VXvue" in (run.root / "CLAUDE.md").read_text(encoding="utf-8")
    settings = json.loads((run.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["deny"][0] == "Bash"
    assert settings["permissions"]["allow"] == list(ALLOWED_TOOLS)
    skills = {path.parent.name for path in (run.root / ".claude" / "skills").glob("*/SKILL.md")}
    assert {"qa-common-rules", "qa-new-issue-analysis", "qa-fixed-issue-analysis", "qa-spec-decision-analysis",
            "qa-comment-analysis", "qa-spec-coverage-analysis", "qa-manual-completeness", "vxvue-qa-rules"} <= skills
    assert not {"vxvue-spec-change-impact", "vxvue-issue-verification"} & skills
    assert "vxvue-qa-rules" in build_prompt(Task("NEW-001", "qa-new-issue-analysis", {}), run)
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


def test_prompt_prefers_supplied_candidates_but_keeps_evidence_search(tmp_path):
    """Validates: REQ-QAINTEL-011. 근거 탐색을 막지 않고 불필요한 재검색을 줄인다."""
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    task = Task("FIX-001", "qa-fixed-issue-analysis", {"items": [{"target": "VP-100"}]})
    prompt = build_prompt(task, run)
    assert "입력 후보를 먼저 비교" in prompt
    assert "부족하거나 서로 충돌" in prompt
    assert "전체 색인" in prompt and "runs/r1/context" in prompt
    assert "후보 수·본문·근거 검사를 줄이지" in prompt
    assert "이미 읽은 공통 출력 형식은 재사용" in prompt
    assert "runs/r1/out/FIX-001.json" in prompt


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


def test_rules_revision_mismatch_blocks(tmp_path):
    from app.core.product_knowledge import product_dir
    import json
    base = product_dir("Acme", tmp_path)
    folder = base / "original/qa_rules"
    folder.mkdir(parents=True)
    for rev in ("1.12", "1.17"):
        name = f"guide_Rev{rev}.md"
        (folder / name).write_text("# Rules\nContent", encoding="utf-8")
        (base / "manifest.json").write_text(json.dumps({"assets": [{"kind": "qa_rules", "file_name": name}]}), encoding="utf-8")
        state = rules.check("Acme", tmp_path, supported_rev="1.17")
        assert state.found_rev == rev
        assert state.ok == (rev == "1.17")
        assert rules.check("Acme", tmp_path).ok


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
    assert outcome.limit is not None and outcome.limit.kind == claude_limits.KIND_SESSION and outcome.limit.reset_at
    log = json.loads((run.run_dir / "logs" / "B-001.claude.json").read_text(encoding="utf-8"))
    assert log["is_error"] is True and log["tool_call_log"] == []


def test_command_passes_model_and_effort_only_when_set():
    """Validates: REQ-DAILY-016 순서 5 — 일일 QA 작업도 화면 AI 와 같은 모델·추론 정도를 쓴다."""
    command = build_command("claude", "claude-opus-5-5", "medium")
    assert _option_values(command, "--model") == ["claude-opus-5-5"]
    assert _option_values(command, "--effort") == ["medium"]
    bare = build_command("claude")
    assert "--model" not in bare and "--effort" not in bare


def test_settings_read_shared_claude_model_and_effort(tmp_path):
    """Validates: REQ-DAILY-016 순서 5 — 모델은 ai.claude.models.standard, 추론 정도는 ai.claude.effort."""
    from app.modules.daily_qa.settings import load

    class FakeSettings:
        root = Path(__file__).resolve().parents[1]
        secrets = type("S", (), {"__getattr__": lambda self, name: ""})()
        values = {"ai.claude.models.standard": "claude-opus-5-5", "ai.claude.effort": "medium",
                  "ai.claude.models.light": "claude-sonnet-5-5"}

        def get(self, key, default=None):
            return self.values.get(key, default)

    cfg = load(FakeSettings())
    assert (cfg.claude_model, cfg.claude_effort) == ("claude-opus-5-5", "medium")
    assert cfg.intelligence.audit_model == "claude-sonnet-5-5"


def test_runner_sends_model_and_effort(tmp_path, monkeypatch):
    """Validates: REQ-DAILY-016 순서 5 — 실행기가 받은 모델·추론 정도를 CLI 에 넘긴다."""
    seen: list = []

    def fake_run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    monkeypatch.setattr(agent_runner.subprocess, "run", fake_run)
    run = prepare(tmp_path / "ws", tmp_path / "repo", "r1", None, None)
    ClaudeRunner("claude", "", 5, model="claude-opus-5-5", effort="medium").run(Task("B-001", "qa-spec-change-summary", {}), run)
    assert seen and _option_values(seen[0], "--model") == ["claude-opus-5-5"]
    assert _option_values(seen[0], "--effort") == ["medium"]
