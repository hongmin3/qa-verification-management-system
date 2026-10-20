"""Claude 사용량 화면 캡처 격리와 운영 점검 (REQ-OPS-005, REQ-QAINTEL-039).

자동 종료는 이 앱 전용 systemd scope에만 수행한다. 일반 Claude는 조회만 한다.
인증정보는 scope 모드가 물려받는 환경으로만 전달하며 명령·로그에 넣지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

SCOPE_PATTERN = re.compile(r"qa-verification-usage-[0-9a-f]{32}\.scope\Z")
DESCRIPTION = "QA Claude usage capture"
HIGH_MEMORY_BYTES = 1024 ** 3


def manager_env(base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    if sys.platform == "linux":
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    return env


def new_scope() -> str:
    return f"qa-verification-usage-{uuid4().hex}.scope"


def _validate(unit: str) -> None:
    if not SCOPE_PATTERN.fullmatch(unit):
        raise ValueError("이 앱의 캡처 묶음이 아닙니다")


def scope_command(command: list[str], unit: str, timeout: int) -> list[str]:
    _validate(unit)
    if timeout <= 0:
        raise ValueError("캡처 제한 시간은 양수여야 합니다")
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}",
            f"--description={DESCRIPTION}", "-p", "MemoryHigh=512M", "-p", "MemoryMax=1G",
            "-p", "MemorySwapMax=256M", "-p", f"RuntimeMaxSec={timeout + 30}",
            "-p", "TimeoutStopSec=3", "--", sys.executable, str(Path(__file__).resolve()),
            "--exec-capture", *command]


def effective_limits_available(proc_cgroup: Path = Path("/proc/self/cgroup"),
                               cgroup_root: Path = Path("/sys/fs/cgroup")) -> bool:
    """설정 접수 여부와 별개로 커널이 적용한 상한을 확인한다."""
    try:
        group = next(line[3:] for line in proc_cgroup.read_text().splitlines() if line.startswith("0::"))
        path = Path(group)
        if not SCOPE_PATTERN.fullmatch(path.name) or ".." in path.parts:
            return False
        folder = cgroup_root / group.lstrip("/")
        for name, cap in [("memory.high", 512 * 1024 ** 2), ("memory.max", 1024 ** 3),
                          ("memory.swap.max", 256 * 1024 ** 2)]:
            value = int((folder / name).read_text().strip())
            if value < 0 or value > cap:
                return False
        return True
    except (OSError, ValueError, StopIteration):
        return False


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], env=manager_env(), capture_output=True,
                          text=True, encoding="utf-8", timeout=15)


def stop_scope(unit: str) -> None:
    _validate(unit)
    result = _systemctl("stop", "--", unit)
    if result.returncode:
        # --collect가 이미 회수한 묶음은 종료 성공과 같다. 조회 실패는 성공으로 숨기지 않는다.
        state = _systemctl("show", unit, "-p", "LoadState")
        if "LoadState=not-found" not in state.stdout:
            raise RuntimeError("캡처 묶음 종료 실패")


def claude_usage(proc: Path = Path("/proc"), *, uid: int | None = None) -> dict:
    uid = os.getuid() if uid is None else uid
    result = {"count": 0, "rss_bytes": 0, "swap_bytes": 0, "high_memory_pids": [], "unreadable": 0}
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines() if ":" in line)
            if int(fields["Uid"].split()[0]) != uid or fields["Name"].strip() != "claude":
                continue
            rss = int(fields.get("VmRSS", "0").split()[0]) * 1024
            swap = int(fields.get("VmSwap", "0").split()[0]) * 1024
            result["count"] += 1
            result["rss_bytes"] += rss
            result["swap_bytes"] += swap
            if rss + swap >= HIGH_MEMORY_BYTES:
                result["high_memory_pids"].append(int(path.name))
        except FileNotFoundError:
            continue  # 읽는 중 정상 종료한 프로세스
        except (OSError, ValueError, KeyError):
            result["unreadable"] += 1
    return result


def _seconds(value: str) -> float:
    """systemctl의 기간 표기(90s, 1min 30s)를 초로 바꾼다."""
    if value.isdecimal():
        return int(value) / 1_000_000
    parts = re.findall(r"(\d+(?:\.\d+)?)(us|ms|min|h|s|d)", value.replace(" ", ""))
    if not parts or "".join(a + b for a, b in parts) != value.replace(" ", ""):
        raise ValueError("기간을 읽지 못했습니다")
    factors = {"us": .000001, "ms": .001, "s": 1, "min": 60, "h": 3600, "d": 86400}
    return sum(float(number) * factors[unit] for number, unit in parts)


def inspect_scopes(*, now: float | None = None) -> dict:
    result = {"captures": [], "errors": []}
    try:
        listing = _systemctl("list-units", "--type=scope", "--all", "--output=json", "--no-pager")
        if listing.returncode:
            raise RuntimeError("systemd 사용자 관리자 조회 실패")
        for row in json.loads(listing.stdout):
            unit = row["unit"]
            if not SCOPE_PATTERN.fullmatch(unit):
                continue
            shown = _systemctl("show", unit, "-p", "Description", "-p", "ActiveState", "-p", "LoadState",
                               "-p", "ActiveEnterTimestampMonotonic", "-p", "RuntimeMaxUSec",
                               "-p", "MemoryCurrent", "-p", "MemorySwapCurrent")
            fields = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
            if fields.get("LoadState") == "not-found":
                continue
            if shown.returncode:
                raise RuntimeError("캡처 묶음 조회 실패")
            if fields.get("Description") != DESCRIPTION or fields.get("ActiveState") != "active":
                continue
            elapsed = (time.monotonic() if now is None else now) - _seconds(fields["ActiveEnterTimestampMonotonic"])
            limit = _seconds(fields["RuntimeMaxUSec"])
            capture = {"unit": unit, "age_seconds": round(elapsed, 1), "action": "kept",
                       "rss_bytes": int(fields.get("MemoryCurrent", "0")),
                       "swap_bytes": int(fields.get("MemorySwapCurrent", "0"))}
            result["captures"].append(capture)
            if elapsed >= limit:
                stop_scope(unit)
                capture["action"] = "stopped"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, RuntimeError) as exc:
        result["errors"].append(type(exc).__name__)
    return result


def inspect_claude_resources() -> dict:
    if sys.platform != "linux":
        return {"status": "unsupported", "reason": "linux_only"}
    try:
        usage = claude_usage()
        scopes = inspect_scopes()
        status = "alert" if usage["high_memory_pids"] or usage["unreadable"] or scopes["errors"] else "ok"
        return {"status": status, **usage, **scopes}
    except (OSError, ValueError) as exc:
        return {"status": "alert", "errors": [type(exc).__name__]}


if __name__ == "__main__":
    if sys.argv[1:2] != ["--exec-capture"] or len(sys.argv) < 3:
        raise SystemExit(64)
    if not effective_limits_available():
        raise SystemExit(78)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
