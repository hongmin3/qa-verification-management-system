"""Validates: REQ-AICALL-007, REQ-WEB-008, NFR-SEC-004."""
import shutil
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import ROOT, build_settings


@pytest.fixture
def settings_app(tmp_path, monkeypatch):
    from app.web import settings_router
    shutil.copyfile(ROOT / "config.yaml", tmp_path / "config.yaml")
    current = [build_settings(tmp_path)]
    monkeypatch.setattr(settings_router, "get_settings", lambda: current[0])
    def reload():
        current[0] = build_settings(tmp_path)
        return current[0]
    monkeypatch.setattr(settings_router, "reload_settings", reload)
    app = FastAPI()
    app.include_router(settings_router.router)
    return app, tmp_path


@pytest.mark.parametrize("address,headers", [
    ("192.0.2.1", {}), ("testclient", {}),
    ("192.0.2.1", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", {"X-Forwarded-For": "192.0.2.1"}),
    ("127.0.0.1", {"Forwarded": "for=127.0.0.1"}),
    ("127.0.0.1", {"X-Forwarded-Proto": "http"}),
    ("127.0.0.1", {"X-Forwarded-Unknown": ""}),
    ("127.0.0.1", {"Origin": "https://other.example"}),
])
def test_remote_and_proxy_writes_forbidden(settings_app, address, headers):
    app, root = settings_app
    client = TestClient(app, client=(address, 50123))
    assert client.get("/settings/values").status_code == 200
    for route, body in [("/settings", {"ai.claude.effort": "high"}),
                        ("/settings/reset", {"keys": ["ai.claude.effort"]})]:
        assert client.post(route, json=body, headers=headers).status_code == 403
    assert not (root / "data/app.db").exists()


def test_local_write_reset_and_all_or_nothing(settings_app):
    app, root = settings_app
    client = TestClient(app, client=("127.0.0.1", 50123))
    values = client.get("/settings/values").json()
    assert values["can_edit"] is True
    assert len(values["settings"]) == 13
    comment = next(row for row in values["settings"] if row["key"] == "daily_qa.intelligence.comment_model")
    assert comment["choices"][0] == ""                        # 빈 값 = 가벼운 작업 모델 따름
    assert client.post("/settings", json={"ai.claude.effort": "high", "app.port": "12"}).status_code == 422
    assert build_settings(root).get("ai.claude.effort") == "medium"
    response = client.post("/settings", json={"ai.claude.effort": "high"})
    assert response.status_code == 200
    saved = next(row for row in response.json()["settings"] if row["key"] == "ai.claude.effort")
    assert saved["value"] == "high"
    assert saved["default"] == "medium"
    assert saved["source"] == "화면에서 바꿈"
    assert client.post("/settings/reset", json={"keys": ["ai.claude.effort", "app.port"]}).status_code == 422
    assert build_settings(root).get("ai.claude.effort") == "high"
    response = client.post("/settings/reset", json={"keys": ["ai.claude.effort"]})
    assert response.status_code == 200
    assert build_settings(root).get("ai.claude.effort") == "medium"
    assert len(response.json()["history"]) == 2


@pytest.mark.parametrize("payload", [{}, {"ai.claude.effort": 3}, {"ai.claude.models.light": "unknown"}, []])
def test_invalid_inputs(settings_app, payload):
    client = TestClient(settings_app[0], client=("::1", 50123))
    assert client.post("/settings", json=payload).status_code == 422


def test_server_entrypoint_retains_tcp_peer(monkeypatch):
    import uvicorn
    from app import serve
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(serve, "app_bind", lambda: ("127.0.0.1", 24357))
    serve.main()
    assert calls[0]["proxy_headers"] is False


def test_nginx_core_proxy_always_marks_forwarding():
    # 설정 문서 검사다. 실제 nginx 실행 여부는 이 테스트로 판단하지 않는다.
    conf = (ROOT / "deploy/nginx/qa-platform.conf").read_text(encoding="utf-8")
    core = re.search(r"location\s+/\s*\{([^}]+)\}", conf)
    assert core
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in core[1]
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in core[1]


# ---- 설정 탭과 관리자 로그인 (REQ-WEB-008, NFR-SEC-004) ----

@pytest.fixture
def client(settings_app):
    """서버에 직접 접속한 것과 같은 루프백 클라이언트. 리다이렉트를 따라가지 않는다."""
    return TestClient(settings_app[0], client=("127.0.0.1", 50123), follow_redirects=False)


@pytest.fixture
def remote_client(settings_app):
    return TestClient(settings_app[0], client=("192.0.2.1", 50123), follow_redirects=False)


@pytest.fixture
def client_admin(settings_app):
    """원격 접속에서 처음 비밀번호로 로그인한 클라이언트."""
    http = TestClient(settings_app[0], client=("192.0.2.1", 50123), follow_redirects=False)
    assert http.post("/settings/login", data={"password": "1"}).status_code == 303
    return http


@pytest.fixture(autouse=True)
def _fresh_admin_state():
    from app.core import admin_auth
    admin_auth.reset_lockout()


def save_override(settings, key, value):
    from app.core.setting_overrides import SettingOverrides
    SettingOverrides(settings.root / "data/app.db").save({key: value}, settings, "test")


def test_login_with_initial_password_sets_cookie(client):
    response = client.post("/settings/login", data={"password": "1"})
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "qa_admin=" in cookie and "HttpOnly" in cookie
    assert "samesite=strict" in cookie.lower()
    assert "Path=/" in cookie and "Max-Age=28800" in cookie


def test_wrong_password_sets_no_cookie_and_locks_after_five(remote_client):
    for _ in range(5):
        response = remote_client.post("/settings/login", data={"password": "nope"})
        assert response.status_code == 303 and "set-cookie" not in response.headers
    locked = remote_client.post("/settings/login", data={"password": "1"})
    assert locked.status_code == 303 and "set-cookie" not in locked.headers
    assert "locked" in locked.headers["location"]


def test_login_from_other_origin_is_refused(remote_client):
    response = remote_client.post("/settings/login", data={"password": "1"}, headers={"Origin": "https://other.example"})
    assert response.status_code == 403


def test_save_without_admin_cookie_is_refused(remote_client, settings_app):
    assert remote_client.post("/settings", json={"daily_qa.report_recipients": "x@example.test"}).status_code == 403
    assert not (settings_app[1] / "data/app.db").exists()


def test_forged_or_expired_cookie_is_refused(remote_client):
    remote_client.cookies.set("qa_admin", "9999999999.deadbeef")
    assert remote_client.post("/settings", json={"daily_qa.usage_guard.five_hour_percent": 70}).status_code == 403
    remote_client.cookies.set("qa_admin", "1.deadbeef")
    assert remote_client.post("/settings", json={"daily_qa.usage_guard.five_hour_percent": 70}).status_code == 403


def test_admin_cookie_allows_remote_save_and_reset(client_admin, settings_app):
    response = client_admin.post("/settings", json={"daily_qa.usage_guard.five_hour_percent": 70,
                                                    "daily_qa.review_base_url": "http://10.13.0.222:24357"})
    assert response.status_code == 200
    settings = build_settings(settings_app[1])
    assert settings.get("daily_qa.usage_guard.five_hour_percent") == "70"
    assert settings.get("daily_qa.review_base_url") == "http://10.13.0.222:24357"
    reset = client_admin.post("/settings/reset", json={"keys": ["daily_qa.usage_guard.five_hour_percent"]})
    assert reset.status_code == 200
    assert build_settings(settings_app[1]).get("daily_qa.usage_guard.five_hour_percent") == 80


def test_admin_cookie_from_other_origin_is_refused(client_admin):
    response = client_admin.post("/settings", json={"daily_qa.usage_guard.five_hour_percent": 70},
                                 headers={"Origin": "https://other.example"})
    assert response.status_code == 403


def test_local_access_saves_without_login_and_records_client(client):
    assert client.post("/settings", json={"daily_qa.usage_guard.seven_day_percent": 95}).status_code == 200
    history = client.get("/settings/values").json()["history"]
    assert history[0]["client"] == "127.0.0.1"


@pytest.mark.parametrize("payload", [
    {"daily_qa.report_recipients": ""},
    {"daily_qa.report_recipients": "not-an-address"},
    {"daily_qa.report_recipients": ", ".join(f"p{i}@example.test" for i in range(11))},
    {"notifications.admin_email": "a@example.test, b@example.test"},
    {"notifications.admin_email": "bad address@example.test"},
    {"daily_qa.usage_guard.five_hour_percent": 49},
    {"daily_qa.usage_guard.seven_day_percent": 101},
    {"daily_qa.usage_guard.five_hour_percent": "abc"},
    {"daily_qa.usage_guard.five_hour_percent": True},
    {"daily_qa.usage_guard.five_hour_percent": 80.5},
    {"daily_qa.review_base_url": "ftp://server"},
    {"daily_qa.review_base_url": "http://"},
    {"daily_qa.review_base_url": "javascript:alert(1)"},
    {"daily_qa.review_base_url": "http://a b"},
    {"daily_qa.savings.analysis_model": "unknown-model"},
    {"daily_qa.savings.per_run": 5},
    {"admin.password_hash": "pbkdf2_sha256$600000$00$00"},
    {"daily_qa.usage_guard.five_hour_percent": 70, "daily_qa.review_base_url": "ftp://server"},
])
def test_invalid_new_values_are_refused_whole(client_admin, settings_app, payload):
    response = client_admin.post("/settings", json=payload)
    assert response.status_code == 422
    assert not (settings_app[1] / "data/app.db").exists() or build_settings(settings_app[1]).overrides == {}


def test_recipients_are_normalised_and_deduplicated(client_admin, settings_app):
    response = client_admin.post("/settings", json={
        "daily_qa.report_recipients": " A@example.test ;b@example.test,a@example.test\n"})
    assert response.status_code == 200
    assert build_settings(settings_app[1]).get("daily_qa.report_recipients") == "A@example.test, b@example.test"


def test_report_recipients_override_secret(monkeypatch, settings_app):
    from app.modules.daily_qa import settings as daily_settings
    monkeypatch.setenv("DAILY_QA_EMAIL_TO", "daily@example.test")
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "notify@example.test, second@example.test")
    settings = build_settings(settings_app[1])
    assert daily_settings.report_recipients(settings) == ("daily@example.test",)
    monkeypatch.delenv("DAILY_QA_EMAIL_TO")
    assert daily_settings.report_recipients(build_settings(settings_app[1])) == ("notify@example.test", "second@example.test")
    save_override(settings, "daily_qa.report_recipients", "a@example.test")
    chosen = build_settings(settings_app[1])
    assert daily_settings.report_recipients(chosen) == ("a@example.test",)
    assert daily_settings.load(chosen).email_to == ("a@example.test",)


