"""Dedicated restricted Claude CLI runner; shared QA calls stay unchanged."""
from pathlib import Path
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from app.core import claude_cli
from app.core.prompt_manager import load_prompt
from app.core.security_filter import mask_text


def build_command(command, prompt_file, mcp_config, *, model, effort, max_turns=24, max_budget_usd=0):
    args = [command, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--system-prompt-file", str(prompt_file), "--tools", "", "--allowedTools", "mcp__vxvue_knowledge__*",
            "--permission-mode", "dontAsk", "--setting-sources", "", "--strict-mcp-config", "--mcp-config", str(mcp_config), "--no-session-persistence"]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if max_turns > 0:
        args += ["--max-turns", str(max_turns)]
    if max_budget_usd > 0:
        args += ["--max-budget-usd", str(max_budget_usd)]
    return args


def usage_metrics(result, tool_calls=0):
    usage = result.get("usage") or {}
    value = {k: int(usage.get(k, 0) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    denominator = sum(value[k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    value.update(usage_reported=bool(usage), cache_creation=usage.get("cache_creation") or {}, cache_read_ratio=round(value["cache_read_input_tokens"] / denominator, 4) if denominator else 0,
                 num_turns=result.get("num_turns"), tool_calls=tool_calls, duration_ms=result.get("duration_ms"),
                 estimated_cost_usd=result.get("total_cost_usd"), cost_basis="cli_estimate",
                 result_subtype=result.get("subtype"), api_error_status=result.get("api_error_status"))
    return value


def cli_failure(message, result, tool_calls, model):
    failure = RuntimeError(message)
    failure.metrics = usage_metrics(result, tool_calls)
    failure.usage = claude_cli.usage_to_tokens(result.get("usage"))
    failure.model = model
    return failure


def failure_message(result):
    reason = str(result.get("result") or "").lower()
    if any(marker in reason for marker in ("hit your limit", "usage limit", "out of usage", "out of extra usage")):
        return "이번 실행에 로그인된 Claude 계정의 사용량 한도에 도달했습니다. 해당 계정의 사용량과 초기화 시각을 확인해주세요."
    if result.get("api_error_status") == 429 or any(marker in reason for marker in ("rate_limit", "rate limit", "too many requests")):
        return "Claude의 일시적인 호출 제한 응답을 받았습니다. 사용량 소진과 별도로 서버 연결·호출 빈도를 확인해주세요."
    if result.get("api_error_status") == 529 or "overloaded" in reason:
        return "Claude 서버가 일시적으로 혼잡합니다. 잠시 후 다시 요청해주세요."
    return "Claude CLI 호출에 실패했습니다. 서버 인증과 연결 상태를 확인해주세요."


def event_update(event):
    if event.get("type") == "stream_event":
        delta = event.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            return "답변 작성", delta.get("text", "")
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                return ("원문 확인" if any(k in block.get("name", "") for k in ("get_document", "read_attachment")) else "자료 검색"), ""
    return "", ""


def history_prompt(messages, max_chars=120000):
    picked, size = [], 0
    for message in reversed(messages):
        if not message["content"] and not message.get("attachments"):
            continue
        value = {"role": message["role"], "content": message["content"], "attachments": [{"id": a["id"], "name": a["name"]} for a in message.get("attachments", [])]}
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if picked and size + len(encoded) > max_chars:
            break
        picked.append(value)
        size += len(encoded)
    omitted = sum(bool(m["content"] or m.get("attachments")) for m in messages) - len(picked)
    return json.dumps({"conversation": list(reversed(picked)), "omitted_older_messages": omitted}, ensure_ascii=False, separators=(",", ":")), omitted


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def run(messages, snapshot, attachments, job_id, settings, cancel, progress):
    base = claude_cli.validate_workdir(Path(str(settings.get("ai.claude.workdir") or claude_cli.DEFAULT_WORKDIR)))
    execution_dir = claude_cli.validate_workdir(base / "vxvue-chatbot")
    workdir = execution_dir / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    module = Path(__file__).parent
    system = load_prompt("vxvue_chatbot").system_instruction
    system += "\n\n" + (module / "skill" / "SKILL.md").read_text(encoding="utf-8")
    if snapshot.get("skill_text"):
        system += "\n\n" + snapshot["skill_text"]
    else:
        system += "\n\n" + (module / "skill" / "references" / "qa-work-principles.md").read_text(encoding="utf-8")
        system += "\n\n" + (module / "skill" / "references" / "generator-integration.md").read_text(encoding="utf-8")
    system += "\n\n# 서버의 최신 지침 프롬프트·QA 규칙 전문\n" + snapshot["rules"]
    prompt_file, context_file, config_file = (workdir / name for name in ("system.txt", "context.json", "mcp.json"))
    system, system_mask = mask_text(system)
    prompt_file.write_text(system, encoding="utf-8")
    context_file.write_text(json.dumps({"snapshot": {k: v for k, v in snapshot.items() if k not in ("rules", "skill_text")}, "attachments": attachments, "job_id": job_id}, ensure_ascii=False), encoding="utf-8")
    trace = workdir / "references.jsonl"
    config_file.write_text(json.dumps({"mcpServers": {"vxvue_knowledge": {"command": sys.executable, "args": ["-X", "utf8", str(module / "mcp_server.py"), "--context", str(context_file), "--trace", str(trace)]}}}), encoding="utf-8")
    prompt, omitted = history_prompt(messages, int(settings.get("vxvue_chatbot.history_char_limit", 120000)))
    prompt = prompt[:-1] + ',"source_failures":' + json.dumps(snapshot.get("failures", []), ensure_ascii=False, separators=(",", ":")) + "}"
    if snapshot.get("skill_metadata"):
        prompt = prompt[:-1] + ',"skill_code_review_needed":' + json.dumps(snapshot["skill_metadata"]["code_hash"] != settings.get("vxvue_chatbot.reviewed_skill_code_hash", "")) + "}"
    prompt, history_mask = mask_text(prompt)
    (workdir / "input.json").write_text(prompt, encoding="utf-8")
    token = settings.secrets.claude_code_oauth_token or ""
    model = str(settings.get("vxvue_chatbot.model") or settings.get("ai.claude.models.standard") or "")
    effort = str(settings.get("ai.claude.effort", "medium"))
    command = claude_cli.resolve_command(str(settings.get("ai.claude.command", "claude")))
    args = build_command(command, prompt_file, config_file, model=model, effort=effort,
                         max_turns=int(settings.get("vxvue_chatbot.max_turns", 24)), max_budget_usd=float(settings.get("vxvue_chatbot.max_budget_usd", 0)))
    env = claude_cli.build_env(token)
    ttl = settings.get("vxvue_chatbot.prompt_cache_ttl", "")
    if ttl in ("5m", "1h"):
        env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = ttl
    timeout = int(settings.get("vxvue_chatbot.timeout_seconds", 900))
    events = queue.Queue()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    prefix = f"이전 메시지 {omitted}개는 이번 답변의 입력에서 제외되었습니다.\n\n" if omitted else ""
    answer, result, tool_calls = "", {}, 0
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", cwd=execution_dir, env=env, **options)
    def reader():
        try:
            for line in process.stdout:
                events.put(line)
        finally:
            events.put(None)
    threading.Thread(target=reader, daemon=True).start()
    def writer():
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass
    sender = threading.Thread(target=writer, daemon=True)
    sender.start()
    try:
        start = time.monotonic()
        while True:
            if cancel.is_set():
                raise InterruptedError("답변 생성을 중지했습니다.")
            if time.monotonic() - start > timeout:
                raise TimeoutError("답변 제한 시간을 넘었습니다. 질문 범위를 줄여 다시 요청해주세요.")
            try:
                line = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                result = event
            if event.get("type") == "assistant":
                tool_calls += sum(block.get("type") == "tool_use" for block in event.get("message", {}).get("content", []))
            stage, delta = event_update(event)
            if delta:
                answer += delta
            if stage:
                progress(stage, prefix + answer)
        process.wait(timeout=10)
        if process.returncode or not result or result.get("is_error"):
            if result.get("subtype") in ("error_max_turns", "error_max_budget_usd"):
                raise cli_failure("이번 요청의 AI 진행 횟수 또는 비용 제한에 도달했습니다. 범위를 나눠 이어 요청해주세요.", result, tool_calls, model)
            # Never return arbitrary CLI stderr, which can include sensitive settings.
            raise cli_failure(failure_message(result), result, tool_calls, model)
        final = str(result.get("result") or answer).strip()
        if not final:
            raise cli_failure("Claude 답변을 받지 못했습니다. 다시 요청해주세요.", result, tool_calls, model)
        references = []
        if trace.is_file():
            seen = set()
            for line in trace.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                key = (item.get("document_id"), item.get("chunk_id"), json.dumps(item.get("location"), sort_keys=True))
                if key not in seen:
                    references.append(item)
                    seen.add(key)
        return {"text": prefix + final, "citations": references, "usage": claude_cli.usage_to_tokens(result.get("usage")), "model": model, "metrics": usage_metrics(result, tool_calls), "masking": {"system": system_mask.as_dict(), "history": history_mask.as_dict()}}
    finally:
        stop_process(process)
        sender.join(timeout=2)
        process.stdout.close()
    # Job input files are private server data and never included in Git.
