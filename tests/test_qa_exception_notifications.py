"""Validates: REQ-DAILY-008, REQ-QAINTEL-025, REQ-QAINTEL-038. 알림은 추가 AI 호출 없이 만든다."""
from app.modules.daily_qa import report


def test_unexpected_run_failure_sends_one_notification(tmp_path, monkeypatch):
    from app.modules.daily_qa.agent_runner import FakeRunner
    from app.modules.daily_qa.pipeline import _Run, run_daily
    from app.modules.daily_qa.store import DailyQaStore
    from tests.daily_qa_fixtures import make_settings, rules_ok
    cfg = make_settings(tmp_path / "repo", tmp_path / "workspace")
    store = DailyQaStore(cfg.db_path)
    sent = []
    def crash(_):
        raise RuntimeError("synthetic failure")
    def sender(*args):
        sent.append(args)
        return {"status": "sent"}
    monkeypatch.setattr(_Run, "execute", crash)
    result = run_daily(cfg, store=store, runner=FakeRunner(), rules_state=rules_ok(tmp_path), send_email=sender)
    assert result["status"] == "FAILED" and result["email"]["status"] == "sent"
    assert len(sent) == 1 and "점검 실패" in sent[0][0] and "RuntimeError" in sent[0][1]


def test_scheduled_limit_notice_has_priority_and_weekly_resume():
    summary = {"findings": 1, "trigger": "scheduled", "pending_analysis_events": 12,
               "automation_policy": {"mode": "weekly"}, "claude_calls": 2,
               "claude_limit": {"kind": "SESSION", "label": "Claude 세션(5시간) 사용량 한도", "description": "한도 초과"}}
    subject, text, body = report.build_email("RUN", "일부 실패", {"FIXED_ISSUE": {"status": "limit"}},
                                            summary, "http://example.test/run", "", product="VXvue")
    assert "사용량 한도" in subject
    assert "대기 변경 12건" in text and "대기 변경 12건" in body
    assert "다음 주 첫 업무일" in text and "지금 실행" in body


def test_collection_failure_is_not_reported_as_no_change():
    subject, text, body = report.build_email("RUN", "일부 실패",
        {"collect_srs": {"status": "failed", "note": "ConnectError"}},
        {"findings": 0, "trigger": "scheduled"}, "/run", "")
    assert "수집 실패" in subject
    assert "수집 실패로 변경을 확인하지 못했습니다" in text
    assert "오늘 변경: 변경 없음" not in text
    assert "ConnectError" in body


def test_auth_failure_does_not_claim_usage_limit_resume():
    subject, text, _ = report.build_email("RUN", "일부 실패", {},
        {"trigger": "scheduled", "claude_limit": {"kind": "AUTH", "label": "Claude 인증 실패", "description": "인증 실패"},
         "automation_policy": {"mode": "daily"}}, "/run", "")
    assert "인증 실패" in subject
    assert "설정" in text and "한도가 풀린 뒤 자동 재개" not in text


def test_disabled_server_schedule_does_not_promise_automatic_resume():
    _, text, _ = report.build_email("RUN", "일부 실패", {},
        {"claude_limit": {"kind": "SESSION"}, "automation_policy": {"mode": "daily"},
         "automatic_resume_enabled": False}, "/run", "")
    assert "자동 재개합니다" not in text
    assert "지금 실행" in text