def test_report_recipients_fall_back_to_admin_email(monkeypatch, settings_app):
    from app.modules.daily_qa import settings as daily_settings
    monkeypatch.delenv("DAILY_QA_EMAIL_TO", raising=False)
    monkeypatch.delenv("NOTIFY_EMAIL_TO", raising=False)
    settings = build_settings(settings_app[1])
    assert daily_settings.report_recipients(settings) == ()
    save_override(settings, "notifications.admin_email", "boss@example.test")
    assert daily_settings.report_recipients(build_settings(settings_app[1])) == ("boss@example.test",)


def test_admin_email_override_wins_over_first_notify_address(monkeypatch, settings_app):
    from app.core import notifier
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "notify@example.test, second@example.test")
    settings = build_settings(settings_app[1])
    assert notifier.admin_recipients(settings) == ("notify@example.test",)
    save_override(settings, "notifications.admin_email", "boss@example.test")
    assert notifier.admin_recipients(build_settings(settings_app[1])) == ("boss@example.test",)


def test_usage_guard_override_reaches_daily_settings(settings_app):
    from app.modules.daily_qa import settings as daily_settings
    save_override(build_settings(settings_app[1]), "daily_qa.usage_guard.five_hour_percent", "65")
    cfg = daily_settings.load(build_settings(settings_app[1]))
    assert cfg.usage_guard_five_hour_percent == 65
    assert cfg.usage_guard_seven_day_percent == 90


