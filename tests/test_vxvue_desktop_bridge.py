"""Validates: REQ-VXVUECHATBOT-011, REQ-VXVUECHATBOT-013 with the shipped Node bridge. Synthetic tickets only."""
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.vxvue_chatbot import desktop
from tests.test_vxvue_desktop import server  # noqa: F401  (fixture reuse)
from tests.test_vxvue_desktop_enroll import Settings

BRIDGE = Path(__file__).resolve().parents[1] / "deploy/claude-desktop/server.cjs"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="Node.js is needed to exercise the shipped bridge")
HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
]


class Proxy:
    """A real HTTP port in front of the TestClient, so the bridge talks over a socket."""

    def __init__(self, client):
        self.client = client
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                keep = {k: v for k, v in self.headers.items() if k.lower() in ("authorization", "content-type", "x-vxvue-bridge")}
                response = proxy.client.post(self.path, content=body, headers=keep)
                self.send_response(response.status_code)
                self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
                self.end_headers()
                self.wfile.write(response.content)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def setup(server, monkeypatch, tmp_path):
    _, _, root = server
    settings = Settings(root=root, values={})
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(desktop.router, prefix="/vxvue-desktop")
    with TestClient(app, client=("10.20.30.40", 50000)) as client:
        proxy = Proxy(client)
        try:
            yield client, proxy, tmp_path, settings
        finally:
            proxy.close()


def install(client, proxy, folder):
    """Unpack a downloaded .mcpb the way Claude Desktop does: files side by side, env from the manifest."""
    settings = desktop.get_settings()
    settings.values["vxvue_desktop.public_url"] = proxy.url
    data = client.get("/vxvue-desktop/download").content
    folder.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(folder)
        manifest = json.loads(archive.read("manifest.json"))
    return folder / "server.cjs", manifest["server"]["mcp_config"]["env"]


def run(script, env, home, messages=HANDSHAKE):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VXVUE_")}
    process = subprocess.run(["node", str(script)], input="\n".join(json.dumps(m) for m in messages) + "\n",
                             text=True, encoding="utf-8", capture_output=True, timeout=60,
                             env={**clean, **env, "VXVUE_AI_HOME": str(home)})
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()], process.stderr


def tool_text(reply):
    return reply["result"]["content"][0]["text"]


def test_first_run_enrolls_then_restart_reuses_key_without_input(setup):
    client, proxy, tmp, _ = setup
    script, env = install(client, proxy, tmp / "ext")
    home = tmp / "home"
    replies, log = run(script, env, home)
    assert [r["id"] for r in replies] == [1, 2]
    status = json.loads(tool_text(replies[1]))
    assert status["snapshot_id"] and status["connection"]["update_required"] is False
    saved = json.loads((home / "credentials.json").read_text(encoding="utf-8"))
    token = saved[proxy.url]["token"]
    assert "이 PC를 등록했습니다" in log
    enrollment = json.loads((script.parent / "enrollment.json").read_text(encoding="utf-8"))
    assert token not in log + json.dumps(replies) and enrollment["ticket"] not in log + json.dumps(replies)
    # Restart: the ticket is spent, so success proves the saved key was reused.
    again, log2 = run(script, env, home)
    assert json.loads(tool_text(again[1]))["snapshot_id"] and "등록했습니다" not in log2
    # A second extension package on the same PC (reinstall) reuses the same key too.
    script2, env2 = install(client, proxy, tmp / "ext2")
    third, log3 = run(script2, env2, home)
    assert json.loads(tool_text(third[1]))["snapshot_id"] and "등록했습니다" not in log3


