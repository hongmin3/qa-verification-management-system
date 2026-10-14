"""Claude 사용량 한도 증거 캡처 (SPEC REQ-QAINTEL-039).

한도에 걸리면 서버의 Claude CLI `/usage` 화면을 실제 터미널 창에서 찍는다. 그림을 그리지 않는다.

1. 화면 없는 가상 디스플레이(`Xvfb`)에 `xterm` 을 띄우고 그 안에서 `script` 로 Claude CLI 를 연다.
   `script` 가 CLI 원본 출력을 파일로 남긴다.
2. 원본 출력을 `pyte`(터미널 화면 재현)로 다시 그려 지금 화면 글자를 읽는다. 폴더 신뢰 확인이면 "Yes",
   입력 칸이면 `/usage` 를 `xdotool` 로 입력한다.
3. 사용량 패널이 보이면 `import -window` 로 xterm 창 픽셀을 PNG 로 저장한다.
4. 화면 글자에서 세션·주간이 100% 인지 보고, 100% 일 때만 증거 폴더에 남긴다.

캡처가 실패해도 예외를 밖으로 내지 않는다. 한도 처리와 점검은 그대로 이어 가야 한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.modules.daily_qa import claude_limits

logger = logging.getLogger("regression_analyzer")

COLS, ROWS = 120, 36
TOOLS = ("Xvfb", "xterm", "xdotool", "import", "script")
CAPTURE_KINDS = (claude_limits.KIND_SESSION, claude_limits.KIND_WEEKLY, claude_limits.KIND_UNKNOWN)
LIMIT_NAMES = {"session": "세션한도", "week": "주간한도"}
LEDGER = "episodes.json"
LOCK = ".capture.lock"
LOCK_STALE_SECONDS = 600
ALLOWED_SUFFIXES = (".png", ".txt")

SESSION_RE = re.compile(r"Current session\s*\n[^\n]*?(\d{1,3})% used", re.IGNORECASE)
WEEK_RE = re.compile(r"Current week[^\n]*\n[^\n]*?(\d{1,3})% used", re.IGNORECASE)
TRUST_MARK = "trust this folder"
PANEL_MARKS = ("Current session", "% used")


@dataclass
class Shot:
    """캡처 실행기 한 번의 결과. 실패면 png 가 None 이고 reason 에 이유가 있다."""

    png: Path | None
    screen: str = ""
    reason: str = ""


@dataclass
class Result:
    saved: bool
    reason: str = ""
    files: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


Runner = Callable[[Path, str, int], Shot]


def _settings(settings):
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return settings


def evidence_dir(settings=None) -> Path:
    return Path(_settings(settings).root) / "data" / "limit_evidence"


def screen_text(raw: bytes, cols: int = COLS, rows: int = ROWS) -> str:
    """CLI 원본 출력을 터미널 화면으로 다시 그려 지금 보이는 글자를 돌려준다."""
    import pyte

    screen = pyte.Screen(cols, rows)
    pyte.ByteStream(screen).feed(raw)
    return "\n".join(line.rstrip() for line in screen.display)


def read_usage(text: str) -> dict:
    """화면 글자에서 세션·주간 사용률(%)을 읽는다. 못 읽은 칸은 넣지 않는다."""
    usage = {}
    for name, pattern in (("session", SESSION_RE), ("week", WEEK_RE)):
        match = pattern.search(text or "")
        if match:
            usage[name] = int(match.group(1))
    return usage


def full_limit(usage: dict) -> str:
    """100% 인 한도 이름(`session`·`week`). 세션을 먼저 본다. 없으면 빈 문자열."""
    for name in ("session", "week"):
        if usage.get(name, 0) >= 100:
            return name
    return ""


def account_label(settings=None, claude_home: Path | None = None) -> str:
    """파일 이름의 계정 표시. 설정값이 없으면 CLI 로그인 이메일 앞부분(`Claude13@vieworks.com` → `Claude13`)."""
    configured = str(_settings(settings).get("daily_qa.limit_evidence.account_label", "") or "").strip()
    if configured:
        return _safe(configured)
    path = (claude_home or Path.home()) / ".claude.json"
    try:
        email = str((json.loads(path.read_text(encoding="utf-8")).get("oauthAccount") or {}).get("emailAddress") or "")
    except (OSError, ValueError, AttributeError):
        email = ""
    local = email.split("@", 1)[0].strip()
    return _safe(local[:1].upper() + local[1:]) if local else "Claude"


def _safe(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "Claude"


def file_stem(moment: datetime, label: str, limit_name: str) -> str:
    local = moment.astimezone(claude_limits.KST)
    return f"스크린샷 {local:%Y-%m-%d %H%M%S}_{label}_{LIMIT_NAMES.get(limit_name, '사용량한도')}"


def episode_key(limit: claude_limits.LimitInfo) -> str:
    return f"{limit.kind}:{limit.reset_at or '-'}"


def _load_ledger(folder: Path) -> dict:
    try:
        data = json.loads((folder / LEDGER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(folder: Path, ledger: dict) -> None:
    temp = folder / (LEDGER + ".tmp")
    temp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(folder / LEDGER)


def _take_lock(folder: Path) -> bool:
    """다른 점검 프로세스가 찍는 중이면 False. 오래된 잠금은 버려진 것으로 보고 지운다."""
    lock = folder / LOCK
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()
    except FileNotFoundError:
        pass
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def tools_missing() -> list[str]:
    return [tool for tool in TOOLS if shutil.which(tool) is None]


def capture(limit: claude_limits.LimitInfo, *, settings=None, claude_command: str = "claude",
            runner: Runner | None = None, now: datetime | None = None, force: bool = False,
            claude_home: Path | None = None) -> Result:
    """한도 하나의 증거를 찍어 저장한다. 어떤 실패도 예외로 내지 않는다.

    force 는 같은 한도를 이미 찍었는지만 보지 않는다(수동 캡처). 100% 확인은 그대로 한다.
    """
    try:
        return _capture(limit, _settings(settings), claude_command, runner, now, force, claude_home)
    except Exception as error:  # noqa: BLE001 — 증거 캡처 실패가 점검을 멈추면 안 된다
        logger.warning("limit_evidence_failed error=%s", type(error).__name__)
        return Result(False, f"error:{type(error).__name__}")


def _skip(reason: str, **extra) -> Result:
    detail = " ".join(f"{key}={value}" for key, value in extra.items())
    logger.info("limit_evidence_skipped reason=%s %s", reason, detail)
    return Result(False, reason, usage=extra.get("usage") or {})


def _capture(limit, settings, claude_command, runner, now, force, claude_home) -> Result:
    if not bool(settings.get("daily_qa.limit_evidence.enabled", True)):
        return _skip("disabled")
    if limit.kind not in CAPTURE_KINDS:
        return _skip("kind", kind=limit.kind)
    if runner is None:
        missing = tools_missing()
        if missing:
            return _skip("tools_missing", missing=",".join(missing))
        runner = run_xterm
    folder = evidence_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    key = episode_key(limit)
    if not force and key in _load_ledger(folder):
        return _skip("already_captured", key=key)
    if not _take_lock(folder):
        return _skip("busy")
    try:
        timeout = int(settings.get("daily_qa.limit_evidence.timeout_seconds", 60) or 60)
        with tempfile.TemporaryDirectory(prefix="limit_evidence_") as temp:
            shot = runner(Path(temp), claude_command, timeout)
            if shot.png is None:
                return _skip(shot.reason or "capture_failed")
            usage = read_usage(shot.screen)
            name = full_limit(usage)
            if not name:
                return _skip("not_full", usage=usage)
            moment = now or datetime.now(timezone.utc)
            stem = file_stem(moment, account_label(settings, claude_home), name)
            png = folder / f"{stem}.png"
            txt = folder / f"{stem}.txt"
            shutil.copyfile(shot.png, png)
            txt.write_text(_evidence_text(limit, moment, usage, shot.screen), encoding="utf-8")
        ledger = _load_ledger(folder)
        ledger[key] = {"files": [png.name, txt.name], "captured_at": moment.isoformat(timespec="seconds")}
        _save_ledger(folder, ledger)
        logger.info("limit_evidence_saved file=%s usage=%s", png.name, usage)
        return Result(True, "", [png.name, txt.name], usage)
    finally:
        try:
            (folder / LOCK).unlink()
        except FileNotFoundError:
            pass


def _evidence_text(limit, moment: datetime, usage: dict, screen: str) -> str:
    local = moment.astimezone(claude_limits.KST).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "Claude 사용량 한도 증거 (REQ-QAINTEL-039)",
        f"캡처 시각(한국 시간): {local}",
        f"서버: {os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', '')}",
        f"감지한 한도: {limit.label} / 초기화 {limit.reset_kst() or '모름'}",
        f"감지한 문장 원문: {limit.message}",
        f"화면에서 읽은 사용률: 세션 {usage.get('session', '?')}% · 주간 {usage.get('week', '?')}%",
        "",
        "----- 터미널 화면 (claude /usage) -----",
        screen,
        "",
    ]
    return "\n".join(lines)


def _wait_screen(raw: Path, check: Callable[[str], bool], deadline: float) -> str:
    """원본 출력을 다시 그려 check 가 참이 될 때까지 기다린다. 시간을 넘기면 빈 문자열."""
    while time.monotonic() < deadline:
        try:
            text = screen_text(raw.read_bytes())
        except OSError:
            text = ""
        if text and check(text):
            return text
        time.sleep(0.5)
    return ""


def _free_display() -> int:
    for number in range(90, 120):
        if not Path(f"/tmp/.X{number}-lock").exists() and not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    raise RuntimeError("빈 디스플레이 번호 없음")


def run_xterm(work: Path, claude_command: str, timeout: int) -> Shot:
    """가상 디스플레이의 xterm 안에서 Claude CLI `/usage` 를 열고 창을 찍는다."""
    deadline = time.monotonic() + timeout
    number = _free_display()
    env = {**os.environ, "DISPLAY": f":{number}", "TZ": "Asia/Seoul"}
    raw = work / "claude.raw"
    png = work / "usage.png"
    # 폴더 신뢰 확인이 매번 나오지 않게 고정 폴더에서 연다. 저장소 밖이라 저장소의 CLAUDE.md 를 읽지 않는다.
    cwd = Path.home() / ".qa-limit-evidence"
    cwd.mkdir(exist_ok=True)
    processes: list[subprocess.Popen] = []

    def xdotool(*args: str) -> str:
        done = subprocess.run(["xdotool", *args], env=env, capture_output=True, text=True, timeout=15)
        return done.stdout.strip()

    try:
        processes.append(subprocess.Popen(["Xvfb", f":{number}", "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        socket = Path(f"/tmp/.X11-unix/X{number}")
        while not socket.exists():
            if time.monotonic() > deadline:
                return Shot(None, reason="xvfb_timeout")
            time.sleep(0.2)
        title = f"limit-evidence-{number}"
        command = ["xterm", "-xrm", "XTerm*termName: xterm-256color", "-geometry", f"{COLS}x{ROWS}+0+0",
                   "-fa", "DejaVu Sans Mono", "-fs", "11", "-bg", "#0c0c0c", "-fg", "#cccccc", "-T", title,
                   "-e", "script", "-q", "-f", str(raw), "-c", claude_command]
        processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        text = _wait_screen(raw, lambda t: TRUST_MARK in t or "❯" in t, deadline)
        if not text:
            return Shot(None, reason="timeout")
        window = xdotool("search", "--sync", "--name", f"^{title}$").splitlines()
        if not window:
            return Shot(None, reason="window_missing")
        xdotool("windowfocus", window[0])
        if TRUST_MARK in text:
            xdotool("key", "Down")
            time.sleep(0.3)
            xdotool("key", "Return")
            if not _wait_screen(raw, lambda t: TRUST_MARK not in t and "❯" in t, deadline):
                return Shot(None, reason="timeout")
        time.sleep(1.0)
        xdotool("type", "--delay", "60", "/usage")
        time.sleep(0.5)
        xdotool("key", "Return")
        screen = _wait_screen(raw, lambda t: all(mark in t for mark in PANEL_MARKS) and "Resets" in t, deadline)
        if not screen:
            return Shot(None, reason="timeout")
        time.sleep(1.0)  # 막대가 다 그려질 때까지
        screen = screen_text(raw.read_bytes())
        subprocess.run(["import", "-window", window[0], str(png)], env=env, capture_output=True, timeout=20)
        if not png.is_file():
            return Shot(None, screen, reason="import_failed")
        xdotool("key", "Escape")
        return Shot(png, screen)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def list_evidence(settings=None) -> list[dict]:
    """증거 폴더의 PNG 목록. 최신 순. 같은 이름의 `.txt` 가 있으면 함께 적는다."""
    folder = evidence_dir(settings)
    if not folder.is_dir():
        return []
    items = []
    for png in folder.glob("*.png"):
        stat = png.stat()
        txt = png.with_suffix(".txt")
        items.append({
            "name": png.name,
            "text": txt.name if txt.is_file() else "",
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone(claude_limits.KST).strftime("%Y-%m-%d %H:%M"),
            "limit": next((label for label in LIMIT_NAMES.values() if png.stem.endswith(label)), ""),
        })
    return sorted(items, key=lambda item: item["name"], reverse=True)


def evidence_file(name: str, settings=None) -> Path | None:
    """웹에서 내려줄 파일. 증거 폴더 바로 아래의 PNG·TXT 만 허락한다."""
    if not name or name != Path(name).name or not name.endswith(ALLOWED_SUFFIXES):
        return None
    path = evidence_dir(settings) / name
    return path if path.is_file() else None
