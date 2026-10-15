"""Validates: REQ-VXVUECHATBOT-011. Only synthetic knowledge is used."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.vxvue_chatbot import desktop


@pytest.fixture
def server(tmp_path, monkeypatch):
    base = tmp_path / "data/product_knowledge/vxvue"
    assets = []
    for kind, name, body in (
        ("instruction_prompt", "VXvue_検証_Rev1.18.txt", "CURRENT PROMPT"),
        ("qa_rules", "[QA 작성 규칙] guide_Rev1.18.md", "# Guide\nPreserve TC"),
        ("specification", "(사양서) Exposure.md", "# VP-1234 Exposure\nReady precedes Trigger."),
        ("skill_instruction", "[Skill 지침] VXvue.md", "placeholder"),
    ):
        p = base / "original" / kind / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if kind == "skill_instruction":
            body = "TEAM SKILL\nApply original QA workflow."
            metadata = {"schema": 1, "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                        "code_hash": "a" * 64, "plugin": "qa@team", "skill": "vxvue-ai", "version": "1.0.0"}
            body = "<!-- qa-skill-export:v1 " + json.dumps(metadata) + " -->\n" + body
        p.write_text(body, encoding="utf-8")
        assets.append({"kind": kind, "file_name": name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    (base / "manifest.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
    token = tmp_path / "data/vxvue_desktop/access-token.txt"
    token.parent.mkdir(parents=True)
    token.write_text("x" * 48)
    monkeypatch.setattr(desktop, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    desktop.reset_cache()
    app = FastAPI()
    app.include_router(desktop.router, prefix="/vxvue-desktop")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "x" * 48
        yield client, base, tmp_path
    desktop.reset_cache()


def rpc(client, name, args=None, method="tools/call"):
    params = {"name": name, "arguments": args or {}} if method == "tools/call" else (args or {})
    r = client.post("/vxvue-desktop/rpc", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def value(reply):
    assert "error" not in reply, reply
    result = reply["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_auth_disabled_bad_token_and_cross_origin(server):
    client, _, root = server
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/vxvue-desktop/rpc", json=request, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/vxvue-desktop/rpc", json=request, headers={"Origin": "https://other.test"}).status_code == 403
    (root / "data/vxvue_desktop/access-token.txt").unlink()
    assert client.post("/vxvue-desktop/rpc", json=request).status_code == 503


def test_handshake_tools_workflow_search_and_pinned_old_revision(server):
    client, base, _ = server
    reply = rpc(client, "", {"protocolVersion": "2025-06-18"}, "initialize")
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in rpc(client, "", method="tools/list")["result"]["tools"]}
    assert {"get_status", "get_workflow", "get_document_page", "polarion_query"} <= names
    assert not {"read_attachment", "shell", "batch_lookup"} & names
    status = value(rpc(client, "get_status"))
    sid = status["snapshot_id"]
    workflow = value(rpc(client, "get_workflow", {"snapshot_id": sid, "limit": 8}))
    assert workflow["truncated"] and workflow["next_offset"] == 8
    all_text = workflow["text"]
    while workflow["truncated"]:
        workflow = value(rpc(client, "get_workflow", {"snapshot_id": sid, "offset": workflow["next_offset"], "limit": 8}))
        all_text += workflow["text"]
    assert "CURRENT PROMPT" in all_text and "Preserve TC" in all_text
    found = value(rpc(client, "search_documents", {"snapshot_id": sid, "query": "VP-1234"}))
    assert found["results"] and "reference_url" not in found["results"][0]
    p = base / "original/specification/(사양서) Exposure.md"
    p.write_text("# VP-5678 Changed", encoding="utf-8")
    manifest = json.loads((base / "manifest.json").read_text())
    next(a for a in manifest["assets"] if a["kind"] == "specification")["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert value(rpc(client, "get_status"))["snapshot_id"] != sid
    assert value(rpc(client, "search_documents", {"snapshot_id": sid, "query": "VP-1234"}))["results"]


def test_missing_snapshot_path_escape_and_hash_mismatch_are_errors(server):
    client, base, _ = server
    assert rpc(client, "get_document", {"document_id": "../../secrets.txt"})["result"]["isError"]
    (base / "original/specification/(사양서) Exposure.md").write_text("uncommitted")
    assert rpc(client, "get_status")["result"]["isError"]
    manifest = json.loads((base / "manifest.json").read_text())
    manifest["assets"][-1]["file_name"] = "../../secrets.txt"
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert rpc(client, "get_status")["result"]["isError"]


def test_missing_skill_blocks_workflow(server):
    client, base, _ = server
    manifest = json.loads((base / "manifest.json").read_text())
    manifest["assets"] = [a for a in manifest["assets"] if a["kind"] != "skill_instruction"]
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert rpc(client, "get_status")["result"]["isError"]


def test_guide_and_bundle_are_unauthenticated_but_contain_no_token(server):
    client, _, _ = server
    assert client.get("/vxvue-desktop").status_code == 200
    response = client.get("/vxvue-desktop/download")
    assert response.status_code == 200
    assert "x" * 48 not in response.content.decode("latin1")
    import zipfile
    import io
    skill = client.get("/vxvue-desktop/skill.zip")
    assert skill.status_code == 200
    with zipfile.ZipFile(io.BytesIO(skill.content)) as archive:
        assert archive.namelist() == ["vxvue-ai/SKILL.md"]
        assert b"2024980" not in archive.read("vxvue-ai/SKILL.md")


def test_pdf_page_and_full_source_locations(server):
    import fitz
    client, base, _ = server
    path = base / "original/specification/(사양서) image.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "VP-9001 Trigger follows Ready.")
    pdf.save(path)
    pdf.close()
    manifest = json.loads((base / "manifest.json").read_text())
    manifest["assets"].append({"kind": "specification", "file_name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sid = value(rpc(client, "get_status"))["snapshot_id"]
    result = rpc(client, "get_document_page", {"snapshot_id": sid, "document_id": path.name, "page": 1})["result"]
    assert not result["isError"]
    assert any(c["type"] == "image" and c["mimeType"] == "image/png" for c in result["content"])
    assert rpc(client, "get_document_page", {"snapshot_id": sid, "document_id": "../../secret.pdf", "page": 1})["result"]["isError"]


def test_real_node_bridge_roundtrip_and_failure(server):
    import http.server
    import os
    import subprocess
    import threading
    import shutil
    client, _, _ = server
    if not shutil.which("node"):
        pytest.skip("Node.js is needed to exercise the shipped desktop bridge")
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            response = client.post(self.path, content=self.rfile.read(length),
                                   headers={"Authorization": self.headers.get("Authorization", ""), "Content-Type": "application/json"})
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)
        def log_message(self, *args):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).resolve().parents[1] / "deploy/claude-desktop/server.cjs"
    env = {**os.environ, "VXVUE_SERVER_URL": f"http://127.0.0.1:{httpd.server_port}", "VXVUE_ACCESS_TOKEN": "x" * 48}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_status"}},
    ]
    try:
        process = subprocess.run(["node", str(script)], input="\n".join(json.dumps(m) for m in messages) + "\n",
                                 text=True, encoding="utf-8", capture_output=True, env=env, timeout=30)
        assert process.returncode == 0 and not process.stderr
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        assert [r["id"] for r in replies] == [1, 2]
        assert value(replies[1])["snapshot_id"]
        env["VXVUE_ACCESS_TOKEN"] = "wrong-token-" * 4
        failure = subprocess.run(["node", str(script)], input=json.dumps(messages[0]) + "\n", text=True,
                                 encoding="utf-8", capture_output=True, env=env, timeout=30)
        assert "HTTP 401" in json.loads(failure.stdout)["error"]["message"]
        assert env["VXVUE_ACCESS_TOKEN"] not in failure.stdout + failure.stderr
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_bundle_is_deterministic_and_token_initialization_preserves_existing(tmp_path):
    import io
    import zipfile
    from scripts.build_vxvue_desktop import bundle_bytes
    from scripts.configure_vxvue_desktop import initialize
    data = bundle_bytes("http://127.0.0.1:24357")
    assert data == bundle_bytes("http://127.0.0.1:24357")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "server.cjs"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["user_config"]["access_token"]["sensitive"]
        assert manifest["user_config"]["server_url"]["default"] == "http://127.0.0.1:24357"
    path = initialize(tmp_path)
    token = path.read_bytes()
    assert len(token.strip()) >= 32
    assert initialize(tmp_path).read_bytes() == token
