"""Validates: REQ-AICALL-007."""
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
    assert client.get("/settings/ai/values").status_code == 200
    for route, body in [("/settings/ai", {"ai.claude.effort": "high"}),
                        ("/settings/ai/reset", {"keys": ["ai.claude.effort"]})]:
        assert client.post(route, json=body, headers=headers).status_code == 403
    assert not (root / "data/app.db").exists()


def test_local_write_reset_and_all_or_nothing(settings_app):
    app, root = settings_app
    client = TestClient(app, client=("127.0.0.1", 50123))
    values = client.get("/settings/ai/values").json()
    assert values["can_edit"] is True
    assert len(values["settings"]) == 6
    comment = next(row for row in values["settings"] if row["key"] == "daily_qa.intelligence.comment_model")
    assert comment["choices"][0] == ""                        # 빈 값 = 가벼운 작업 모델 따름
    assert client.post("/settings/ai", json={"ai.claude.effort": "high", "app.port": "12"}).status_code == 422
    assert build_settings(root).get("ai.claude.effort") == "medium"
    response = client.post("/settings/ai", json={"ai.claude.effort": "high"})
    assert response.status_code == 200
    saved = next(row for row in response.json()["settings"] if row["key"] == "ai.claude.effort")
    assert saved["value"] == "high"
    assert saved["default"] == "medium"
    assert saved["source"] == "화면에서 바꿈"
    assert client.post("/settings/ai/reset", json={"keys": ["ai.claude.effort", "app.port"]}).status_code == 422
    assert build_settings(root).get("ai.claude.effort") == "high"
    response = client.post("/settings/ai/reset", json={"keys": ["ai.claude.effort"]})
    assert response.status_code == 200
    assert build_settings(root).get("ai.claude.effort") == "medium"
    assert len(response.json()["history"]) == 2


@pytest.mark.parametrize("payload", [{}, {"ai.claude.effort": 3}, {"ai.claude.models.light": "unknown"}, []])
def test_invalid_inputs(settings_app, payload):
    client = TestClient(settings_app[0], client=("::1", 50123))
    assert client.post("/settings/ai", json=payload).status_code == 422


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
