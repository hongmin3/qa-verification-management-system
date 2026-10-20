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
    with TestClient(app, client=("10.0.0.5", 50000)) as client:
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
    # Without the old team token file the bearer is just an unknown credential (REQ-VXVUECHATBOT-013).
    assert client.post("/vxvue-desktop/rpc", json=request).status_code == 401


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


def test_bundle_carries_no_user_config_and_token_initialization_preserves_existing(tmp_path):
    import io
    import zipfile
    from scripts.build_vxvue_desktop import bundle_bytes
    from scripts.configure_vxvue_desktop import initialize
    ticket = "t" * 43
    data = bundle_bytes("http://127.0.0.1:24357", ticket, "2026-10-22T00:00:00+00:00")
    assert data == bundle_bytes("http://127.0.0.1:24357", ticket, "2026-10-22T00:00:00+00:00")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "server.cjs", "enrollment.json", "tools.json"}
        manifest = json.loads(archive.read("manifest.json"))
        assert "user_config" not in manifest
        assert manifest["server"]["mcp_config"]["env"] == {"VXVUE_SERVER_URL": "http://127.0.0.1:24357"}
        assert json.loads(archive.read("enrollment.json"))["ticket"] == ticket
    with pytest.raises(ValueError):
        bundle_bytes("http://user:pw@127.0.0.1:24357", ticket)
    with pytest.raises(ValueError):
        bundle_bytes("http://127.0.0.1:24357", "short")
    path = initialize(tmp_path)
    token = path.read_bytes()
    assert len(token.strip()) >= 32
    assert initialize(tmp_path).read_bytes() == token

