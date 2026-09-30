"""사양서 동기화 CLI(scripts/sync_vxvue_spec.py)의 종료 코드.

PARTIAL 도 1 로 끝낸다. 0 이면 작업 스케줄러에 성공으로 보여 아무도 모른다. 지식 업로드와 같은 기준이다
(OPEN_QUESTIONS 8-17, SPEC 13.5).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "sync_vxvue_spec_cli", Path(__file__).resolve().parents[1] / "scripts" / "sync_vxvue_spec.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


# Validates: REQ-SYNC-001
@pytest.mark.parametrize(
    ("status", "expected"),
    [("SUCCESS", 0), ("DRY_RUN", 0), ("PARTIAL", 1), ("FAILED", 1), ("NEEDS_CONFIG", 1)],
)
def test_spec_sync_exit_code(monkeypatch, status, expected):
    cli = _load_cli()
    reported = []
    monkeypatch.setattr(cli, "acquire_lock", lambda: True)
    monkeypatch.setattr(cli, "release_lock", lambda: None)
    monkeypatch.setattr(cli, "run", lambda url, dry_run: {"status": status, "detail": "d"})
    monkeypatch.setattr(cli, "report_sync_log", lambda *args: reported.append(args))
    monkeypatch.setattr(sys, "argv", ["sync_vxvue_spec.py", "--target-url", "http://server.invalid"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == expected
    assert reported and reported[0][4] == status
