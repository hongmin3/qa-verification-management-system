"""Keep every copy of the managed vxvue-ai Skill on this PC equal to the server's deployed one.

Claude Code and Codex each hold their own copy, so an improvement deployed from either side
would otherwise reach only the copy that side installed (REQ-VXVUECHATBOT-015).

    python scripts/sync_vxvue_local_skill.py                 # sync now and print the result
    python scripts/sync_vxvue_local_skill.py --install-hooks # run it at every Claude/Codex session start
    python scripts/sync_vxvue_local_skill.py --uninstall-hooks

Standard library only: the session-start hook must stay fast and never depend on the app's venv.
Only the connection URL is read from the client configs; keys and other values are never read out.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import urllib.request
import zipfile

MARKER = b"vxvue-ai-managed"
ZIP_ENTRY = "vxvue-ai/SKILL.md"
MAX_ZIP_BYTES = 1_000_000
FETCH_TIMEOUT = 3
RETRY_AFTER_FAILURE = 30 * 60
KEEP_BACKUPS = 40
HOOK_TAG = "vxvue-skill-sync"
SCRIPT = Path(__file__).resolve()
PLUGIN = Path("local-marketplaces/vxvue-server-local/plugins/vxvue-server-ai")
CACHE = Path("plugins/cache/vxvue-server-local/vxvue-server-ai")


def default_claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def default_codex_dir():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def normalized(data):
    return data.replace(b"\r\n", b"\n")


def digest(data):
    return hashlib.sha256(normalized(data)).hexdigest()


def targets(claude_dir, codex_dir):
    """(label, path) for every place a client loads the Skill from; the cache uses its newest version."""
    found = [("claude", claude_dir / "skills/vxvue-ai/SKILL.md"),
             ("codex", codex_dir / "skills/vxvue-ai/SKILL.md"),
             ("codex-plugin", codex_dir / PLUGIN / "skills/vxvue-ai/SKILL.md")]
    cache = codex_dir / CACHE
    versions = []
    if cache.is_dir():
        versions = [(tuple(map(int, p.name.split("."))), p) for p in cache.iterdir()
                    if p.is_dir() and not p.is_symlink() and re.fullmatch(r"\d+(?:\.\d+)*", p.name)]
    newest = max(versions)[1] if versions else cache / "<없음>"
    found.append(("codex-cache", newest / "skills/vxvue-ai/SKILL.md"))
    return found


def _origin(url):
    parts = urlsplit(url or "")
    if parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password:
        return f"{parts.scheme}://{parts.netloc}"
    return None


def _json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def server_url(explicit, *, claude_dir, codex_dir, env=os.environ):
    """Option, then VXVUE_SERVER_URL, then the registered Claude connection, then the Codex plugin."""
    if explicit:
        return _origin(explicit) or explicit.rstrip("/")
    if env.get("VXVUE_SERVER_URL"):
        return _origin(env["VXVUE_SERVER_URL"])
    claude = _json(claude_dir.parent / ".claude.json") or {}
    url = ((claude.get("mcpServers") or {}).get("vxvue-ai") or {}).get("url")
    if _origin(url):
        return _origin(url)
    plugin = _json(codex_dir / PLUGIN / ".mcp.json") or {}
    value = (((plugin.get("mcpServers") or {}).get("vxvue-ai") or {}).get("env") or {}).get("VXVUE_SERVER_URL")
    return _origin(value)


def fetch(server, timeout):
    request = urllib.request.Request(f"{server}/vxvue-desktop/skill.zip", headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("Skill ZIP이 너무 큽니다.")
    return data


def skill_from_zip(data):
    """The one SKILL.md of the managed vxvue-ai Skill, or ValueError: nothing is written from a bad download."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if ZIP_ENTRY not in archive.namelist():
                raise ValueError("ZIP에 vxvue-ai/SKILL.md가 없습니다.")
            text = archive.read(ZIP_ENTRY)
    except zipfile.BadZipFile as exc:
        raise ValueError("Skill ZIP 형식이 아닙니다.") from exc
    head = normalized(text).split(b"\n---\n", 1)[0]
    if not text.startswith(b"---") or not re.search(rb"^name:\s*vxvue-ai\s*$", head, re.M) or MARKER not in text:
        raise ValueError("서버 Skill의 이름 또는 관리 표시가 맞지 않습니다.")
    return text


