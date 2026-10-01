"""세 화면 기능의 AI 판정을 Claude CLI 로 받는 통로 (SPEC REQ-AICALL-005).

실제 CLI 는 부르지 않는다. `runner` 자리에 명령·표준입력·환경·폴더를 기록하는 가짜를 넣는다.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core import claude_cli
from app.core import gemini_client as gemini_module
from app.core import model_router
from app.core.gemini_client import GeminiClient
from app.core.notifier import KIND_CLAUDE_AUTH_FAILED, KIND_CLAUDE_USAGE_LIMIT, KIND_QUOTA_EXHAUSTED, classify_error
from app.core.storage import Storage

PROMPT = "manual_revision_quick"
TOKEN = "sk-ant-oat01-test-token-value"


class _Answer(BaseModel):
    decision: str
    confidence: float = 0.0


class FakeRunner:
    """`subprocess.run` 대신 불린다. 받은 값을 남기고 정해 둔 출력을 돌려준다."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "", raise_exc: Exception | None = None) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def __call__(self, command, *, input, env, cwd, timeout):
        self.calls.append({"command": list(command), "input": input, "env": dict(env), "cwd": Path(cwd), "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def _result(**extra) -> str:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "structured_output": {"decision": "PASS", "confidence": 0.9},
        "usage": {"input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, "output_tokens": 40},
        "total_cost_usd": 0.01,
    }
    payload.update(extra)
    return json.dumps(payload)


@pytest.fixture(autouse=True)
def _no_notifications(monkeypatch):
    monkeypatch.setattr(gemini_module, "notify_for_error", lambda *args, **kwargs: None)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """저장소 밖이고 상위에 CLAUDE.md 가 없는 폴더. 검사 대상 저장소는 가짜로 둔다."""
    folder = tmp_path / "ai-work"
    monkeypatch.setattr(claude_cli, "REPO_ROOT", tmp_path / "repo")
    return folder


def _client(tmp_path, monkeypatch, runner, workdir, *, provider="claude_cli", token="", **claude) -> GeminiClient:
    client = GeminiClient(storage=Storage(tmp_path / "t.db"), claude_runner=runner)
    ai = {"provider": provider, "claude": {"command": "claude", "timeout_seconds": 77, "workdir": str(workdir), **claude}}
    monkeypatch.setitem(client.settings.raw, "ai", ai)
    monkeypatch.setattr(client.settings.secrets, "claude_code_oauth_token", token)
    monkeypatch.setattr(client.settings.secrets, "gemini_api_key", "gemini-secret-value")
    monkeypatch.setattr(client.settings.secrets, "smtp_password", "smtp-secret-value")
    return client


def _option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


# Validates: REQ-AICALL-005, REQ-AICALL-001
def test_claude_provider_runs_cli_with_isolated_arguments(tmp_path, monkeypatch, workdir):
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir, effort="low")

    raw = client.generate_structured('{"change": "로그인"}', prompt_name=PROMPT, response_schema=_Answer, system_suffix="규칙 발췌", model="claude-sonnet-5-5")

    assert raw["decision"] == "PASS" and raw["confidence"] == 0.9
    call = runner.calls[0]
    command = call["command"]
    assert command[0] == "claude" and "-p" in command
    assert _option(command, "--output-format") == "json"
    assert _option(command, "--tools") == ""
    assert _option(command, "--permission-mode") == "dontAsk"
    assert _option(command, "--setting-sources") == ""
    assert "--strict-mcp-config" in command and "--no-session-persistence" in command
    assert _option(command, "--model") == "claude-sonnet-5-5"
    assert _option(command, "--effort") == "low"
    assert json.loads(_option(command, "--json-schema")) == _Answer.model_json_schema()
    system_prompt = _option(command, "--system-prompt")
    assert system_prompt.endswith("규칙 발췌") and len(system_prompt) > len("규칙 발췌")
    assert "--bare" not in command
    # 본문은 인자가 아니라 표준입력으로 간다. 보낸 값은 감사 화면용으로 남는다.
    assert call["input"] == client.last_sent_prompt
    assert call["cwd"] == workdir.resolve() and workdir.is_dir()
    assert call["timeout"] == 77
    assert client.last_provider == "claude_cli" and client.last_model == "claude-sonnet-5-5"


# Validates: REQ-AICALL-005, NFR-SEC-001
def test_claude_env_carries_only_needed_values(tmp_path, monkeypatch, workdir):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-value")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret-value")
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir, token=TOKEN)
    client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)

    env = runner.calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert env["DISABLE_TELEMETRY"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    joined = json.dumps(env)
    assert "gemini-secret-value" not in joined and "smtp-secret-value" not in joined
    assert "GEMINI_API_KEY" not in env

    runner2 = FakeRunner(_result())
    client2 = _client(tmp_path, monkeypatch, runner2, workdir, token="")
    client2.generate_structured("다른 본문", prompt_name=PROMPT, response_schema=_Answer)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in runner2.calls[0]["env"]


# Validates: REQ-AICALL-005, REQ-USAGE-001
def test_claude_usage_becomes_token_usage(tmp_path, monkeypatch, workdir):
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir)
    raw = client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)

    assert raw["token_usage"] == {"prompt_tokens": 150, "candidate_tokens": 40, "total_tokens": 190}
    assert client.token_usage["total_tokens"] == 190


