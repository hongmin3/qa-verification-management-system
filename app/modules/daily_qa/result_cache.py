"""Exact whole-batch result reuse (REQ-QAINTEL-037). No AI calls or history backfill."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path

from app.modules.daily_qa.agent_runner import build_prompt
from app.modules.daily_qa.evidence_validation import ABSENCE_VERDICTS, validate_finding
from app.modules.daily_qa.schema import ResultFileError, parse_result

VERSION = 1
PREFIX = "daily_qa:result:v1:"
INSUFFICIENT = ABSENCE_VERDICTS - {"NO_DUPLICATE_FOUND"}


def corpus_digest(corpus) -> str:
    # Candidate excerpts alone cannot detect edits beyond their text limit.
    value = {"issues": corpus.issues, "srs": corpus.srs, "spec_chunks": corpus.spec_chunks,
             "chunk_labels": corpus.chunk_labels, "tcs": corpus.tc_rows, "manuals": corpus.manuals,
             "unreadable": corpus.unreadable_documents}
    def encode(part):
        if hasattr(part, "model_dump"):
            return part.model_dump()
        return asdict(part) if is_dataclass(part) else str(part)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode).encode("utf-8")).hexdigest()


def _semantic_payload(payload: dict) -> dict:
    value = copy.deepcopy(payload)
    for key in ("run_id", "task_id", "event_ids"):
        value.pop(key, None)
    for item in value.get("items") or []:
        for key in ("run_id", "task_id", "event_ids"):
            item.pop(key, None)
        for event in item.get("events") or []:
            for key in ("id", "run_id", "task_id"):
                event.pop(key, None)
    return value


def _files(root: Path) -> list[tuple[str, str]]:
    if not root.exists():
        return []
    return [(path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(root.rglob("*")) if path.is_file()]


def fingerprint(task, workspace, product: str, kind: str, mode: str, model: str, effort: str, profile: dict) -> str:
    payload = json.loads((workspace.in_dir / f"{task.task_id}.json").read_text(encoding="utf-8"))
    # 실행·작업 번호는 자리표시로 만든다. 문자열 치환은 파일 이름 같은 다른 글자까지 바꿀 수 있다.
    prompt = build_prompt(replace(task, task_id="<task>"), replace(workspace, run_id="<run>"), inline_input=False)
    implementation = Path(__file__).parent
    value = {"version": VERSION, "product": product, "kind": kind, "mode": mode,
             "model": model, "effort": effort, "profile": profile, "skill": task.skill,
             "payload": _semantic_payload(payload), "prompt": prompt,
             "rules": _files(workspace.root / "rules"), "skills": _files(workspace.root / ".claude" / "skills"),
             "context": _files(workspace.context_dir),
             "instructions": [(name, hashlib.sha256((workspace.root / name).read_bytes()).hexdigest())
                              for name in ("CLAUDE.md", ".claude/settings.json") if (workspace.root / name).is_file()],
             "validation": [(name, hashlib.sha256((implementation / name).read_bytes()).hexdigest())
                            for name in ("schema.py", "evidence_validation.py", "result_cache.py")]}
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unresolved(value) -> bool:
    if isinstance(value, dict):
        if (isinstance(value.get("verdict"), str) and value["verdict"] in INSUFFICIENT) or value.get("confidence") == "Unsupported":
            return True
        if value.get("tc_hold") or value.get("open_questions") or value.get("questions") or value.get("questions_for_lab"):
            return True
        return any(_unresolved(part) for part in value.values())
    return isinstance(value, list) and any(_unresolved(part) for part in value)


def eligible(checked, task, kind, known, axes, tc_mode=False) -> bool:
    result = checked.result
    targets = {item.get("target") for item in task.payload.get("items") or []}
    if (task.payload.get("unreadable_documents") or checked.rejected or result.open_questions
            or not result.gate_status or any(status != "PASS" for status in result.gate_status.values())
            or not checked.accepted or {finding.subject for finding in checked.accepted} != targets):
        return False
    for finding in checked.accepted:
        raw = finding.model_dump()
        previous = (raw.get("sections") or {}).get("validation") or {}
        if not isinstance(previous, dict) or previous.get("removed") or previous.get("notes") or _unresolved(raw):
            return False
        valid, _ = validate_finding(copy.deepcopy(raw), kind, known, targets, axes, tc_mode=tc_mode)
        if valid is None:
            return False
        record = valid.get("sections", {}).get("validation") or {}
        if record.get("removed") or record.get("notes") or _unresolved(valid) or not valid.get("evidence"):
            return False
        if any(entry.get("source_type") in ("srs", "spec_doc") and entry.get("validity") != "Current"
               for entry in valid["evidence"]):
            return False
    return True


def load(storage, key, result_path, task, kind, known, axes, tc_mode=False, diagnostics=None):
    """Malformed/stale entries are misses; the caller retains its normal CLI path."""
    temporary = None
    try:
        cached = storage.cache_get(key)
        if not isinstance(cached, dict) or cached.get("version") != VERSION or cached.get("fingerprint") != key:
            return None
        source = cached.get("source")
        if (not isinstance(source, dict) or not isinstance(source.get("run_id"), str) or not source["run_id"]
                or not isinstance(source.get("task_id"), str) or not source["task_id"]
                or not isinstance(source.get("finding_ids"), list) or not source["finding_ids"]
                or any(not isinstance(value, int) or value <= 0 for value in source["finding_ids"])):
            return None
        raw = copy.deepcopy(cached["result"])
        if raw.get("skill") != task.skill:
            return None
        raw["task_id"] = task.task_id
        # Validate separately. A miss must neither overwrite an existing result
        # nor leave a rejected cache response where fresh CLI output is expected.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", prefix=".cache-check-",
                                         dir=result_path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(raw, handle, ensure_ascii=False)
        checked = parse_result(temporary, task.skill, task.task_id)
        if eligible(checked, task, kind, known, axes, tc_mode):
            temporary.replace(result_path)
            return checked, source
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ResultFileError, sqlite3.Error) as exc:
        if diagnostics is not None:
            diagnostics["cache_error"] = f"read: {type(exc).__name__}"
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                if diagnostics is not None:
                    diagnostics["cache_error"] = f"cleanup: {type(exc).__name__}"
    return None


def save(storage, key, checked, source, diagnostics=None):
    try:
        storage.cache_set(key, {"version": VERSION, "fingerprint": key, "source": source,
                                "result": checked.result.model_dump()})
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
        if diagnostics is not None:
            diagnostics["cache_error"] = f"write: {type(exc).__name__}"