def test_password_hash_never_returned(client_admin):
    client_admin.post("/settings/password", json={"password": "new-password-1"})
    values = client_admin.get("/settings/values")
    assert "pbkdf2" not in values.text and "new-password-1" not in values.text
    page = client_admin.get("/settings")
    assert "pbkdf2" not in page.text and "new-password-1" not in page.text


def test_change_password_replaces_initial_one(client_admin, settings_app, remote_client):
    assert client_admin.post("/settings/password", json={"password": "abc"}).status_code == 422     # 너무 짧다
    assert client_admin.post("/settings/password", json={"password": "new-password-1"}).status_code == 200
    from app.core import admin_auth
    settings = build_settings(settings_app[1])
    assert admin_auth.check_password("new-password-1", settings) is True
    assert admin_auth.check_password("1", settings) is False
    history = client_admin.get("/settings/values").json()["history"]
    assert history[0]["key"] == "admin.password_hash"
    assert "new-password-1" not in str(history) and "pbkdf2" not in str(history)
    assert remote_client.post("/settings/login", data={"password": "1"}).headers["location"].endswith("error=login")


def test_password_change_requires_admin(remote_client):
    assert remote_client.post("/settings/password", json={"password": "new-password-1"}).status_code == 403


def test_history_and_values_show_masked_addresses_only(client_admin):
    client_admin.post("/settings", json={"daily_qa.report_recipients": "honest.person@example.test, a@example.test",
                                          "notifications.admin_email": "boss.person@example.test"})
    text = client_admin.get("/settings/values").text
    assert "honest.person@example.test" not in text and "boss.person@example.test" not in text
    assert "hon***@example.test" in text and "a***@example.test" in text
    assert "boss.person" not in text