# Validates: REQ-AICALL-005
def test_result_text_is_read_when_structured_output_is_missing(tmp_path, monkeypatch, workdir):
    stdout = _result(structured_output=None, result='```json\n{"decision": "FAIL", "confidence": 0.4}\n```')
    client = _client(tmp_path, monkeypatch, FakeRunner(stdout), workdir)
    raw = client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    assert raw["decision"] == "FAIL"


# Validates: REQ-AICALL-005, REQ-AICALL-003
@pytest.mark.parametrize(
    ("runner", "expected"),
    [
        (FakeRunner(_result(is_error=True, result="You've hit your session limit · resets 1:50pm", structured_output=None), returncode=1), "session limit"),
        (FakeRunner(raise_exc=subprocess.TimeoutExpired("claude", 77)), "제한 시간 77초 초과"),
        (FakeRunner(raise_exc=FileNotFoundError("claude")), "실행 파일을 찾을 수 없습니다"),
        (FakeRunner(_result(structured_output=None, result="판정할 수 없습니다")), "응답 JSON 을 읽지 못했습니다"),
        (FakeRunner("not json at all"), "응답 JSON 을 읽지 못했습니다"),
    ],
)
def test_cli_failures_raise_marked_errors_without_retry(tmp_path, monkeypatch, workdir, runner, expected):
    client = _client(tmp_path, monkeypatch, runner, workdir)
    with pytest.raises(RuntimeError) as excinfo:
        client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    message = str(excinfo.value)
    assert message.startswith("Claude CLI 오류:") and expected in message
    assert len(runner.calls) == 1  # 다시 시도하지 않는다


# Validates: REQ-AICALL-005
def test_token_is_removed_from_error_text(tmp_path, monkeypatch, workdir):
    runner = FakeRunner("", returncode=1, stderr=f"auth failed for token {TOKEN}")
    client = _client(tmp_path, monkeypatch, runner, workdir, token=TOKEN)
    with pytest.raises(RuntimeError) as excinfo:
        client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    assert TOKEN not in str(excinfo.value) and "***" in str(excinfo.value)


# Validates: REQ-AICALL-005, REQ-AICALL-002
def test_cache_hit_does_not_call_cli(tmp_path, monkeypatch, workdir):
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir)
    client.generate_structured("같은 본문", prompt_name=PROMPT, response_schema=_Answer)
    client.generate_structured("같은 본문", prompt_name=PROMPT, response_schema=_Answer)
    assert len(runner.calls) == 1 and client.last_cache_hit


# Validates: REQ-AICALL-005, REQ-AICALL-001
def test_gemini_provider_does_not_call_cli(tmp_path, monkeypatch, workdir):
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir, provider="gemini")
    monkeypatch.setattr(client.settings.secrets, "gemini_api_key", "")
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    assert runner.calls == [] and client.last_provider == "gemini"


