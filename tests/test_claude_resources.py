"""Validates: REQ-OPS-005, REQ-QAINTEL-039. 종료 범위와 실패 시 보존을 확인한다."""
import json
import os
import subprocess
import sys
import shlex
from pathlib import Path

import pytest

from app.core import claude_resources as resources


OWNED = "qa-verification-usage-" + "a" * 32 + ".scope"


def test_scope_wrap_keeps_auth_in_environment_and_applies_limits():
    args = resources.scope_command(["xterm", "-e", "script", "-c", "claude"], OWNED, 60)
    assert args[:3] == ["systemd-run", "--user", "--scope"]
    assert "MemoryMax=1G" in args and "MemorySwapMax=256M" in args
    assert "RuntimeMaxSec=90" in args
    assert args[-5:] == ["xterm", "-e", "script", "-c", "claude"]
    assert "--exec-capture" in args
    with pytest.raises(ValueError):
        resources.scope_command(["true"], "user-session.scope", 60)


def test_proc_report_counts_only_same_user_and_does_not_read_arguments(tmp_path):
    for pid, uid, name, rss, swap in [(51, 123, "claude", 800000, 300000),
                                      (52, 124, "claude", 900000, 0),
                                      (53, 123, "python", 10000, 0)]:
        p = tmp_path / str(pid)
        p.mkdir()
        (p / "status").write_text(f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nVmRSS:\t{rss} kB\nVmSwap:\t{swap} kB\n")
        (p / "cmdline").write_text("secret-not-for-report")
    report = resources.claude_usage(tmp_path, uid=123)
    assert report["count"] == 1
    assert report["rss_bytes"] == 819200000
    assert report["swap_bytes"] == 307200000
    assert report["high_memory_pids"] == [51]
    assert "secret" not in json.dumps(report)


def test_guard_stops_only_expired_owned_scope(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "list-units" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps([
                {"unit": OWNED}, {"unit": "session-9.scope"},
                {"unit": "qa-verification-usage-not-valid.scope"}]), "")
        if "show" in args:
            return subprocess.CompletedProcess(args, 0,
                "Description=QA Claude usage capture\nActiveState=active\nActiveEnterTimestampMonotonic=1000000\nRuntimeMaxUSec=1min 30s\nMemoryCurrent=100\nMemorySwapCurrent=20\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(resources.subprocess, "run", run)
    result = resources.inspect_scopes(now=200)
    assert result["captures"][0]["action"] == "stopped"
    stopped = [c[-1] for c in calls if "stop" in c]
    assert stopped == [OWNED]


@pytest.mark.parametrize("description,age", [("Some other application", 200), ("QA Claude usage capture", 20)])
def test_guard_preserves_foreign_or_active_capture(monkeypatch, description, age):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        out = json.dumps([{"unit": OWNED}]) if "list-units" in args else (
            f"Description={description}\nActiveState=active\nActiveEnterTimestampMonotonic=1000000\nRuntimeMaxUSec=90s\n")
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(resources.subprocess, "run", run)
    resources.inspect_scopes(now=age + 1)
    assert not any("stop" in c for c in calls)


def test_guard_reports_manager_failure_without_stopping(monkeypatch):
    monkeypatch.setattr(resources.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "no bus"))
    assert resources.inspect_scopes()["errors"]


def test_capture_entry_refuses_execution_without_effective_limits(tmp_path):
    """상한 설정이 무시된 환경에서 일반 명령으로 넘어가면 실패해야 한다."""
    marker = tmp_path / "executed"
    result = subprocess.run([sys.executable, str(Path(resources.__file__)), "--exec-capture",
                             sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"],
                            capture_output=True, timeout=10)
    assert result.returncode == 78
    assert not marker.exists()


@pytest.mark.parametrize("memory_max,expected", [("1073741824", True), ("max", False),
                                                  ("2147483648", False)])
def test_effective_kernel_limits_must_be_present_and_bounded(tmp_path, memory_max, expected):
    proc = tmp_path / "cgroup"
    proc.write_text("0::/user.slice/" + OWNED + "\n")
    folder = tmp_path / "sys/user.slice" / OWNED
    folder.mkdir(parents=True)
    for key, value in [("memory.high", "536870912"), ("memory.max", memory_max),
                       ("memory.swap.max", "268435456")]:
        (folder / key).write_text(value)
    assert resources.effective_limits_available(proc, tmp_path / "sys") is expected


@pytest.mark.skipif(sys.platform != "linux", reason="실제 Linux 터미널 정리 시험")
def test_real_terminal_timeout_leaves_no_capture_scope(monkeypatch, tmp_path):
    from app.modules.daily_qa import limit_evidence
    if not Path(f"/run/user/{os.getuid()}/bus").exists():
        pytest.skip("systemd 사용자 관리자 없음")
    unit = resources.new_scope()
    marker = tmp_path / "started.json"
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text("import os,json,pathlib,time\n"
                        + f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid(),'display':os.environ['DISPLAY']}}))\n"
                        + "print('❯',flush=True)\ntime.sleep(300)\n", encoding="utf-8")
    monkeypatch.setattr(resources, "new_scope", lambda: unit)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = limit_evidence.run_xterm(tmp_path, shlex.quote(sys.executable) + " " + shlex.quote(str(fake_cli)), 3)
    assert result.reason == "timeout"
    assert marker.exists(), "캡처 내부의 CLI가 시작되지 않아 종료 검증을 할 수 없습니다"
    child = json.loads(marker.read_text())
    p = Path(f"/proc/{child['pid']}/status")
    assert not p.exists() or "State:\tZ" in p.read_text()
    assert not Path("/tmp/.X11-unix/X" + child["display"].lstrip(":" )).exists()
    check = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState"],
                           env=resources.manager_env(), capture_output=True, text=True)
    assert "ActiveState=active" not in check.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="실제 Linux systemd 종료 범위 시험")