def test_old_ai_settings_url_redirects_to_settings_tab(remote_client):
    response = remote_client.get("/settings/ai")
    assert response.status_code == 303
    assert response.headers["location"] == "/settings#ai"


def test_test_mail_needs_admin_and_sends_only_to_chosen_group(monkeypatch, client_admin, remote_client):
    from app.core import notifier
    calls = []
    monkeypatch.setattr(notifier, "send_test", lambda kind, recipients: calls.append((kind, tuple(recipients))) or
                        {"status": "sent", "recipients": len(recipients), "recipient_masks": ["hon***@example.test"]})
    monkeypatch.setenv("NOTIFY_EMAIL_TO", "honest.person@example.test, second.person@example.test")
    from app.web import settings_router
    settings_router.reload_settings()
    assert remote_client.post("/settings/test-mail", json={"kind": "report", "target": "admin"}).status_code == 403
    assert calls == []
    response = client_admin.post("/settings/test-mail", json={"kind": "report", "target": "admin"})
    assert response.status_code == 200
    assert calls == [("report", ("honest.person@example.test",))]
    assert "honest.person@example.test" not in response.text
    assert client_admin.post("/settings/test-mail", json={"kind": "report", "target": "nobody"}).status_code == 422
    assert client_admin.post("/settings/test-mail", json={"kind": "bad-kind", "target": "admin"}).status_code == 422


def test_rows_show_code_defaults_for_keys_missing_from_config_file(client):
    rows = {row["key"]: row for row in client.get("/settings/values").json()["settings"]}
    assert rows["daily_qa.savings.analysis_model"]["value"] == "claude-sonnet-5-5"
    assert rows["daily_qa.savings.review_model"]["default"] == "claude-opus-5-5"
    assert rows["daily_qa.usage_guard.five_hour_percent"]["value"] == "80"
    assert rows["daily_qa.usage_guard.seven_day_percent"]["default"] == "90"
    assert "daily_qa.savings.per_run" not in rows and "admin.password_hash" not in rows