# Validates: REQ-AICALL-005
def test_unknown_provider_falls_back_to_claude(tmp_path, monkeypatch, workdir):
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, workdir, provider="openai")
    client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    assert len(runner.calls) == 1 and client.last_provider == "claude_cli"


# Validates: REQ-AICALL-005, NFR-SEC-001
def test_workdir_inside_repository_is_refused(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(claude_cli, "REPO_ROOT", repo)
    runner = FakeRunner(_result())
    client = _client(tmp_path, monkeypatch, runner, repo / "inside")
    with pytest.raises(RuntimeError, match="저장소 안"):
        client.generate_structured("본문", prompt_name=PROMPT, response_schema=_Answer)
    assert runner.calls == []

    parent = tmp_path / "with-agent-doc"
    parent.mkdir()
    (parent / "CLAUDE.md").write_text("개발용 지침", encoding="utf-8")
    client = _client(tmp_path, monkeypatch, runner, parent / "work")
    with pytest.raises(RuntimeError, match="CLAUDE.md"):
        client.generate_structured("본문2", prompt_name=PROMPT, response_schema=_Answer)
    assert runner.calls == []


# Validates: REQ-AICALL-004, REQ-AICALL-005
def test_model_tier_reads_provider_specific_names(monkeypatch):
    settings = model_router.get_settings()
    monkeypatch.setitem(settings.raw, "ai", {"provider": "claude_cli", "claude": {"models": {"complex": "claude-fable-5-1"}}})
    assert model_router.model_for(model_router.TIER_COMPLEX) == "claude-fable-5-1"
    assert model_router.model_for(model_router.TIER_STANDARD) == model_router.DEFAULT_CLAUDE_MODELS[model_router.TIER_STANDARD]

    monkeypatch.setitem(settings.raw, "ai", {"provider": "gemini"})
    assert model_router.model_for(model_router.TIER_STANDARD) == str(settings.get("models.standard") or model_router.DEFAULT_MODELS["standard"])


# Validates: REQ-USAGE-004
def test_claude_models_have_default_prices():
    assert model_router.DEFAULT_PRICING["claude-opus-5-5"] == {"input": 4.0, "output": 20.0}
    assert model_router.DEFAULT_PRICING["claude-sonnet-5-5"] == {"input": 2.0, "output": 10.0}
    assert model_router.estimate_cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == 24.0


# Validates: REQ-USAGE-003
def test_claude_errors_get_their_own_notification_kinds():
    assert classify_error("Claude CLI 오류: You've hit your session limit · resets 1:50pm") == KIND_CLAUDE_USAGE_LIMIT
    assert classify_error("Claude CLI 오류: exit=1 api_error_status 429") == KIND_CLAUDE_USAGE_LIMIT
    assert classify_error("Claude CLI 오류: Not logged in · Please run /login") == KIND_CLAUDE_AUTH_FAILED
    assert classify_error("Claude CLI 오류: 제한 시간 600초 초과") is None
    # Claude 머리말이 없으면 예전처럼 Gemini 종류로 본다.
    assert classify_error("429 RESOURCE_EXHAUSTED") == KIND_QUOTA_EXHAUSTED


# Validates: REQ-WEB-004, REQ-AICALL-005
def test_config_status_reports_ai_provider_without_secret_values(monkeypatch):
    from app.core.config import get_settings
    from app.main import app

    settings = get_settings()
    monkeypatch.setitem(settings.raw, "ai", {"provider": "claude_cli", "claude": {"command": "definitely-missing-claude-cli"}})
    monkeypatch.setattr(settings.secrets, "claude_code_oauth_token", TOKEN)
    body = TestClient(app).get("/config/status").json()

    status = body["ai_provider"]
    assert status["provider"] == "claude_cli"
    assert status["claude_command"] == "definitely-missing-claude-cli"
    assert status["claude_command_found"] is False and status["ready"] is False
    assert status["claude_token_configured"] is True
    assert status["model"]
    assert TOKEN not in json.dumps(body)
