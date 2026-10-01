"""토큰이 없는 PC 에서 Claude CLI 로그인으로 점검 AI 를 부른다 (사용자 결정 2026-10-01).

TEST-QAINTEL-016 Claude CLI 로그인 사용.

Validates: REQ-DAILY-001, REQ-DAILY-012, REQ-QAINTEL-025
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from app.core.claude_cli import login_status
from app.modules.daily_qa import claude_limits, pipeline
from app.modules.daily_qa.agent_runner import FakeRunner
from tests.daily_qa_fixtures import issue_item, srs_item
from tests.qa_intel_harness import DAY1, DAY2, Harness

LOGGED_IN = {"logged_in": True, "method": "claude.ai", "subscription": "team", "error": ""}


def _completed(stdout: str, code: int = 0):
    return lambda command, **kwargs: subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")


def test_login_status_reads_json_after_hook_lines_and_drops_identity():
    out = '[hook] 안내 한 줄\n{"loggedIn": true, "authMethod": "claude.ai", "email": "a@b.c", "orgId": "x", "subscriptionType": "team"}'
    status = login_status(runner=_completed(out))
    assert status == LOGGED_IN
    assert "email" not in status and "orgId" not in status          # 기록에 남기지 않는다


def test_login_status_not_logged_in_or_broken_output():
    assert login_status(runner=_completed('{"loggedIn": false}'))["logged_in"] is False
    assert login_status(runner=_completed("Unknown command: auth", 1)) == {"logged_in": False, "method": "", "subscription": "", "error": "exit=1"}

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])
    assert login_status(runner=missing)["error"] == "FileNotFoundError"


def _harness(tmp_path, monkeypatch, login):
    h = Harness(tmp_path, claude_token="")
    h.srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색")]
    h.issues = [issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open")]
    monkeypatch.setattr(pipeline, "claude_login_status", lambda command="claude", **_: login)
    made = []

    def fake_runner(command, token, timeout, model=""):
        made.append(token)
        return FakeRunner(h._produce)
    monkeypatch.setattr(pipeline, "ClaudeRunner", fake_runner)
    return h, made


def test_logged_in_cli_is_used_when_token_is_missing(tmp_path, monkeypatch):
    h, made = _harness(tmp_path, monkeypatch, LOGGED_IN)
    h.run(DAY1, runner=None)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    outcome = h.run(DAY2, runner=None)
    assert made and made[-1] == ""                                       # 토큰 없이 CLI 를 부른다(로그인 사용)
    assert outcome["stages"]["preflight"] == {"status": "ok", "note": "Claude CLI 로그인(claude.ai)으로 부릅니다."}
    assert outcome["stages"]["NEW_ISSUE"]["status"] == "ok"                 # (이 도우미는 TC 파일이 없어 실행 결과는 일부 실패)
    assert any(called for called, _ in h.calls)


def test_without_token_and_login_ai_is_blocked(tmp_path, monkeypatch):
    h, made = _harness(tmp_path, monkeypatch, {"logged_in": False, "method": "", "subscription": "", "error": ""})
    h.run(DAY1, runner=None)
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    outcome = h.run(DAY2, runner=None)
    assert outcome["stages"]["preflight"]["status"] == "partial"
    assert "로그인돼 있지 않아" in outcome["stages"]["preflight"]["note"]
    assert outcome["stages"]["NEW_ISSUE"]["status"] == "skipped" and h.calls == []


def test_auth_failure_record_is_cleared_when_login_is_confirmed(tmp_path, monkeypatch):
    """로그인 방식은 토큰 지문이 늘 같아, 그대로 두면 인증 실패 기록이 영영 풀리지 않는다."""
    h, _ = _harness(tmp_path, monkeypatch, LOGGED_IN)
    h.run(DAY1, runner=None)
    stale = claude_limits.LimitInfo(claude_limits.KIND_AUTH, "", "OAuth token has expired",
                                    datetime.now(timezone.utc).isoformat(), token_fingerprint=claude_limits.token_fingerprint(""))
    h.store.set_state(h.cfg.state_key(pipeline.STATE_CLAUDE_LIMIT), claude_limits.dump(stale))
    h.issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    outcome = h.run(DAY2, runner=None)
    assert outcome["stages"]["NEW_ISSUE"]["status"] == "ok"
    assert h.store.get_state(h.cfg.state_key(pipeline.STATE_CLAUDE_LIMIT)) == ""