def test_real_scope_removes_detached_descendant_and_preserves_unrelated(tmp_path):
    """창 부모가 먼저 끝나고 자식이 setsid해도 묶음 종료로 정리한다."""
    if not Path(f"/run/user/{os.getuid()}/bus").exists():
        pytest.skip("systemd 사용자 관리자 없음")
    import time
    import uuid
    unit = "qa-verification-usage-" + uuid.uuid4().hex + ".scope"
    pidfile = tmp_path / "child.pid"
    code = ("import os,time,pathlib; p=os.fork(); "
            "(pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid())),os.setsid(),time.sleep(300)) if p==0 else None")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"])
    process = subprocess.Popen(resources.scope_command([sys.executable, "-c", code], unit, 60),
                               env=resources.manager_env(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    child = None
    try:
        end = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < end:
            time.sleep(.05)
        assert pidfile.exists(), process.stderr.read().decode() if process.poll() is not None else "자식 시작 확인 실패"
        child = int(pidfile.read_text())
        snapshot = resources.inspect_scopes()
        assert snapshot["errors"] == []
        assert any(row["unit"] == unit and row["action"] == "kept" for row in snapshot["captures"])
        resources.stop_scope(unit)
        end = time.monotonic() + 5
        while Path(f"/proc/{child}").exists() and time.monotonic() < end:
            if "State:\tZ" in Path(f"/proc/{child}/status").read_text():
                break
            time.sleep(.05)
        assert not Path(f"/proc/{child}").exists() or "State:\tZ" in Path(f"/proc/{child}/status").read_text()
        assert unrelated.poll() is None
    finally:
        resources.stop_scope(unit)
        process.wait(timeout=10)
        unrelated.terminate()
        unrelated.wait(timeout=5)


@pytest.mark.skipif(sys.platform != "linux", reason="systemd 실행 시간 상한 실제 시험")
def test_real_runtime_limit_survives_launcher_exit(tmp_path):
    import time
    if not Path(f"/run/user/{os.getuid()}/bus").exists():
        pytest.skip("systemd 사용자 관리자 없음")
    unit = resources.new_scope()
    marker = tmp_path / "runtime-child.pid"
    code = ("import os,time,pathlib; p=os.fork(); "
            "(os.setsid(),pathlib.Path(" + repr(str(marker)) + ").write_text(str(os.getpid())),time.sleep(300)) if p==0 else None")
    process = subprocess.Popen(resources.scope_command([sys.executable, "-c", code], unit, 1),
                               env=resources.manager_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < end:
            time.sleep(.05)
        assert marker.exists(), "자식 프로세스가 시작되지 않았습니다"
        child = int(marker.read_text())
        group = Path(f"/proc/{child}/cgroup").read_text().split("0::", 1)[1].strip()
        limits = Path("/sys/fs/cgroup") / group.lstrip("/")
        assert int((limits / "memory.max").read_text()) == 1073741824
        assert int((limits / "memory.swap.max").read_text()) == 268435456
        assert process.wait(timeout=5) == 0  # 캡처 호출자가 끝난 뒤에도 시간 제한이 남아야 한다.
        end = time.monotonic() + 38
        child_status = Path(f"/proc/{child}/status")
        while child_status.exists() and time.monotonic() < end:
            if "State:\tZ" in child_status.read_text():
                break
            time.sleep(.2)
        assert not child_status.exists() or "State:\tZ" in child_status.read_text()
    finally:
        resources.stop_scope(unit)
        process.wait(timeout=10)
