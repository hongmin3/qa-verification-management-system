"""Validates: REQ-AICALL-007, REQ-WEB-006."""
from fastapi.testclient import TestClient

from test_settings_api import settings_app


def test_remote_screen_is_readonly(settings_app):
    client = TestClient(settings_app[0], client=("192.0.2.1", 50123))
    response = client.get("/settings/ai")
    assert response.status_code == 200
    assert 'id="save-settings" disabled' in response.text
    assert 'id="reset-settings" disabled' in response.text
    assert "SSH 터널" in response.text
    assert "다음 실행부터 적용" in response.text
    assert 'data-key="ai.claude.effort"' in response.text


def test_local_screen_and_running_notice(settings_app):
    from app.core.storage import Storage
    app, root = settings_app
    storage = Storage(root / "data/app.db")
    storage.daily_qa_create_run("running-example", False)
    client = TestClient(app, client=("127.0.0.1", 50123))
    response = client.get("/settings/ai")
    assert response.status_code == 200
    assert 'id="save-settings" disabled' not in response.text
    assert "running-example" in response.text
    assert "이전 값" in response.text