def test_revoked_key_re_enrolls_once_then_explains(setup):
    client, proxy, tmp, _ = setup
    from app.core.storage import Storage
    script, env = install(client, proxy, tmp / "ext")
    home = tmp / "home"
    run(script, env, home)
    storage = Storage(desktop.get_settings().root / "data/app.db")
    device = storage.vxdesk_devices()[0]["id"]
    storage.vxdesk_revoke(device)
    replies, _ = run(script, env, home)
    assert replies[1]["result"]["isError"] is True
    text = tool_text(replies[1])
    assert "VXvue AI 연결 실패 [device_revoked]" in text and "관리자" in text and "/vxvue-desktop" in text
    assert not (home / "credentials.json").exists() or proxy.url not in json.loads((home / "credentials.json").read_text())


def test_stopped_server_starts_and_explains_cause(setup, tmp_path):
    client, proxy, tmp, _ = setup
    script, env = install(client, proxy, tmp / "ext")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    env = {**env, "VXVUE_SERVER_URL": f"http://127.0.0.1:{closed}"}
    (script.parent / "enrollment.json").write_text(json.dumps({"server_url": env["VXVUE_SERVER_URL"], "ticket": "t" * 43}), encoding="utf-8")
    replies, log = run(script, env, tmp / "home", HANDSHAKE + [{"jsonrpc": "2.0", "id": 3, "method": "tools/list"}])
    assert "get_status" in replies[0]["result"]["instructions"]  # handshake answered from the bundle
    assert replies[1]["result"]["isError"] is True
    assert "[ECONNREFUSED]" in tool_text(replies[1]) and "서버 프로그램" in tool_text(replies[1])
    assert {"get_status", "get_workflow", "polarion_query"} <= {t["name"] for t in replies[2]["result"]["tools"]}
    log = (tmp / "home" / "bridge.log").read_text(encoding="utf-8")
    assert "enroll network refused ECONNREFUSED" in log and "recv tools/call" in log and "t" * 43 not in log
    assert "t" * 43 not in log + json.dumps(replies)


def test_outside_network_is_explained(setup):
    client, proxy, tmp, _ = setup
    script, env = install(client, proxy, tmp / "ext")
    home = tmp / "home"
    run(script, env, home)
    desktop.get_settings().values["vxvue_desktop.allowed_networks"] = ["192.168.0.0/16"]
    replies, _ = run(script, env, home)
    text = tool_text(replies[1])
    assert "[network_not_allowed]" in text and "10.20.30.40" in text


def test_legacy_team_token_bridge_still_connects(setup):
    _, proxy, tmp, _ = setup
    token = "x" * 48
    replies, log = run(BRIDGE, {"VXVUE_SERVER_URL": proxy.url, "VXVUE_ACCESS_TOKEN": token}, tmp / "home")
    assert json.loads(tool_text(replies[1]))["snapshot_id"]
    assert token not in log + json.dumps(replies)
    wrong = "wrong-token-" * 4
    replies, log = run(BRIDGE, {"VXVUE_SERVER_URL": proxy.url, "VXVUE_ACCESS_TOKEN": wrong}, tmp / "home")
    assert "[unknown_credential]" in tool_text(replies[1])
    assert wrong not in log + json.dumps(replies)


def test_silent_server_does_not_stall_the_handshake(setup):
    """Claude Desktop gives up on a server that does not answer initialize; a black-holed QA server must not cause that."""
    import time
    client, proxy, tmp, _ = setup
    script, env = install(client, proxy, tmp / "ext")
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)  # accepts connections, never answers
    try:
        env = {**env, "VXVUE_SERVER_URL": f"http://127.0.0.1:{silent.getsockname()[1]}", "VXVUE_QUICK_MS": "1500"}
        started = time.monotonic()
        replies, _ = run(script, env, tmp / "home", [HANDSHAKE[0], HANDSHAKE[1], {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}])
        elapsed = time.monotonic() - started
    finally:
        silent.close()
    assert [r["id"] for r in replies] == [1, 3]
    assert replies[0]["result"]["serverInfo"]["version"] == "1.2.0"
    assert len(replies[1]["result"]["tools"]) >= 10
    assert elapsed < 15
