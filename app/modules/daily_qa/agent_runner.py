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
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread

from app.core.claude_cli import PASSED_ENV, QUIET_ENV, build_env  # noqa: F401  (세 화면 기능의 Claude 호출과 같은 환경 규칙)
from app.core import claude_usage
from app.modules.daily_qa import claude_limits
from app.modules.daily_qa.cancellation import RunCancelled
from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.workspace import ALLOWED_TOOLS, DENIED_TOOLS, RunWorkspace


@dataclass
class RunnerOutcome:
    ok: bool
    returncode: int
    seconds: float
    error: str = ""
    meta: dict = field(default_factory=dict)
    #: 사용량 한도·인증 실패로 끝났으면 그 정보 (REQ-QAINTEL-025). 그 실행의 남은 AI 작업을 멈춘다.
    limit: claude_limits.LimitInfo | None = None
    cancelled: bool = False


def _stop_owned_child(process: subprocess.Popen) -> None:
    """이 실행기가 만든 살아 있는 자식과 그 하위 프로세스만 종료한다."""
    if sys.platform != 'win32':
        # poll/wait 로 부모를 먼저 회수하지 않는다. 부모가 먼저 끝났어도 손자가
        # 출력 파이프를 갖고 있으면 소유한 세션의 PID/그룹을 유지한 채 정리한다.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=2)
        return
    job = getattr(process, '_qa_job', None)
    if job is not None and job.terminate():
        process.wait(timeout=2)
        return
    if process.poll() is not None:
        return
    try:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass  # 트리 조회가 실패해도 아래의 소유 핸들은 정리한다.
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _run_cancellable(command, *, prompt, cwd, env, timeout, check):
    check()
    if sys.platform == 'win32':
        from app.modules.daily_qa.windows_job import CREATE_SUSPENDED, WindowsJob
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED}
    else:
        options = {'start_new_session': True}
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', cwd=cwd, env=env, **options) as process:
        job = None
        writer = None
        cancelled = False
        resumed = sys.platform != 'win32'
        try:
            if sys.platform == 'win32':
                job = WindowsJob(process)
                if not job.attach():
                    raise OSError("Windows Job의 안전 종료 설정 또는 소유 프로세스 배정에 실패해 CLI를 시작하지 않았습니다.")
                process._qa_job = job
                job.resume()
                resumed = True
            started = time.monotonic()
            value = prompt
            if sys.platform == 'win32':
                # Windows communicate의 동기 stdin 쓰기가 막혀도 취소를 계속 확인한다.
                stream, process.stdin = process.stdin, None
                def send_input():
                    try:
                        with stream:
                            stream.write(prompt)
                    except (OSError, ValueError):
                        pass  # 중단으로 자식이 닫은 파이프
                writer = Thread(target=send_input, daemon=True)
                writer.start()
                value = None
            while True:
                check()
                if time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(input=value, timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    value = None
        except RunCancelled:
            cancelled = True
            _stop_owned_child(process)
            stdout, stderr = process.communicate(timeout=2)
        except BaseException:
            if resumed:
                _stop_owned_child(process)
            else:
                # 아직 재개하지 않은 Popen의 원래 핸들만 종료한다. 자식은 생성되기 전이다.
                process.kill()
                process.wait(timeout=2)
            raise
        finally:
            if job is not None:
                job.close()
            if writer is not None and writer.ident is not None:
                writer.join(timeout=2)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), cancelled


#: 작업 입력 전문을 프롬프트에 넣는 최대 글자 수. 넘으면 파일을 읽게 한다 (REQ-DAILY-016 3번).
PROMPT_INLINE_LIMIT = 200_000
#: 작업 폴더에서 더 읽을 수 있는 자료. 있는 것만 프롬프트에 적어 Glob 으로 찾지 않게 한다.
READABLE_FILES = ("rules/qa-guide.md", "rules/instruction-prompt.txt")


