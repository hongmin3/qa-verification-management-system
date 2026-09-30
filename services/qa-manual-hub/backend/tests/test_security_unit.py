"""Security checks that run without PostgreSQL.

These tests need neither a database nor a login, so they override the
conftest's autouse ``clean_tables`` fixture (which would otherwise create the
schema) with a no-op.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_tables():
    """No database here: replace the conftest fixture of the same name."""
    yield


def _request(headers: dict[str, str], peer: str | None = "10.0.0.9"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 50000) if peer else None,
    }
    return Request(scope)


# Validates: REQ-HUBAUTH-013
def test_client_ip_prefers_the_x_real_ip_set_by_nginx():
    from app.audit import client_ip

    request = _request(
        {"X-Real-IP": "192.0.2.10", "X-Forwarded-For": "203.0.113.66, 192.0.2.10"}
    )
    assert client_ip(request) == "192.0.2.10"


# Validates: REQ-HUBAUTH-013
def test_client_ip_ignores_a_forged_first_forwarded_for_value():
    """nginx appends the real peer with ``$proxy_add_x_forwarded_for``; the first
    value is whatever the browser sent and must not be trusted."""
    from app.audit import client_ip

    request = _request({"X-Forwarded-For": "203.0.113.66, 192.0.2.10"})
    assert client_ip(request) == "192.0.2.10"


# Validates: REQ-HUBAUTH-013
def test_client_ip_falls_back_to_the_socket_peer():
    from app.audit import client_ip

    assert client_ip(_request({})) == "10.0.0.9"
    assert client_ip(_request({}, peer=None)) is None
    assert client_ip(None) is None


@pytest.fixture
def anon_client(app_settings):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


# Validates: REQ-HUB-016
@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_api_documentation_requires_login(anon_client, path):
    response = anon_client.get(path)
    assert response.status_code == 401
    assert response.json()["detail"] == "로그인이 필요합니다."


# Validates: REQ-HUB-015
def test_health_stays_public_without_a_database(anon_client):
    assert anon_client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Session and permission checks against a stand-in database.
#
# ``get_current_user`` only needs ``db.scalar`` (to find the session) and
# ``db.commit``; the fake below answers those so the checks run without
# PostgreSQL.  The PostgreSQL-backed counterparts live in test_auth.py and
# test_users.py.
# --------------------------------------------------------------------------- #
class _FakeDb:
    def __init__(self, record):
        self.record = record
        self.commits = 0

    def scalar(self, *_args, **_kwargs):
        return self.record

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _fake_session(*, minutes_since_seen: int, admin: bool = False, must_change: bool = False):
    import uuid
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    now = datetime.now(UTC)
    user = SimpleNamespace(
        id=uuid.uuid4(),
        login_id="hong",
        display_name="홍길동",
        role="admin" if admin else "user",
        is_admin=admin,
        is_active=True,
        must_change_password=must_change,
        last_login_at=now,
        created_at=now,
        updated_at=now,
    )
    return SimpleNamespace(
        user=user,
        revoked_at=None,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now - timedelta(minutes=minutes_since_seen),
    )


@pytest.fixture
def fake_db_client(app_settings):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    holder: dict = {}

    def _use(record):
        holder["db"] = _FakeDb(record)
        return holder["db"]

    def _override():
        yield holder["db"]

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as c:
            c.cookies.set(app_settings.session_cookie_name, "tok")
            yield c, _use
    finally:
        app.dependency_overrides.pop(get_db, None)


def _max_age(set_cookie: str) -> int:
    for part in set_cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "max-age":
            return int(value)
    raise AssertionError(f"no Max-Age in {set_cookie!r}")


# Validates: REQ-HUBAUTH-003
def test_sliding_refresh_resends_the_cookie_with_a_full_lifetime(fake_db_client, app_settings):
    client, use = fake_db_client
    db = use(_fake_session(minutes_since_seen=20))
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert db.commits == 1
    cookie = response.headers.get("set-cookie", "")
    assert cookie.startswith(f"{app_settings.session_cookie_name}=tok")
    assert _max_age(cookie) == app_settings.session_lifetime_hours * 3600
    assert "httponly" in cookie.lower()


# Validates: REQ-HUBAUTH-003
def test_no_cookie_is_sent_when_the_session_was_not_extended(fake_db_client):
    client, use = fake_db_client
    db = use(_fake_session(minutes_since_seen=1))
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert db.commits == 0
    assert "set-cookie" not in response.headers


# Validates: REQ-HUBAUTH-006
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/users", {"login_id": "kim", "display_name": "김", "password": "x"}),
        ("patch", "/api/users/00000000-0000-0000-0000-000000000001", {"display_name": "새 이름"}),
        ("post", "/api/users/00000000-0000-0000-0000-000000000001/reset-password", {"new_password": "x"}),
        ("post", "/api/products", {"name": "New Product"}),
        ("patch", "/api/products/00000000-0000-0000-0000-000000000001", {"name": "Renamed"}),
        ("post", "/api/categories", {"name": "New Category"}),
        ("patch", "/api/categories/00000000-0000-0000-0000-000000000001", {"name": "Renamed"}),
    ],
)
def test_admin_on_a_temporary_password_cannot_manage(fake_db_client, method, path, body):
    client, use = fake_db_client
    use(_fake_session(minutes_since_seen=1, admin=True, must_change=True))
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 428, response.text
    assert response.json()["detail"] == "비밀번호를 먼저 변경해야 합니다."


# Validates: REQ-HUBAUTH-005
def test_non_admin_still_gets_403_before_the_password_check(fake_db_client):
    client, use = fake_db_client
    use(_fake_session(minutes_since_seen=1, admin=False, must_change=True))
    response = client.post("/api/products", json={"name": "New Product"})
    assert response.status_code == 403
    assert response.json()["detail"] == "관리자 권한이 필요합니다."
