"""Validates: REQ-VXVUECHATBOT-013, REQ-VXVUECHATBOT-014. Synthetic knowledge and tickets only."""
import base64
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.vxvue_chatbot import desktop
from tests.test_vxvue_desktop import server  # noqa: F401  (fixture reuse)

INSIDE = ("10.20.30.40", 50000)
OUTSIDE = ("8.8.8.8", 50000)


class Settings(SimpleNamespace):
    def get(self, dotted, default=None):
        return self.values.get(dotted, default)


@pytest.fixture
def app_root(server, monkeypatch):
    _, _, root = server
    settings = Settings(root=root, values={})
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(desktop.router, prefix="/vxvue-desktop")
    return app, root, settings


def client_for(app, address=INSIDE):
    return TestClient(app, client=address, base_url="http://qa.test:24357")


def bundle(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        enrollment = json.loads(archive.read("enrollment.json"))
        return manifest, enrollment, archive.namelist()


def enroll(http, ticket, **extra):
    body = {"ticket": ticket, "client": "desktop", "hostname": "PC-1", "username": "member", "bridge_version": "1.1.0", **extra}
    return http.post("/vxvue-desktop/enroll", json=body)


def call(client, token, method="tools/list", params=None, path="/vxvue-desktop/rpc", headers=None):
    message = {"jsonrpc": "2.0", "id": 7, "method": method}
    if params is not None:
        message["params"] = params
    return client.post(path, json=message, headers={"Authorization": "Bearer " + token, **(headers or {})})


def test_download_has_server_url_and_one_time_ticket_without_user_input(app_root):
    app, root, _ = app_root
    with client_for(app) as client:
        response = client.get("/vxvue-desktop/download")
        assert response.status_code == 200
        manifest, enrollment, names = bundle(response.content)
        assert sorted(names) == ["enrollment.json", "manifest.json", "server.cjs", "tools.json"]
        assert "user_config" not in manifest
        assert manifest["server"]["mcp_config"]["env"]["VXVUE_SERVER_URL"] == "http://qa.test:24357"
        assert "x" * 48 not in response.content.decode("latin1")
        assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", enrollment["ticket"])
        second = bundle(client.get("/vxvue-desktop/download").content)[1]
        assert second["ticket"] != enrollment["ticket"]
        first = enroll(client, enrollment["ticket"])
        assert first.status_code == 200, first.text
        token = first.json()["token"]
        assert call(client, token).status_code == 200
        reused = enroll(client, enrollment["ticket"])
        assert reused.status_code == 410 and reused.json()["code"] == "ticket_used"
    db = sqlite3.connect(root / "data/app.db")
    dump = "\n".join(db.iterdump())
    assert token not in dump and enrollment["ticket"] not in dump
    assert "PC-1" in dump


def test_public_url_setting_wins_over_request_host(app_root):
    app, _, settings = app_root
    settings.values["vxvue_desktop.public_url"] = "http://10.13.0.222:24357/"
    with client_for(app) as client:
        manifest, _, _ = bundle(client.get("/vxvue-desktop/download").content)
    assert manifest["server"]["mcp_config"]["env"]["VXVUE_SERVER_URL"] == "http://10.13.0.222:24357"


def test_outside_network_cannot_download_enroll_or_call(app_root):
    app, _, _ = app_root
    with client_for(app) as inside:
        ticket = bundle(inside.get("/vxvue-desktop/download").content)[1]["ticket"]
        token = enroll(inside, ticket).json()["token"]
    with client_for(app, OUTSIDE) as outside:
        assert outside.get("/vxvue-desktop/download").status_code == 403
        assert outside.get("/vxvue-desktop/claude-code/install.ps1").status_code == 403
        assert enroll(outside, "a" * 43).status_code == 403
        denied = call(outside, token)
        assert denied.status_code == 403 and denied.json()["code"] == "network_not_allowed"


def test_forwarded_address_is_trusted_only_from_local_proxy(app_root):
    app, _, _ = app_root
    with client_for(app, ("127.0.0.1", 40000)) as proxy:
        assert proxy.get("/vxvue-desktop/download", headers={"X-Real-IP": "8.8.8.8"}).status_code == 403
        assert proxy.get("/vxvue-desktop/download", headers={"X-Real-IP": "10.1.1.1"}).status_code == 200
    with client_for(app, OUTSIDE) as direct:
        assert direct.get("/vxvue-desktop/download", headers={"X-Real-IP": "10.1.1.1"}).status_code == 403


def test_expired_unknown_revoked_and_idle_credentials(app_root):
    app, root, settings = app_root
    from app.core.storage import Storage
    storage = Storage(root / "data/app.db")
    with client_for(app) as client:
        assert enroll(client, "b" * 43).json()["code"] == "ticket_unknown"
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        with sqlite3.connect(root / "data/app.db") as db:
            db.execute("UPDATE vxdesk_tickets SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
        assert enroll(client, ticket).json()["code"] == "ticket_expired"
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        issued = enroll(client, ticket).json()
        devices = storage.vxdesk_devices()
        assert [d["id"] for d in devices] == [issued["device_id"]]
        assert devices[0]["hostname"] == "PC-1" and devices[0]["address"] == INSIDE[0]
        assert "token_hash" not in devices[0]
        settings.values["vxvue_desktop.device_idle_days"] = 30
        with sqlite3.connect(root / "data/app.db") as db:
            db.execute("UPDATE vxdesk_devices SET last_used_at=?", ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),))
        idle = call(client, issued["token"])
        assert idle.status_code == 401 and idle.json()["code"] == "device_expired"
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        fresh = enroll(client, ticket).json()
        assert call(client, fresh["token"]).status_code == 200
        assert storage.vxdesk_revoke(fresh["device_id"]) is True
        revoked = call(client, fresh["token"])
        assert revoked.status_code == 401 and revoked.json()["code"] == "device_revoked"
        assert call(client, "c" * 43).json()["code"] == "unknown_credential"


