"""Validates: REQ-AICALL-007, REQ-WEB-006, REQ-WEB-008."""
import re

from fastapi.testclient import TestClient

from test_settings_api import client, client_admin, remote_client, save_override, settings_app, _fresh_admin_state  # noqa: F401
from app.core.config import build_settings
from app.web import settings_router

SECTIONS = ["관리자 메일", "자동 보고 메일 받는 사람", "메일 시험 발송", "AI 모델과 사용률 기준",
            "메일 링크 서버 주소", "관리자 비밀번호 변경", "변경 이력"]


def test_anonymous_screen_is_readonly_with_login_form(remote_client):
    response = remote_client.get("/settings")
    assert response.status_code == 200
    text = response.text
    assert '<form method="post" action="/settings/login"' in text and 'name="password"' in text
    assert "SSH 터널" in text
    assert "다음 실행부터 적용" in text
    assert 'data-key="ai.claude.effort"' in text
    assert "disabled" in text
    for section in ("관리자 메일", "자동 보고 메일 받는 사람", "AI 모델과 사용률 기준", "변경 이력"):
        assert section in text
    assert 'action="/settings"' not in text and 'action="/settings/test-mail"' not in text


def test_anonymous_screen_masks_addresses(monkeypatch, remote_client, settings_app):
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "honest.person@example.test")
    save_override(build_settings(settings_app[1]), "daily_qa.report_recipients", "other.person@example.test")
    settings_router.reload_settings()
    text = remote_client.get("/settings").text
    assert "honest.person@example.test" not in text and "other.person@example.test" not in text
    assert "hon***@example.test" in text and "oth***@example.test" in text


def test_admin_screen_has_every_section_and_plain_forms(client_admin):
    text = client_admin.get("/settings").text
    for section in SECTIONS:
        assert section in text, section
    assert "<script" not in text[text.index("<main"):]          # 화면 본문에 자바스크립트가 없다. 없어도 읽고 저장한다
    for action in ("/settings", "/settings/test-mail", "/settings/password", "/settings/logout", "/settings/reset"):
        assert f'action="{action}"' in text, action
    assert all(form.endswith('method="post"') or 'method="post"' in form for form in re.findall(r"<form[^>]*>", text))
    assert 'name="daily_qa.usage_guard.five_hour_percent"' in text and 'name="daily_qa.usage_guard.seven_day_percent"' in text
    assert 'name="daily_qa.review_base_url"' in text
    assert "per_run" not in text and "per_day" not in text             # 호출 횟수 상한은 더 이상 읽지 않는다
    assert "다음 실행부터 적용" in text
    assert "비밀번호를 바꾸라" not in text and "초기 비밀번호" not in text


def test_admin_inputs_hold_raw_addresses_and_nothing_else_does(monkeypatch, client_admin, settings_app):
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "honest.person@example.test")
    client_admin.post("/settings", json={"daily_qa.report_recipients": "first.person@example.test, second.person@example.test"})
    text = client_admin.get("/settings").text
    inputs = re.findall(r"<(?:input|textarea)[^>]*>(?:[^<]*</textarea>)?", text)
    with_raw = [item for item in inputs if "first.person@example.test" in item]
    assert len(with_raw) == 1 and 'name="daily_qa.report_recipients"' in with_raw[0]
    outside = re.sub(r"<(?:input|textarea)[^>]*>(?:[^<]*</textarea>)?", "", text)
    assert "first.person@example.test" not in outside and "second.person@example.test" not in outside
    assert "honest.person@example.test" not in outside
    history = text[text.index("변경 이력"):]
    assert "fir***@example.test" in history


def test_form_post_saves_and_redirects_back_to_the_section(client_admin, settings_app):
    response = client_admin.post("/settings", data={
        "daily_qa.usage_guard.five_hour_percent": "72", "daily_qa.usage_guard.seven_day_percent": "88",
        "daily_qa.savings.analysis_model": "claude-sonnet-5-5", "_section": "ai"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings?saved=1") and response.headers["location"].endswith("#ai")
    settings = build_settings(settings_app[1])
    assert settings.get("daily_qa.usage_guard.five_hour_percent") == "72"
    assert settings.get("daily_qa.usage_guard.seven_day_percent") == "88"


def test_form_post_with_bad_value_shows_reason_and_saves_nothing(client_admin, settings_app):
    response = client_admin.post("/settings", data={"daily_qa.usage_guard.five_hour_percent": "49", "_section": "ai"})
    assert response.status_code == 422
    assert "50" in response.text and "100" in response.text and "설정" in response.text
    assert build_settings(settings_app[1]).overrides == {}


def test_form_without_login_is_refused(remote_client):
    assert remote_client.post("/settings", data={"daily_qa.usage_guard.five_hour_percent": "72"}).status_code == 403
    assert remote_client.post("/settings/password", data={"password": "new-password-1"}).status_code == 403
    assert remote_client.post("/settings/logout").status_code in (303, 403)


def test_logout_clears_cookie(client_admin):
    response = client_admin.post("/settings/logout")
    assert response.status_code == 303
    assert "qa_admin=" in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]
    client_admin.cookies.clear()
    assert 'name="daily_qa.report_recipients"' not in client_admin.get("/settings").text


def test_local_screen_and_running_notice(settings_app):
    from app.core.storage import Storage
    app, root = settings_app
    storage = Storage(root / "data/app.db")
    storage.daily_qa_create_run("running-example", False)
    local = TestClient(app, client=("127.0.0.1", 50123))
    response = local.get("/settings")
    assert response.status_code == 200
    assert 'name="daily_qa.usage_guard.five_hour_percent"' in response.text and 'action="/settings/login"' not in response.text
    assert "running-example" in response.text
    assert "이전 값" in response.text


def test_login_notices_are_fixed_phrases(remote_client):
    assert "비밀번호가 맞지 않습니다" in remote_client.get("/settings?error=login").text
    assert "10분" in remote_client.get("/settings?error=locked").text
    assert "<script>alert" not in remote_client.get("/settings?error=<script>alert(1)</script>").text


def test_test_mail_result_is_shown_without_raw_addresses(monkeypatch, client_admin):
    from app.core import notifier
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "honest.person@example.test")
    settings_router.reload_settings()
    monkeypatch.setattr(notifier, "send_test", lambda kind, recipients: {
        "status": "sent", "recipients": 1, "recipient_masks": ["hon***@example.test"]})
    response = client_admin.post("/settings/test-mail", data={"kind": "report", "target": "admin"})
    assert response.status_code == 200
    assert "시험 메일을 보냈습니다" in response.text and "hon***@example.test" in response.text
    monkeypatch.setattr(notifier, "send_test", lambda kind, recipients: {"status": "not_configured"})
    assert "메일 설정이 없어 보내지 않았습니다" in client_admin.post(
        "/settings/test-mail", data={"kind": "report", "target": "admin"}).text