def test_unified_workflow_pagination_without_prompt_preserves_pinned_snapshot(server):
    from tests.test_unified_qa_rules import UNIFIED
    client, base, root = server
    old_sid=value(rpc(client,"get_status"))["snapshot_id"]
    manifest=json.loads((base/"manifest.json").read_text(encoding="utf-8"))
    manifest["assets"]=[a for a in manifest["assets"] if a["kind"] not in ("qa_rules","instruction_prompt")]
    path=base/"original/qa_rules/current.md"
    path.write_text(UNIFIED,encoding="utf-8")
    manifest["assets"].append({"kind":"qa_rules","file_name":"current.md","sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
    (base/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
    sid=value(rpc(client,"get_status"))["snapshot_id"]
    assert sid!=old_sid
    offset=0
    chunks=[]
    while True:
        part=value(rpc(client,"get_workflow",{"snapshot_id":sid,"offset":offset,"limit":300}))
        chunks.append(part["text"])
        if not part["truncated"]: break
        offset=part["next_offset"]
    text="".join(chunks)
    assert text.count("요청 범위와 확인 근거")==1
    assert "CURRENT PROMPT" not in text
    assert "CURRENT PROMPT" in value(rpc(client,"get_workflow",{"snapshot_id":old_sid}))["text"]


def test_client_disconnect_while_reading_is_not_a_server_error(monkeypatch, caplog):
    """Desktop 이 요청 본문을 다 보내기 전에 끊어도 서버 오류 추적을 남기지 않는다 (REQ-VXVUECHATBOT-011)."""
    import asyncio
    import logging

    from starlette.requests import ClientDisconnect

    from app.modules.vxvue_chatbot import desktop

    class Gone:
        headers = {"content-length": "10"}

        async def stream(self):
            raise ClientDisconnect()
            yield b""  # pragma: no cover - 비동기 생성기로 만들기 위한 줄

    monkeypatch.setattr(desktop, "_authorize", lambda request: {"kind": "device"})
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        response = asyncio.run(desktop._handle(Gone(), 204))
    assert response.status_code == 499
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("disconnected" in r.getMessage() for r in caplog.records)


def test_desktop_results_show_paths_inside_the_knowledge_folder_only():
    """Generator 근거의 원본 경로는 지식 폴더 안의 상대 경로로만 보인다 (REQ-VXVUECHATBOT-011).
    모양은 2026-10-19 운영 get_generator_evidence 응답(sources[].file, vss[].source.file, tc[].file)을 따랐다."""
    from app.modules.vxvue_chatbot import desktop

    root = "/home/ubuntu/ai-regression-impact-analyzer/data/vxvue_desktop/snapshots/68bc269460a75612c7c30d6f"
    tools = desktop.DesktopTools.__new__(desktop.DesktopTools)
    tools.snapshot = {"root": root}
    tc_file = f"{root}/original/(TC) RA16-149-003_VXvue_Generator_Test_Case (261006).xlsx"
    value = {"sources": [{"file": tc_file, "sha256": "e0"}],
             "vss": [{"source": {"file": f"{root}/original/(사양서) VSS Specification (261006).xlsx", "cell": "U36"}}],
             "tc": [{"file": tc_file, "row": 7}],
             "scope": {"root": root, "files": [{"path": r"C:\Users\kim\VXvue 지식파일\a.pdf", "bytes": 1}]}}
    cleaned = desktop.client_paths(value, root)
    text = repr(cleaned)
    assert "/home/" not in text and "Users" not in text and "snapshots" not in text
    assert cleaned["sources"][0]["file"] == "(TC) RA16-149-003_VXvue_Generator_Test_Case (261006).xlsx"
    assert cleaned["vss"][0]["source"]["file"] == "(사양서) VSS Specification (261006).xlsx"
    assert cleaned["scope"]["files"][0]["path"] == "a.pdf" and cleaned["scope"]["root"] == ""
    assert cleaned["sources"][0]["sha256"] == "e0" and cleaned["tc"][0]["row"] == 7


def test_status_connection_guide_keeps_the_server_address(monkeypatch):
    """사용자가 이미 접속한 서버 주소는 연결 안내에서 가리지 않는다. 다른 값의 주소는 그대로 가린다."""
    import json

    from app.modules.vxvue_chatbot import desktop

    monkeypatch.setattr(desktop, "call", lambda name, args: {"note": "장비 10.1.2.3"})
    reply = desktop.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
                             {"bridge_version": "1.2.1", "base_url": "http://10.13.0.222:24357"})
    data = json.loads(reply["content"][0]["text"])
    assert data["connection"]["guide"] == "http://10.13.0.222:24357/vxvue-desktop"
    assert data["note"] == "장비 [IP_ADDRESS]"


def test_tool_error_hides_server_path_containing_qa_identifier(monkeypatch):
    """Validates: REQ-VXVUECHATBOT-011, NFR-PRIV-001."""
    from app.modules.vxvue_chatbot import desktop

    def unavailable(name, args):
        raise ValueError("자료 읽기 실패: /home/ubuntu/qa/VP-1234.xlsx")

    monkeypatch.setattr(desktop, "call", unavailable)
    reply = desktop.dispatch({"method": "tools/call", "params": {"name": "get_document", "arguments": {}}})
    assert reply["isError"] is True
    text = " ".join(item["text"] for item in reply["content"])
    assert "/home/" not in text and "[SERVER_PATH]" in text
    assert "VP-1234" in text


@pytest.mark.parametrize("base, expected_prefix", [
    ("http://10.1.2.3:24357", "http://10.1.2.3:24357"),
    ("https://user:secret@example.test", ""),
    ("https://:secret@example.test", ""),
    ("https://example.test?secret=value", ""),
])
def test_review_download_link_keeps_only_validated_server_address(monkeypatch, base, expected_prefix):
    """Validates: REQ-MANUAL-021, REQ-VXVUECHATBOT-011, NFR-PRIV-001."""
    route = "/vxvue-desktop/manual-review/files/report-123/review.html"
    monkeypatch.setattr(desktop, "call", lambda name, args: {
        "files": [{"name": "review.html", "url": route}],
        "note": "10.2.3.4 /home/ubuntu/reviews/VP-1234/report.txt",
    })
    reply = desktop.dispatch({"method": "tools/call", "params": {
        "name": "make_manual_review_report", "arguments": {},
    }}, {"base_url": base})
    assert reply["isError"] is False
    data = json.loads(reply["content"][0]["text"])
    assert data["files"][0]["url"] == expected_prefix + route
    assert "[IP_ADDRESS]" in data["note"] and "[SERVER_PATH]" in data["note"]
    assert "VP-1234" in data["note"]
    assert "/home/" not in data["note"] and "report.txt" not in data["note"]
    assert "secret" not in reply["content"][0]["text"]
