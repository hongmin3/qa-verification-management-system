"""Claude 사용량 남은 양 보기 (SPEC REQ-AICALL-006, TEST-AICALL-005).

Validates: REQ-AICALL-006
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core import claude_usage
from app.modules.daily_qa.agent_runner import parse_output

TOKEN = "sk-ant-oat01-SECRET-TOKEN-VALUE"
NOW = datetime(2026, 10, 13, 0, 41, tzinfo=timezone.utc)          # 09:41 KST
LIVE = {
    "five_hour": {"utilization": 35.0, "resets_at": "2026-10-13T06:00:00+00:00"},
    "seven_day": {"utilization": 62.0, "resets_at": "2026-10-20T00:00:00Z"},
    "seven_day_opus": None,
}


class FakeSettings:
    def __init__(self, root: Path, **values):
        self.root = root
        self.values = {"ai.claude.usage.credentials_path": str(root / "creds.json"), **values}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def env(tmp_path):
    claude_usage.reset_cache()
    (tmp_path / "data").mkdir()
    (tmp_path / "creds.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}), encoding="utf-8")
    calls: list = []

    def http_ok(url, headers, timeout):
        calls.append((url, headers, timeout))
        return 200, json.dumps(LIVE)

    yield FakeSettings(tmp_path), calls, http_ok
    claude_usage.reset_cache()


def test_live_usage_is_read_and_converted_to_kst(env):
    settings, calls, http_ok = env
    usage = claude_usage.get_usage(settings, now=NOW, http_get=http_ok)
    assert usage["source"] == "live" and usage["error"] == ""
    assert usage["five_hour"] == {"used_percent": 35.0, "remaining_percent": 65.0, "resets_at": "2026-10-13 15:00"}
    assert usage["seven_day"] == {"used_percent": 62.0, "remaining_percent": 38.0, "resets_at": "2026-10-20 09:00"}
    assert usage["measured_at"] == "2026-10-13 09:41"
    url, headers, timeout = calls[0]
    assert url == claude_usage.USAGE_URL and headers["Authorization"] == f"Bearer {TOKEN}" and timeout == 5


def test_cache_prevents_repeated_calls(env):
    settings, calls, http_ok = env
    claude_usage.get_usage(settings, now=NOW, http_get=http_ok)
    claude_usage.get_usage(settings, now=NOW.replace(minute=42), http_get=http_ok)
    assert len(calls) == 1
    claude_usage.get_usage(settings, now=NOW.replace(minute=44), http_get=http_ok)      # 120초가 지났다
    assert len(calls) == 2


def _event_line(info: dict) -> str:
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": info, "session_id": "s"})


def test_runner_output_rate_limit_event_is_parsed_and_broken_lines_skipped():
    reset = int(datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc).timestamp())
    stdout = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        '{"type": "rate_limit_ev',                                                       # 깨진 줄
        _event_line({"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.2, "resetsAt": reset}),
        _event_line({"status": "allowed_warning", "rateLimitType": "five_hour", "resetsAt": reset,
                     "unifiedWindows": {"five_hour": {"utilization": 0.41, "resetsAt": reset},
                                        "seven_day": {"utilization": 0.7, "resetsAt": reset + 6 * 86400}}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "usage": {}}),
    ])
    meta, _calls = parse_output(stdout)
    assert meta["rate_limit"]["unifiedWindows"]["seven_day"]["utilization"] == 0.7


def test_fallback_to_last_run_event_when_live_fails(env):
    settings, _calls, _ok = env
    reset = int(datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc).timestamp())
    claude_usage.record_rate_limit_event(
        {"rateLimitType": "five_hour", "utilization": 0.41, "resetsAt": reset}, settings=settings, now=NOW)
    claude_usage.record_rate_limit_event(
        {"rateLimitType": "seven_day", "utilization": 0.7, "resetsAt": reset + 7 * 86400}, settings=settings, now=NOW)

    def http_denied(url, headers, timeout):
        return 403, json.dumps({"error": {"type": "permission_error", "message": f"bad token {TOKEN}"}})

    usage = claude_usage.get_usage(settings, now=NOW, http_get=http_denied)
    assert usage["source"] == "last_run"
    assert usage["five_hour"]["used_percent"] == 41.0 and usage["five_hour"]["resets_at"] == "2026-10-13 15:00"
    assert usage["seven_day"]["used_percent"] == 70.0                          # 앞서 받은 5시간 값과 함께 남는다
    assert "권한" in usage["error"]
    saved = (settings.root / "data" / "claude_usage.json").read_text(encoding="utf-8")
    assert TOKEN not in saved and TOKEN not in json.dumps(usage, ensure_ascii=False)


def test_network_error_and_missing_credentials(env, tmp_path):
    settings, _calls, _ok = env

    def http_boom(url, headers, timeout):
        raise OSError(f"timed out while sending {TOKEN}")

    usage = claude_usage.get_usage(settings, now=NOW, http_get=http_boom)
    assert usage["source"] == "none" and TOKEN not in usage["error"] and usage["five_hour"] is None
    claude_usage.reset_cache()
    (settings.root / "creds.json").unlink()
    usage = claude_usage.get_usage(settings, now=NOW, http_get=http_boom)
    assert usage["source"] == "none" and "로그인 정보" in usage["error"]


def test_disabled_and_unexpected_shape(env):
    settings, _calls, _ok = env
    off = FakeSettings(settings.root, **{"ai.claude.usage.enabled": False})
    assert claude_usage.get_usage(off, now=NOW, http_get=_ok)["source"] == "none"
    claude_usage.reset_cache()

    def http_odd(url, headers, timeout):
        return 200, json.dumps({"five_hour": {"kind": "x", "percent": None}, "seven_day": None})

    usage = claude_usage.get_usage(settings, now=NOW, http_get=http_odd)
    assert usage["source"] == "live" and usage["five_hour"] is None and usage["seven_day"] is None


def test_usage_endpoint_and_dashboard_slot(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(claude_usage, "get_usage", lambda settings=None, **_: {
        "source": "live", "error": "", "measured_at": "2026-10-13 09:41",
        "five_hour": {"used_percent": 35.0, "remaining_percent": 65.0, "resets_at": "2026-10-13 15:00"},
        "seven_day": None})
    client = TestClient(app)
    body = client.get("/claude/usage").json()
    assert body["five_hour"]["remaining_percent"] == 65.0
    page = client.get("/qa-agent").text
    assert 'id="claude-usage"' in page and "/claude/usage" in page


def test_expired_token_is_refreshed_through_cli_at_most_once_per_interval(env):
    settings, _calls, _ok = env
    state = {"fresh": False}
    refreshes: list = []

    def http(url, headers, timeout):
        if not state["fresh"]:
            return 401, json.dumps({"error": {"type": "authentication_error", "message": "OAuth access token has expired."}})
        return 200, json.dumps(LIVE)

    def refresh(settings_):
        refreshes.append(1)
        state["fresh"] = True
        return 924

    usage = claude_usage.get_usage(settings, now=NOW, http_get=http, refresh=refresh)
    assert usage["source"] == "live" and len(refreshes) == 1
    saved = json.loads((settings.root / "data" / "claude_usage.json").read_text(encoding="utf-8"))
    assert saved["last_refresh_at"] and saved["last_refresh_tokens"] == 924

    claude_usage.reset_cache()
    state["fresh"] = False                                       # 다시 만료됐지만 60분 안이다
    usage = claude_usage.get_usage(settings, now=NOW.replace(minute=50), http_get=http, refresh=refresh)
    assert len(refreshes) == 1 and usage["source"] == "none" and "만료" in usage["error"]

    off = FakeSettings(settings.root, **{"ai.claude.usage.refresh_with_cli": False})
    claude_usage.reset_cache()
    claude_usage.get_usage(off, now=NOW.replace(hour=3), http_get=http, refresh=refresh)
    assert len(refreshes) == 1