def _load_state(state_dir):
    state = _json(state_dir / "skill-sync-state.json")
    return state if isinstance(state, dict) else {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _save_state(state_dir, state):
    _write(state_dir / "skill-sync-state.json", json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))


def _backup(state_dir, label, data):
    folder = state_dir / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (folder / f"{stamp}-{label}.md").write_bytes(data)
    for old in sorted(folder.glob("*.md"))[:-KEEP_BACKUPS]:
        old.unlink()


def sync(data, *, claude_dir, codex_dir, state_dir, force=False):
    """Replace managed copies that differ from `data`; a copy edited on this PC since the last sync is kept."""
    state = _load_state(state_dir)
    applied = state.setdefault("applied", {})
    wanted = digest(data)
    result = {"updated": [], "same": [], "skipped": [], "local_edits": []}
    for label, path in targets(claude_dir, codex_dir):
        key = str(path)
        if not path.is_file():
            result["skipped"].append((label, "사본 없음"))
            continue
        current = path.read_bytes()
        if MARKER not in current:
            result["skipped"].append((label, "관리 표시 없음"))
            continue
        if digest(current) == wanted:
            result["same"].append(label)
        elif key in applied and applied[key] != digest(current) and not force:
            result["local_edits"].append(label)
            continue
        else:
            _backup(state_dir, label, current)
            _write(path, data)
            result["updated"].append(label)
        applied[key] = wanted
    state.update(server_sha256=wanted, synced_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    state.pop("last_failure", None)
    _save_state(state_dir, state)
    return result


def summary(result):
    lines = [f"바꿈 {len(result['updated'])}개, 같음 {len(result['same'])}개, 건너뜀 {len(result['skipped'])}개, "
             f"직접 고친 사본 {len(result['local_edits'])}개"]
    lines += [f"  바꿈: {label}" for label in result["updated"]]
    lines += [f"  건너뜀: {label} ({reason})" for label, reason in result["skipped"]]
    lines += [f"  직접 고친 사본(덮어쓰지 않음): {label}" for label in result["local_edits"]]
    return "\n".join(lines)


def hook_message(result):
    notes = []
    if result["updated"]:
        notes.append(f"VXvue AI Skill: 서버 배포본으로 이 PC의 사본 {len(result['updated'])}개를 맞췄습니다 "
                     f"({', '.join(result['updated'])}).")
    if result["local_edits"]:
        notes.append(f"VXvue AI Skill: 이 PC에서 직접 고친 사본은 덮어쓰지 않았습니다 ({', '.join(result['local_edits'])}). "
                     "개선은 deploy/claude-desktop/skill/vxvue-ai/SKILL.md를 고쳐 서버에 배포하고, "
                     "정리 후 scripts/sync_vxvue_local_skill.py --force로 맞춥니다.")
    if not notes:
        return None
    text = " ".join(notes)
    return json.dumps({"systemMessage": text,
                       "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
                      ensure_ascii=False)


def hook_command(python, cli, windows=False):
    """The script path rides in base64 so a non-ASCII folder name survives every shell.

    PowerShell rejects a command that starts with a quoted path, so the Windows form uses the bare
    path when it needs no quoting and falls back to `python` from PATH otherwise.
    """
    encoded = base64.b64encode(str(SCRIPT).encode("utf-8")).decode("ascii")
    code = (f"import base64,runpy,sys;tag='{HOOK_TAG}';p=base64.b64decode('{encoded}').decode('utf-8');"
            f"sys.argv=[p,'--hook','{cli}'];runpy.run_path(p,run_name='__main__')")
    if windows:
        exe = python if re.fullmatch(r"[A-Za-z0-9:\\/._-]+", python) else "python"
    else:
        exe = f'"{python}"'
    return f'{exe} -X utf8 -c "{code}"'


def _edit_hooks(path, cli, python, remove):
    data = _json(path) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: JSON을 읽지 못해 바꾸지 않았습니다.")
    hooks = data.setdefault("hooks", {})
    groups = [{**group, "hooks": [h for h in group.get("hooks", []) if HOOK_TAG not in str(h.get("command", ""))]}
              for group in hooks.get("SessionStart", [])]
    groups = [group for group in groups if group["hooks"]]
    if not remove:
        entry = {"type": "command", "command": hook_command(python, cli), "timeout": 15 if cli == "claude" else 10}
        if cli == "codex":
            entry["commandWindows"] = hook_command(python, cli, windows=True)
        # Codex trusts a hook by its position (session_start:<group>:<hook>). The kit's installer always
        # re-appends its own group last, so staying first keeps both positions, and both approvals, stable.
        groups.insert(0, {"matcher": "startup|resume|clear|compact", "hooks": [entry]})
    if groups:
        hooks["SessionStart"] = groups
    else:
        hooks.pop("SessionStart", None)
    _write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def install_hooks(*, claude_dir, codex_dir, python=sys.executable, remove=False):
    """Add (or remove) only this tool's SessionStart entry; every other hook and setting is kept."""
    _edit_hooks(claude_dir / "settings.json", "claude", python, remove)
    _edit_hooks(codex_dir / "hooks.json", "codex", python, remove)


def main(argv=None):
    parser = argparse.ArgumentParser(description="이 PC의 VXvue AI Skill 사본을 서버 배포본과 맞춘다.")
    parser.add_argument("--server")
    parser.add_argument("--hook", choices=("claude", "codex"))
    parser.add_argument("--force", action="store_true", help="이 PC에서 직접 고친 사본도 백업 후 바꾼다")
    parser.add_argument("--install-hooks", action="store_true")
    parser.add_argument("--uninstall-hooks", action="store_true")
    parser.add_argument("--claude-dir", type=Path, default=None)
    parser.add_argument("--codex-dir", type=Path, default=None)
    parser.add_argument("--state-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    claude_dir = args.claude_dir or default_claude_dir()
    codex_dir = args.codex_dir or default_codex_dir()
    state_dir = args.state_dir or Path.home() / ".vxvue-ai"

    if args.install_hooks or args.uninstall_hooks:
        install_hooks(claude_dir=claude_dir, codex_dir=codex_dir, remove=args.uninstall_hooks)
        print("훅을 " + ("지웠습니다." if args.uninstall_hooks else "등록했습니다. 다음 세션부터 적용됩니다."))
        return 0

    if args.hook:
        # A session must never wait on or fail because of this: errors stay silent, a dead server is retried later.
        try:
            if not sys.stdin.isatty():
                sys.stdin.read()
        except OSError:
            pass
        outcome = "error"
        try:
            state = _load_state(state_dir)
            if time.time() - state.get("last_failure", 0) < RETRY_AFTER_FAILURE:
                outcome = "waiting after failure"
            elif not (server := server_url(args.server, claude_dir=claude_dir, codex_dir=codex_dir)):
                outcome = "no server address"
            else:
                try:
                    data = skill_from_zip(fetch(server, FETCH_TIMEOUT))
                except (OSError, ValueError) as exc:
                    state["last_failure"] = time.time()
                    _save_state(state_dir, state)
                    outcome = f"server unavailable: {exc}"
                else:
                    result = sync(data, claude_dir=claude_dir, codex_dir=codex_dir, state_dir=state_dir)
                    outcome = summary(result).splitlines()[0]
                    message = hook_message(result)
                    if message:
                        print(message)
        except Exception as exc:  # noqa: BLE001 - a hook reports to stderr and lets the session start
            outcome = f"error: {exc}"
            print(f"vxvue skill sync: {exc}", file=sys.stderr)
        try:  # the only trace that the hook ran at all, since a quiet run prints nothing
            state = _load_state(state_dir)
            state["last_hook"] = {"cli": args.hook, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "outcome": outcome[:300]}
            _save_state(state_dir, state)
        except OSError:
            pass
        return 0

    server = server_url(args.server, claude_dir=claude_dir, codex_dir=codex_dir)
    if not server:
        print("서버 주소를 찾지 못했습니다. --server로 지정하세요.", file=sys.stderr)
        return 2
    try:
        data = skill_from_zip(fetch(server, 10))
    except (OSError, ValueError) as exc:
        print(f"서버 Skill을 받지 못해 아무 사본도 바꾸지 않았습니다: {exc}", file=sys.stderr)
        return 1
    result = sync(data, claude_dir=claude_dir, codex_dir=codex_dir, state_dir=state_dir, force=args.force)
    print(f"서버: {server}\n{summary(result)}")
    return 3 if result["local_edits"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