def _inlined_documents(task: Task, run: RunWorkspace, inline_input: bool) -> list[tuple[str, str]]:
    """프롬프트에 전문을 넣을 문서 (작업 입력, 결과 형식, 제품 규칙 Skill 참고 문서).

    CLI 는 턴마다 앞의 대화 전체를 다시 읽는다. 매 작업이 같은 파일을 Read 로 나눠 읽던 턴을 없앤다.
    """
    documents = []
    input_path = run.in_dir / f"{task.task_id}.json"
    if inline_input and input_path.is_file():
        text = input_path.read_text(encoding="utf-8")
        if len(text) <= PROMPT_INLINE_LIMIT:
            documents.append((run.rel(input_path), text))
    skills = run.root / ".claude" / "skills"
    references = [skills / task.skill / "SKILL.md", skills / "qa-common-rules" / "SKILL.md",
                  skills / "qa-common-rules" / "references" / "output-contract.md"]
    if run.product_skill:
        references.append(skills / run.product_skill / "SKILL.md")
        references += sorted((skills / run.product_skill / "references").glob("*.md"))
    documents += [(run.rel(path), path.read_text(encoding="utf-8")) for path in references if path.is_file()]
    from app.modules.daily_qa.rule_context import guide_context
    documents += guide_context(task, run)
    return documents


def build_prompt(task: Task, run: RunWorkspace, inline_input: bool = True) -> str:
    """`inline_input=False` 는 결과 재사용 기준(REQ-QAINTEL-037)용이다. 입력 내용은 그쪽이 따로 비교한다."""
    product_rules = f"제품 규칙 Skill `{run.product_skill}` 을 먼저 확인한다. 아래 포함된 내용은 다시 읽지 않는다.\n" if run.product_skill else ""
    readable = [name for name in READABLE_FILES if (run.root / name).is_file()]
    readable.append(run.rel(run.context_dir / "srs_current.jsonl"))
    documents = _inlined_documents(task, run, inline_input)
    inlined = "".join(f"\n<file path=\"{name}\">\n{text}\n</file>\n" for name, text in documents)
    return (
        f"/{task.skill} {run.rel(run.in_dir / f'{task.task_id}.json')}\n\n"
        f"실행 ID: {run.run_id}\n"
        f"작업 ID: {task.task_id}\n"
        f"결과 파일: {run.rel(run.out_dir / f'{task.task_id}.json')}\n"
        f"전체 색인: {run.rel(run.context_dir)}/\n"
        f"{product_rules}"
        f"더 읽을 수 있는 자료: {', '.join(readable)}. 이 밖의 파일을 Glob 으로 찾지 않는다.\n"
        "입력 후보를 먼저 비교한다. 근거가 부족하거나 서로 충돌할 때만 전체 색인을 추가 검색한다.\n"
        "후보 수·본문·근거 검사를 줄이지 않는다. 규칙에서 요구한 검토를 모두 수행한다.\n"
        "후보의 shared_candidate는 입력 shared_candidates의 같은 키 원문을 뜻한다. 그 원문과 각 후보의 match를 합쳐 검토한다. 이 키는 근거 번호가 아니며 원문의 ref·id·location을 인용한다.\n"
        "아래 넣은 Skill·가이드 절은 이미 읽은 것으로 사용한다. Skill/Read 도구로 같은 내용을 다시 부르지 않는다. 조건별로 더 필요한 절이나 누락된 절만 원문에서 추가 확인한다.\n"
        "여러 파일·검색이 필요하면 한 응답에서 함께 부른다. 규칙 원문 읽기와 전체 색인 검색은 두 번 이하의 응답으로 끝낸다.\n"
        "같은 작업에서 이미 읽은 공통 출력 형식은 재사용한다. 결과를 쓰기 전에 필수 필드를 확인한다.\n"
        "결과 파일 하나만 쓰고 끝낸다. 결과 형식은 qa-common-rules 의 references/output-contract.md 를 따른다."
        + (f"\n\n아래 파일 내용은 이미 넣었다. 이 파일들을 다시 읽지 않는다.\n{inlined}" if documents else "")
    )


