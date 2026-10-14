"""Claude 사용량 한도 증거 캡처 (SPEC REQ-QAINTEL-039, TEST-QAINTEL-024).

Validates: REQ-QAINTEL-039
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.modules.daily_qa import claude_limits, limit_evidence, pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "limit_evidence" / "usage_full.raw"
NOW = datetime(2026, 10, 14, 11, 26, 0, tzinfo=timezone.utc)   # 20:26 KST
SESSION = claude_limits.LimitInfo(claude_limits.KIND_SESSION, "2026-10-14T13:00:00+00:00", "You've hit your session limit · resets 10pm")


class FakeSettings:
    def __init__(self, root: Path, **values):
        self.root = root
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def full_screen() -> str:
    return limit_evidence.screen_text(FIXTURE.read_bytes())


def fake_runner(screen: str, calls: list | None = None, png: bytes = b"\x89PNG fake"):
    def run(work: Path, command: str, timeout: int) -> limit_evidence.Shot:
        if calls is not None:
            calls.append((command, timeout))
        path = work / "usage.png"
        path.write_bytes(png)
        return limit_evidence.Shot(path, screen)
    return run


@pytest.fixture
def settings(tmp_path):
    return FakeSettings(tmp_path, **{"daily_qa.limit_evidence.account_label": ""})


# -- 1. 실제 원본 출력에서 사용률 읽기 ----------------------------------------------------------


def test_real_cli_output_replays_to_usage_panel():
    screen = full_screen()
    assert "Current session" in screen and "Resets 12:29pm (Asia/Seoul)" in screen
    usage = limit_evidence.read_usage(screen)
    assert usage == {"session": 100, "week": 62}
    assert limit_evidence.full_limit(usage) == "session"


def test_full_limit_prefers_session_then_week_and_ignores_partial():
    assert limit_evidence.full_limit({"session": 40, "week": 100}) == "week"
    assert limit_evidence.full_limit({"session": 99, "week": 62}) == ""
    assert limit_evidence.full_limit({}) == ""


# -- 2. 파일 이름과 계정 표시 -------------------------------------------------------------------


def test_file_stem_uses_kst_label_and_limit_name():
    assert limit_evidence.file_stem(NOW, "Claude13", "session") == "스크린샷 2026-10-14 202600_Claude13_세션한도"
    assert limit_evidence.file_stem(NOW, "Claude13", "week").endswith("_Claude13_주간한도")
    assert limit_evidence.file_stem(NOW, "Claude13", "").endswith("_사용량한도")


def test_account_label_from_cli_login_email_or_setting(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "claude13@vieworks.com"}}), encoding="utf-8")
    assert limit_evidence.account_label(FakeSettings(tmp_path), claude_home=tmp_path) == "Claude13"
    configured = FakeSettings(tmp_path, **{"daily_qa.limit_evidence.account_label": "Claude 12/계정"})
    assert limit_evidence.account_label(configured, claude_home=tmp_path) == "Claude_12_계정"
    assert limit_evidence.account_label(FakeSettings(tmp_path), claude_home=tmp_path / "none") == "Claude"


# -- 3. 저장·중복 방지·건너뛰기 -----------------------------------------------------------------


def test_capture_saves_png_and_screen_text(settings, tmp_path):
    calls: list = []
    result = limit_evidence.capture(SESSION, settings=settings, claude_command="/usr/bin/claude",
                                    runner=fake_runner(full_screen(), calls), now=NOW, claude_home=tmp_path)
    assert result.saved and calls == [("/usr/bin/claude", 60)]
    folder = tmp_path / "data" / "limit_evidence"
    png = folder / "스크린샷 2026-10-14 202600_Claude_세션한도.png"
    assert png.read_bytes() == b"\x89PNG fake"   # 찍은 그대로 복사한다
    text = png.with_suffix(".txt").read_text(encoding="utf-8")
    assert "Current session" in text and "100% used" in text
    assert "You've hit your session limit" in text and "세션 100% · 주간 62%" in text
    assert not (folder / limit_evidence.LOCK).exists()


def test_same_limit_is_captured_once(settings, tmp_path):
    calls: list = []
    runner = fake_runner(full_screen(), calls)
    assert limit_evidence.capture(SESSION, settings=settings, runner=runner, now=NOW, claude_home=tmp_path).saved
    again = limit_evidence.capture(SESSION, settings=settings, runner=runner, now=NOW, claude_home=tmp_path)
    assert (again.saved, again.reason, len(calls)) == (False, "already_captured", 1)
    weekly = claude_limits.LimitInfo(claude_limits.KIND_WEEKLY, "2026-10-17T15:59:00+00:00", "Weekly limit reached")
    assert limit_evidence.capture(weekly, settings=settings, runner=runner, now=NOW, claude_home=tmp_path).saved
    assert len(calls) == 2


def test_screen_below_100_percent_is_not_saved(settings, tmp_path):
    partial = full_screen().replace("100% used", "37% used")
    result = limit_evidence.capture(SESSION, settings=settings, runner=fake_runner(partial), now=NOW, claude_home=tmp_path)
    assert (result.saved, result.reason) == (False, "not_full")
    assert not list((tmp_path / "data" / "limit_evidence").glob("*.png"))


def test_busy_disabled_auth_and_missing_tools_skip(settings, tmp_path, monkeypatch):
    folder = tmp_path / "data" / "limit_evidence"
    folder.mkdir(parents=True)
    (folder / limit_evidence.LOCK).write_text("", encoding="utf-8")
    assert limit_evidence.capture(SESSION, settings=settings, runner=fake_runner(full_screen()), now=NOW).reason == "busy"

    off = FakeSettings(tmp_path, **{"daily_qa.limit_evidence.enabled": False})
    assert limit_evidence.capture(SESSION, settings=off, runner=fake_runner(full_screen())).reason == "disabled"

    auth = claude_limits.LimitInfo(claude_limits.KIND_AUTH, "", "OAuth token has expired")
    assert limit_evidence.capture(auth, settings=settings, runner=fake_runner(full_screen())).reason == "kind"

    monkeypatch.setattr(limit_evidence.shutil, "which", lambda name: None)
    assert limit_evidence.capture(SESSION, settings=settings).reason == "tools_missing"


def test_runner_failure_never_raises(settings, tmp_path):
    def broken(work, command, timeout):
        raise RuntimeError("xvfb died")

    result = limit_evidence.capture(SESSION, settings=settings, runner=broken, now=NOW)
    assert (result.saved, result.reason) == (False, "error:RuntimeError")
    timeout = limit_evidence.capture(SESSION, settings=settings, runner=lambda *a: limit_evidence.Shot(None, reason="timeout"))
    assert timeout.reason == "timeout"
    assert not (tmp_path / "data" / "limit_evidence" / limit_evidence.LOCK).exists()


# -- 4. 점검 파이프라인 연결 --------------------------------------------------------------------


def test_pipeline_captures_once_when_limit_is_hit(tmp_path, monkeypatch):
    from tests.test_qa_intel_failures import DAY1, DAY2, harness, limited, new_issue

    seen: list = []
    monkeypatch.setattr(pipeline, "capture_limit_evidence", lambda limit, cfg: seen.append((limit.kind, cfg.claude_command)))
    h = harness(tmp_path)
    h.run(DAY1)
    h.issues.append(new_issue("VP-200"))
    h.outcome_override = limited("Weekly limit reached ∙ resets Oct 6, 9am (Asia/Seoul)", DAY2)
    h.run(DAY2)
    assert seen == [(claude_limits.KIND_WEEKLY, h.cfg.claude_command)]


def test_pipeline_wrapper_passes_cli_command(monkeypatch):
    class Cfg:
        claude_command = "/home/ubuntu/.local/bin/claude"
        claude_token = "synthetic-test-token"

    # conftest 가 바꿔 끼운 것을 되돌려 실제 연결 함수를 부른다.
    monkeypatch.undo()
    seen: list = []
    monkeypatch.setattr(limit_evidence, "capture", lambda limit, **kwargs: seen.append(kwargs))
    pipeline.capture_limit_evidence(SESSION, Cfg())
    assert seen == [{"claude_command": "/home/ubuntu/.local/bin/claude", "claude_token": "synthetic-test-token"}]


# -- 5. 웹 화면 --------------------------------------------------------------------------------


def test_web_lists_and_serves_evidence_only_inside_folder(tmp_path, monkeypatch):
    from app.main import app

    monkeypatch.setattr(limit_evidence, "evidence_dir", lambda settings=None: tmp_path)
    (tmp_path / "스크린샷 2026-10-14 202600_Claude13_세션한도.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "스크린샷 2026-10-14 202600_Claude13_세션한도.txt").write_text("Current session 100% used", encoding="utf-8")
    (tmp_path / "episodes.json").write_text("{}", encoding="utf-8")
    client = TestClient(app)

    response = client.get("/claude/limit-evidence.json")
    # Windows PowerShell 5.1 uses the declared charset for non-ASCII file names.
    assert "charset=utf-8" in response.headers["content-type"]
    items = response.json()["items"]
    assert [(item["name"], item["limit"]) for item in items] == [("스크린샷 2026-10-14 202600_Claude13_세션한도.png", "세션한도")]
    page = client.get("/claude/limit-evidence")
    assert page.status_code == 200 and "세션한도" in page.text and "/claude/limit-evidence/files/" in page.text
    png = client.get(f"/claude/limit-evidence/files/{items[0]['name']}")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content == b"\x89PNG fake"
    assert client.get("/claude/limit-evidence/files/episodes.json").status_code == 404
    assert client.get("/claude/limit-evidence/files/..%2Fconfig.yaml").status_code == 404


def test_capture_uses_analysis_authentication(settings, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(limit_evidence, 'tools_missing', lambda: [])
    def terminal(work, command, timeout, *, claude_token=''):
        seen.append(claude_token)
        return fake_runner(full_screen())(work, command, timeout)
    monkeypatch.setattr(limit_evidence, 'run_xterm', terminal)
    result = limit_evidence.capture(SESSION, settings=settings, claude_token='synthetic-test-token', now=NOW, claude_home=tmp_path)
    assert result.saved and seen == ['synthetic-test-token']


def test_unknown_reset_does_not_suppress_later_limit(settings, tmp_path):
    from datetime import timedelta
    unknown = claude_limits.LimitInfo(claude_limits.KIND_UNKNOWN, '', 'Usage limit reached')
    kwargs = dict(settings=settings, runner=fake_runner(full_screen()), claude_home=tmp_path)
    assert limit_evidence.capture(unknown, now=NOW, **kwargs).saved
    assert limit_evidence.capture(unknown, now=NOW + timedelta(days=1), **kwargs).saved


def test_same_reset_for_different_account_is_not_suppressed(tmp_path):
    one = FakeSettings(tmp_path, **{'daily_qa.limit_evidence.account_label': 'Claude12'})
    two = FakeSettings(tmp_path, **{'daily_qa.limit_evidence.account_label': 'Claude13'})
    kwargs = dict(runner=fake_runner(full_screen()), now=NOW, claude_home=tmp_path)
    assert limit_evidence.capture(SESSION, settings=one, **kwargs).saved
    assert limit_evidence.capture(SESSION, settings=two, **kwargs).saved


def test_usage_environment_allows_usage_lookup_without_unrelated_secrets(monkeypatch):
    monkeypatch.setenv('SMTP_PASSWORD', 'synthetic-private')
    env = limit_evidence.terminal_env('synthetic-token')
    assert env['CLAUDE_CODE_OAUTH_TOKEN'] == 'synthetic-token'
    assert 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC' not in env
    assert 'SMTP_PASSWORD' not in env
    assert env['DISABLE_AUTOUPDATER'] == '1'
