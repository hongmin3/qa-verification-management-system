"""Claude Code CLI 실행기 (SPEC NFR-SEC-001).

`claude -p` 를 작업 폴더에서 한 작업씩 부른다. 프롬프트는 표준입력으로 넘긴다
(`--allowedTools` 같은 가변 인자 옵션이 뒤따르는 위치 인자를 삼키지 않게 하기 위함).

- `--permission-mode dontAsk`: 허용 목록에 없는 도구는 묻지 않고 거부한다.
- `--setting-sources project`: 사용자 홈의 설정·hook 을 읽지 않고 작업 폴더 설정만 쓴다.
- `--strict-mcp-config`: MCP 서버를 하나도 붙이지 않는다.
- `--no-session-persistence`: 대화 기록을 디스크에 남기지 않는다.

`--bare` 는 쓰지 않는다. bare 모드는 `CLAUDE_CODE_OAUTH_TOKEN` 을 읽지 않아 Team 계정
토큰으로 인증할 수 없다.

프로세스 환경은 필요한 값만 새로 만든다. 서버의 다른 비밀값(Gemini 키, SMTP 암호 등)은
넘기지 않는다.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.workspace import ALLOWED_TOOLS, DENIED_TOOLS, RunWorkspace

PASSED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "USERPROFILE", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
              "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS")
QUIET_ENV = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
}


@dataclass
class RunnerOutcome:
    ok: bool
    returncode: int
    seconds: float
    error: str = ""
    meta: dict = field(default_factory=dict)


def build_prompt(task: Task, run: RunWorkspace) -> str:
    return (
        f"/{task.skill} {run.rel(run.in_dir / f'{task.task_id}.json')}\n\n"
        f"실행 ID: {run.run_id}\n"
        f"작업 ID: {task.task_id}\n"
        f"결과 파일: {run.rel(run.out_dir / f'{task.task_id}.json')}\n"
        f"전체 색인: {run.rel(run.context_dir)}/\n"
        "결과 파일 하나만 쓰고 끝낸다. 결과 형식은 vxvue-qa-rules 의 references/output-contract.md 를 따른다."
    )


def build_command(claude_command: str, model: str = "") -> list[str]:
    # stream-json(+ --verbose)은 도구 호출을 한 줄씩 내보낸다. 무엇을 읽고 검색했는지 감사
    # 기록에 남기려면 이 형식이어야 한다 (json 형식은 최종 결과 한 덩어리만 준다).
    command = [
        claude_command,
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "dontAsk",
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--allowedTools", *ALLOWED_TOOLS,
        "--disallowedTools", *DENIED_TOOLS,
    ]
    if model:
        command += ["--model", model]
    return command


def build_env(token: str, base: dict[str, str] | None = None) -> dict[str, str]:
    source = base if base is not None else os.environ
    env = {name: source[name] for name in PASSED_ENV if name in source}
    env.update(QUIET_ENV)
    # 빈 토큰을 넣으면 CLI 가 그 값으로 인증을 시도하다 실패한다. 없으면 넣지 않는다
    # (운영에서는 사전 점검이 토큰 없는 실행을 막고, 개발 PC 는 기존 로그인을 쓴다).
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


META_KEYS = ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd", "usage", "session_id", "permission_denials")
#: 도구 입력 가운데 감사 기록에 남길 키. 파일 경로와 검색어만 남기고, 쓴 내용(content)은
#: 결과 파일에 있으므로 길이만 남긴다.
TOOL_INPUT_KEYS = ("file_path", "path", "pattern", "glob", "skill", "offset", "limit")


def _tool_call(block: dict) -> dict:
    tool_input = block.get("input") or {}
    call = {"tool": block.get("name", ""), **{key: tool_input[key] for key in TOOL_INPUT_KEYS if key in tool_input}}
    if "content" in tool_input:
        call["content_chars"] = len(str(tool_input["content"]))
    return call


def _result_meta(result: dict) -> dict:
    meta = {key: result.get(key) for key in META_KEYS if key in result}
    if result.get("is_error"):
        # 사용량 한도 초과처럼 실패 이유가 결과 문장에만 있다. 응답 본문이 아니라 오류 문장이다.
        meta["error_text"] = str(result.get("result") or "")[:300]
    return meta


def parse_output(stdout: str) -> tuple[dict, list[dict]]:
    """CLI 출력에서 감사 기록에 남길 값(`meta`)과 도구 호출 목록을 고른다.

    stream-json 은 한 줄에 사건 하나다: 도구 호출은 `assistant` 메시지의 `tool_use` 블록,
    마지막 줄은 `result`. 예전 json 형식(한 덩어리)이 와도 읽는다.
    """
    meta: dict = {}
    calls: list[dict] = []
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append(_tool_call(block))
        elif event.get("type") == "result":
            meta = _result_meta(event)
    if not meta and len(events) == 1 and isinstance(events[0], dict) and "subtype" in events[0]:
        meta = _result_meta(events[0])
    if not meta:
        meta = {"parse_error": True}
    meta["tool_calls"] = len(calls)
    return meta, calls


def _safe_meta(stdout: str) -> dict:
    return parse_output(stdout)[0]


class ClaudeRunner:
    def __init__(self, claude_command: str, token: str, timeout_seconds: int, model: str = "") -> None:
        self.claude_command = claude_command
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.model = model

    def run(self, task: Task, run: RunWorkspace) -> RunnerOutcome:
        started = time.monotonic()
        log_dir = run.run_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        try:
            completed = subprocess.run(
                build_command(self.claude_command, self.model),
                input=build_prompt(task, run),
                cwd=run.root,
                env=build_env(self.token),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return RunnerOutcome(False, -1, time.monotonic() - started, f"제한 시간 {self.timeout_seconds}초 초과")
        except FileNotFoundError:
            return RunnerOutcome(False, -1, 0.0, f"Claude CLI 를 찾을 수 없습니다: {self.claude_command}")
        meta, calls = parse_output(completed.stdout or "")
        log = {**meta, "tool_call_log": calls}
        (log_dir / f"{task.task_id}.claude.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        error = ""
        if completed.returncode != 0 or meta.get("is_error"):
            # stderr 에 토큰이 찍히는 일은 없지만, 혹시 몰라 토큰 문자열은 지운다.
            tail = (completed.stderr or "")[-600:].replace(self.token, "***") if self.token else (completed.stderr or "")[-600:]
            error = f"exit={completed.returncode} {meta.get('error_text', '')} {tail}".strip()
        return RunnerOutcome(completed.returncode == 0 and not meta.get("is_error"), completed.returncode,
                             time.monotonic() - started, error, meta)


class FakeRunner:
    """테스트·`--dry-run` 용. 실제 CLI 대신 `producer(task, run)` 가 결과 파일을 쓴다."""

    def __init__(self, producer: Callable[[Task, RunWorkspace], None] | None = None) -> None:
        self.producer = producer
        self.calls: list[str] = []

    def run(self, task: Task, run: RunWorkspace) -> RunnerOutcome:
        self.calls.append(task.task_id)
        if self.producer is None:
            return RunnerOutcome(False, 0, 0.0, "dry-run: AI 를 부르지 않았습니다")
        self.producer(task, run)
        return RunnerOutcome(True, 0, 0.0)