def build_command(claude_command: str, model: str = "", effort: str = "") -> list[str]:
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
    # 화면 AI 기능과 같은 모델·추론 정도를 쓴다. 비면 CLI 기본값이다 (REQ-DAILY-016 순서 5).
    if model:
        command += ["--model", model]
    if effort:
        command += ["--effort", effort]
    return command


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
    마지막 줄은 `result`. 예전 json 형식(한 덩어리)이 와도 읽는다. 구독 계정이면 사용량 창 정보가
    `rate_limit_event` 로 오고, 마지막 것을 `meta["rate_limit"]` 에 남긴다 (REQ-AICALL-006).
    """
    meta: dict = {}
    calls: list[dict] = []
    rate_limit: dict | None = None
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
        elif event.get("type") == "rate_limit_event" and isinstance(event.get("rate_limit_info"), dict):
            rate_limit = event["rate_limit_info"]
    if not meta and len(events) == 1 and isinstance(events[0], dict) and "subtype" in events[0]:
        meta = _result_meta(events[0])
    if not meta:
        meta = {"parse_error": True}
    meta["tool_calls"] = len(calls)
    if rate_limit is not None:
        meta["rate_limit"] = rate_limit
    return meta, calls


def _safe_meta(stdout: str) -> dict:
    return parse_output(stdout)[0]


class ClaudeRunner:
    def __init__(self, claude_command: str, token: str, timeout_seconds: int, model: str = "", effort: str = "", cancel_check: Callable | None = None) -> None:
        self.claude_command = claude_command
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.effort = effort
        self.cancel_check = cancel_check

    def run(self, task: Task, run: RunWorkspace) -> RunnerOutcome:
        started = time.monotonic()
        log_dir = run.run_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        cancelled = False
        try:
            if self.cancel_check is not None:
                completed, cancelled = _run_cancellable(build_command(self.claude_command, self.model, self.effort),
                    prompt=build_prompt(task, run), cwd=run.root, env=build_env(self.token),
                    timeout=self.timeout_seconds, check=self.cancel_check)
            else:
                completed = subprocess.run(
                    build_command(self.claude_command, self.model, self.effort),
                    input=build_prompt(task, run),
                    cwd=run.root,
                    env=build_env(self.token),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout_seconds,
                )
        except RunCancelled:
            return RunnerOutcome(False, -1, time.monotonic() - started, '사용자 중단', {'cli_started': False}, cancelled=True)
        except subprocess.TimeoutExpired:
            return RunnerOutcome(False, -1, time.monotonic() - started, f"제한 시간 {self.timeout_seconds}초 초과")
        except FileNotFoundError:
            message = f"Claude CLI 를 찾을 수 없습니다: {self.claude_command}"
            limit = claude_limits.LimitInfo(claude_limits.KIND_CLI_MISSING, "", message,
                                            token_fingerprint=claude_limits.token_fingerprint(self.token))
            return RunnerOutcome(False, -1, 0.0, message, limit=limit)
        meta, calls = parse_output(completed.stdout or "")
        if meta.get("rate_limit"):
            # 화면의 사용량 칸이 지금 조회에 실패할 때 보일 값 (REQ-AICALL-006). 저장 실패가 분석을 멈추지 않는다.
            try:
                claude_usage.record_rate_limit_event(meta["rate_limit"])
            except Exception:  # noqa: BLE001
                pass
        log = {**meta, "tool_call_log": calls}
        (log_dir / f"{task.task_id}.claude.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        if cancelled:
            meta['cli_started'] = True
            return RunnerOutcome(False, completed.returncode, time.monotonic() - started, '사용자 중단', meta, cancelled=True)
        error = ""
        if completed.returncode != 0 or meta.get("is_error"):
            # stderr 에 토큰이 찍히는 일은 없지만, 혹시 몰라 토큰 문자열은 지운다.
            tail = (completed.stderr or "")[-600:].replace(self.token, "***") if self.token else (completed.stderr or "")[-600:]
            error = f"exit={completed.returncode} {meta.get('error_text', '')} {tail}".strip()
        ok = completed.returncode == 0 and not meta.get("is_error")
        limit = None if ok else claude_limits.classify(error, token=self.token, run_id=run.run_id)
        return RunnerOutcome(ok, completed.returncode, time.monotonic() - started, error, meta, limit)


class FakeRunner:
    """테스트·`--dry-run` 용. 실제 CLI 대신 `producer(task, run)` 가 결과 파일을 쓴다."""

    def __init__(self, producer: Callable[[Task, RunWorkspace], None] | None = None) -> None:
        self.producer = producer
        self.calls: list[str] = []

    def run(self, task: Task, run: RunWorkspace) -> RunnerOutcome:
        self.calls.append(task.task_id)
        if self.producer is None:
            return RunnerOutcome(False, 0, 0.0, "dry-run: AI 를 부르지 않았습니다")
        produced = self.producer(task, run)
        # 테스트가 실패(예: 사용량 한도)를 흉내 내려면 producer 가 RunnerOutcome 을 돌려준다.
        return produced if isinstance(produced, RunnerOutcome) else RunnerOutcome(True, 0, 0.0)