def test_legacy_team_token_follows_setting(app_root):
    app, _, settings = app_root
    with client_for(app) as client:
        assert call(client, "x" * 48).status_code == 200
        settings.values["vxvue_desktop.accept_team_token"] = False
        assert call(client, "x" * 48).status_code == 401


def test_streamable_http_endpoint_for_claude_code(app_root):
    app, _, _ = app_root
    with client_for(app) as client:
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        token = enroll(client, ticket, client="claude-code").json()["token"]
        init = call(client, token, "initialize", {"protocolVersion": "2025-06-18"}, path="/vxvue-desktop/mcp",
                    headers={"Accept": "application/json, text/event-stream"})
        assert init.status_code == 200 and init.headers["content-type"].startswith("application/json")
        assert init.json()["result"]["serverInfo"]["name"] == "vxvue-ai"
        note = client.post("/vxvue-desktop/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers={"Authorization": "Bearer " + token})
        assert note.status_code == 202
        assert client.get("/vxvue-desktop/mcp", headers={"Authorization": "Bearer " + token}).status_code == 405
        assert client.post("/vxvue-desktop/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401


def test_claude_code_installer_embeds_fresh_ticket_and_managed_skill(app_root):
    app, _, _ = app_root
    with client_for(app) as client:
        response = client.get("/vxvue-desktop/claude-code/install.ps1")
        assert response.status_code == 200
        script = response.text
        assert "x" * 48 not in script
        assert "$Server = 'http://qa.test:24357'" in script
        assert '"$Server/vxvue-desktop/mcp"' in script and "claude mcp add --transport http --scope user vxvue-ai" in script
        assert not re.search(r"__(SERVER|TICKET|SKILL_BASE64|VERSION)__", script)  # every placeholder filled
        ticket = re.search(r"\$Ticket = '([A-Za-z0-9_-]{40,})'", script).group(1)
        skill = base64.b64decode(re.search(r"\$SkillBase64 = '([A-Za-z0-9+/=]+)'", script).group(1)).decode("utf-8")
        assert skill.startswith("---\nname: vxvue-ai") and "vxvue-ai-managed" in skill
        assert enroll(client, ticket, client="claude-code").status_code == 200


def test_status_reports_outdated_bridge(app_root):
    app, _, _ = app_root
    with client_for(app) as client:
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        token = enroll(client, ticket).json()["token"]
        params = {"name": "get_status", "arguments": {}}
        old = call(client, token, "tools/call", params, headers={"X-VXvue-Bridge": "1.0.0"}).json()
        current = call(client, token, "tools/call", params, headers={"X-VXvue-Bridge": desktop.BRIDGE_VERSION}).json()
    old_status = json.loads(old["result"]["content"][0]["text"])
    current_status = json.loads(current["result"]["content"][0]["text"])
    assert old_status["connection"]["update_required"] is True
    assert "/vxvue-desktop" in old_status["connection"]["guide"]
    assert current_status["connection"]["update_required"] is False


def test_team_token_bridge_without_version_header_is_told_to_reinstall(app_root):
    app, _, _ = app_root
    with client_for(app) as client:
        reply = call(client, "x" * 48, "tools/call", {"name": "get_status", "arguments": {}}).json()
    status = json.loads(reply["result"]["content"][0]["text"])
    assert status["connection"]["bridge_version"] == "1.0.0" and status["connection"]["update_required"] is True


def test_admin_script_lists_and_revokes_without_printing_keys(app_root, capsys):
    app, root, _ = app_root
    from scripts.vxvue_desktop_devices import main
    with client_for(app) as client:
        ticket = bundle(client.get("/vxvue-desktop/download").content)[1]["ticket"]
        issued = enroll(client, ticket).json()
        assert main(["--project-root", str(root), "list"]) == 0
        listed = capsys.readouterr().out
        assert issued["device_id"] in listed and "PC-1" in listed and issued["token"] not in listed
        assert main(["--project-root", str(root), "revoke", issued["device_id"]]) == 0
        assert main(["--project-root", str(root), "revoke", issued["device_id"]]) == 1
        assert call(client, issued["token"]).json()["code"] == "device_revoked"


def test_guide_asks_for_no_address_or_token_and_shows_cli_command(app_root):
    app, _, _ = app_root
    with client_for(app) as client:
        page = client.get("/vxvue-desktop").text
    assert "irm http://qa.test:24357/vxvue-desktop/claude-code/install.ps1 | iex" in page
    assert "팀 접근 토큰에는" not in page and "{{" not in page
    assert "/vxvue-desktop/download" in page and "network_not_allowed" in page


def test_tool_text_tells_claude_vp_numbers_are_polarion_not_jira():
    tools = {t["name"]: t["description"] for t in desktop.definitions()}
    assert "Jira" in tools["get_polarion_item"] and "VP-" in tools["get_polarion_item"]
    assert "Jira가 아니라" in desktop.INSTRUCTIONS
