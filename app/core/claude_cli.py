"""Claude Code CLI(`claude -p`) 로 구조화 판정 한 번 받기 (SPEC REQ-AICALL-005).

Regression 영향 분석·QA Agent·매뉴얼 개정 검증의 AI 호출은 `app/core/gemini_client.py` 한
통로를 지난다. `ai.provider` 가 `claude_cli` 이면 그 통로가 이 모듈을 부른다. 가리기·응답
저장본·토큰 합산은 통로가 하고, 여기서는 CLI 를 안전하게 한 번 실행하고 결과를 읽기만 한다.

격리 조건은 일일 QA 점검(NFR-SEC-001)과 같은 기준이되 더 좁다.

- 도구를 하나도 주지 않는다(`--tools ""`). 판정 근거는 표준입력의 분석 입력에 다 있다.
- 저장소 밖 빈 폴더에서 실행하고, 상위 폴더에 개발용 지침(`CLAUDE.md` 등)이 있으면 거부한다.
  저장소 안에서 부르면 개발용 지침(커밋·푸시 규칙)이 판정에 섞인다.
- 사용자·프로젝트 설정과 hook(`--setting-sources ""`), MCP(`--strict-mcp-config`), 대화 기록
  (`--no-session-persistence`)을 쓰지 않는다.
- 프로세스 환경은 필요한 값만 새로 만든다. Gemini 키·SMTP 암호·Polarion 토큰은 넘기지 않는다.

`--bare` 는 쓰지 않는다. bare 모드는 `CLAUDE_CODE_OAUTH_TOKEN` 을 읽지 않아 회사 Team 계정
토큰으로 인증할 수 없다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Claude CLI 오류는 모두 이 머리말로 시작한다. 알림(app/core/notifier.py)이 이 머리말로
#: Claude 오류와 Gemini 오류를 가른다.
ERROR_PREFIX = "Claude CLI 오류: "

DEFAULT_COMMAND = "claude"
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_WORKDIR = "~/.qa-ai-workspace"

AGENT_DOC_NAMES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md")

PASSED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "USERPROFILE", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
              "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS")
QUIET_ENV = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
}

#: `runner(command, input=..., env=..., cwd=..., timeout=...) -> CompletedProcess`. 테스트가 바꿔 끼운다.
Runner = Callable[..., subprocess.CompletedProcess]


class ClaudeCliError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(ERROR_PREFIX + detail)


def build_env(token: str, base: dict[str, str] | None = None) -> dict[str, str]:
    source = base if base is not None else os.environ
    env = {name: source[name] for name in PASSED_ENV if name in source}
    env.update(QUIET_ENV)
    # 빈 토큰을 넣으면 CLI 가 그 값으로 인증을 시도하다 실패한다. 없으면 넣지 않는다
    # (서버는 토큰으로, 개발 PC 는 기존 로그인으로 인증한다).
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def validate_workdir(workdir: Path, repo_root: Path | None = None) -> Path:
    """빈 작업 폴더 위치 검사. 통과하면 절대 경로를 돌려준다 (NFR-SEC-001 과 같은 조건)."""
    resolved = workdir.expanduser().resolve()
    repo = (repo_root or REPO_ROOT).resolve()
    if resolved == repo or repo in resolved.parents:
        raise ClaudeCliError(f"작업 폴더가 저장소 안에 있습니다: {resolved}. ai.claude.workdir 를 저장소 밖으로 지정하세요.")
    if resolved in repo.parents:
        raise ClaudeCliError(f"작업 폴더가 저장소를 포함합니다: {resolved}")
    for parent in (resolved, *resolved.parents):
        for name in AGENT_DOC_NAMES:
            if (parent / name).is_file():
                raise ClaudeCliError(f"작업 폴더 또는 그 상위 폴더에 {parent / name} 가 있어 판정에 섞여 들어갑니다.")
    return resolved


def build_command(command: str, *, system_prompt: str, schema: dict, model: str, effort: str = "") -> list[str]:
    args = [
        command,
        "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        "--system-prompt", system_prompt,
        "--tools", "",
        "--permission-mode", "dontAsk",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args


def usage_to_tokens(usage: dict | None) -> dict[str, int]:
    usage = usage or {}
    prompt_tokens = sum(int(usage.get(key, 0) or 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    return {"prompt_tokens": prompt_tokens, "candidate_tokens": output_tokens, "total_tokens": prompt_tokens + output_tokens}


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _json_from_text(text: str) -> dict | None:
    text = (text or "").strip()
    match = _FENCE.match(text)
    if match:
        text = match.group(1)
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def parse_result(stdout: str) -> tuple[dict, dict]:
    """CLI 의 `--output-format json` 출력 → (결과 덩어리, 응답 JSON). 응답을 못 읽으면 응답은 빈 dict."""
    result = _json_from_text(stdout) or {}
    structured = result.get("structured_output")
    if isinstance(structured, dict):
        return result, structured
    return result, _json_from_text(str(result.get("result") or "")) or {}


def _default_runner(command, *, input, env, cwd, timeout):
    return subprocess.run(command, input=input, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def request_structured(prompt: str, *, system_prompt: str, schema: dict, model: str, settings, runner: Runner | None = None) -> dict:
    """`claude -p` 를 한 번 실행하고 응답 JSON 에 `token_usage` 를 붙여 돌려준다. 실패하면 ClaudeCliError."""
    command = str(settings.get("ai.claude.command", DEFAULT_COMMAND) or DEFAULT_COMMAND)
    timeout = int(settings.get("ai.claude.timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    effort = str(settings.get("ai.claude.effort", "") or "")
    token = str(settings.secrets.claude_code_oauth_token or "")
    workdir = validate_workdir(Path(str(settings.get("ai.claude.workdir", "") or DEFAULT_WORKDIR)))
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        completed = (runner or _default_runner)(
            build_command(command, system_prompt=system_prompt, schema=schema, model=model, effort=effort),
            input=prompt,
            env=build_env(token),
            cwd=workdir,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"제한 시간 {timeout}초 초과") from exc
    except FileNotFoundError as exc:
        raise ClaudeCliError(f"실행 파일을 찾을 수 없습니다: {command}") from exc

    result, payload = parse_result(completed.stdout or "")
    if completed.returncode != 0 or result.get("is_error"):
        # 사용량 한도처럼 실패 이유가 결과 문장(`result`)에만 있는 경우가 있다.
        reason = str(result.get("result") or "")[:300]
        status = result.get("api_error_status")
        tail = (completed.stderr or "")[-600:]
        detail = " ".join(part for part in (f"exit={completed.returncode}", f"api_error_status={status}" if status else "", reason, tail) if part)
        raise ClaudeCliError(_redact(detail.strip(), token))
    if not payload:
        raise ClaudeCliError("응답 JSON 을 읽지 못했습니다" + (f" (결과 문장: {str(result.get('result') or '')[:200]})" if result.get("result") else ""))
    payload = dict(payload)
    payload["token_usage"] = usage_to_tokens(result.get("usage"))
    return payload


def login_status(command: str = DEFAULT_COMMAND, timeout: int = 20, runner: Runner | None = None) -> dict:
    """이 PC 의 Claude CLI 가 로그인돼 있는가 (`claude auth status`, 모델을 부르지 않는다).

    토큰(`CLAUDE_CODE_OAUTH_TOKEN`)이 없는 PC 에서 기존 로그인으로 부를 수 있는지 볼 때 쓴다
    (SPEC REQ-DAILY-001 4번). 이메일·조직 번호는 돌려주지 않는다. 기록에 남기지 않기 위해서다.
    """
    try:
        completed = (runner or _default_runner)([command, "auth", "status"], input="", env=build_env(""), cwd=None, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"logged_in": False, "method": "", "subscription": "", "error": type(exc).__name__}
    text = completed.stdout or ""
    data = _json_from_text(text[text.find("{"):]) if "{" in text else None
    if not isinstance(data, dict):
        return {"logged_in": False, "method": "", "subscription": "", "error": f"exit={completed.returncode}"}
    return {"logged_in": bool(data.get("loggedIn")), "method": str(data.get("authMethod") or ""),
            "subscription": str(data.get("subscriptionType") or ""), "error": ""}


def status(settings) -> dict:
    """`/config/status` 용. 명령을 찾았는지와 토큰이 있는지만 알리고 값은 넣지 않는다."""
    command = str(settings.get("ai.claude.command", DEFAULT_COMMAND) or DEFAULT_COMMAND)
    return {
        "claude_command": command,
        "claude_command_found": shutil.which(command) is not None,
        "claude_token_configured": bool(settings.secrets.claude_code_oauth_token),
    }
