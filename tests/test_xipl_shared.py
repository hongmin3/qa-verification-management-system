"""Validates REQ-XIPLSHARED-001..003 using synthetic sources and real RPC."""
import hashlib
import json
from types import SimpleNamespace

import fitz
import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.product_config import load_product_config
from app.core.product_knowledge import classify
from app.modules.vxvue_chatbot import desktop


def test_xipl_config_classifies_original_names_without_changing_other_products():
    cfg = load_product_config("xipl")
    assert cfg is not None
    for name, expected in (
        ("XIPL User Manual.V3.6.0_KO_리뷰완료_수정사항없음.docx", "manual"),
        ("XIPL.STUDIO User Manual.V1.1.1.0_KO.pdf", "manual"),
        ("Revision History_XIPL_3.6.0_RA20-13A-005_R60_수정.docx", "release_note"),
    ):
        assert classify(name, cfg.knowledge_source.classify) == expected
    assert not cfg.qa_rules.required and not cfg.testcase.required
    assert not cfg.qa_intelligence.enabled
    assert load_product_config("vxvue").knowledge_source.classify == {}


@pytest.fixture
def shared(tmp_path, monkeypatch):
    base = tmp_path / "data/product_knowledge/xipl"
    original = base / "original/manual"
    original.mkdir(parents=True)
    pdf = original / "XIPL.STUDIO User Manual.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "XIPL_STUDIO_UNIQUE")
    doc.save(pdf)
    doc.close()
    word = original / "XIPL User Manual.docx"
    doc = Document()
    doc.add_paragraph("VXCAD_SHARED_UNIQUE")
    doc.save(word)
    assets = [{"kind": "manual", "file_name": p.name,
               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (pdf, word)]
    release = base / "original/release_note/Revision History_XIPL.md"
    release.parent.mkdir()
    release.write_text("# Changes\nRELEASE_UNIQUE", encoding="utf8")
    assets.append({"kind": "release_note", "file_name": release.name,
                   "sha256": hashlib.sha256(release.read_bytes()).hexdigest()})
    manifest = base / "manifest.json"
    manifest.write_text(json.dumps({"product": "XIPL", "assets": assets}), encoding="utf8")
    token = tmp_path / "data/vxvue_desktop/access-token.txt"
    token.parent.mkdir(parents=True)
    token.write_text("x" * 48)
    monkeypatch.setattr(desktop, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    desktop.reset_cache()
    app = FastAPI()
    app.include_router(desktop.router, prefix="/vxvue-desktop")
    with TestClient(app, client=("10.0.0.5", 50000)) as client:
        client.headers["Authorization"] = "Bearer " + "x" * 48
        yield client, base, manifest, word
    desktop.reset_cache()


def rpc(client, name, **arguments):
    response = client.post("/vxvue-desktop/rpc", json={"jsonrpc": "2.0", "id": 1,
        "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    assert response.status_code == 200
    return response.json()["result"]


def value(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_shared_rpc_status_search_source_and_pdf_without_vxvue_rules(shared):
    client, _, _, word = shared
    listed=client.post('/vxvue-desktop/rpc',json={'jsonrpc':'2.0','id':2,'method':'tools/list'}).json()
    definitions={t['name']:t for t in listed['result']['tools']}
    assert 'make_word_report' in definitions and 'xipl_get_status' in definitions
    assert definitions['make_word_report']['annotations']['readOnlyHint'] is False
    assert definitions['xipl_get_document']['annotations']['readOnlyHint'] is True
    status = value(rpc(client, "xipl_get_status"))
    assert status["scope"]["files"] == 3
    sid = status["snapshot_id"]
    found = value(rpc(client, "xipl_search_documents", snapshot_id=sid, query="VXCAD_SHARED_UNIQUE"))
    assert len(found["results"]) == 1
    assert found["results"][0]["document_id"] == word.name
    source = value(rpc(client, "xipl_get_document", snapshot_id=sid, document_id=word.name))
    assert "VXCAD_SHARED_UNIQUE" in json.dumps(source)
    assert str(word.parent) not in json.dumps(status) + json.dumps(source)
    page = rpc(client, "xipl_get_document_page", snapshot_id=sid,
               document_id="XIPL.STUDIO User Manual.pdf", page=1)
    assert not page.get("isError")
    assert any(b["type"] == "image" for b in page["content"])


def test_shared_old_snapshot_survives_update_and_failed_new_source(shared):
    client, _, manifest, word = shared
    old = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    doc = Document()
    doc.add_paragraph("UPDATED_UNIQUE")
    doc.save(word)
    assert rpc(client, "xipl_get_status")["isError"]
    data = json.loads(manifest.read_text(encoding="utf8"))
    next(a for a in data["assets"] if a["file_name"] == word.name)["sha256"] = hashlib.sha256(word.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data), encoding="utf8")
    new = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    assert new != old
    assert value(rpc(client, "xipl_search_documents", snapshot_id=old, query="VXCAD_SHARED_UNIQUE"))["results"]
    assert not value(rpc(client, "xipl_search_documents", snapshot_id=new, query="VXCAD_SHARED_UNIQUE"))["results"]
    word.unlink()
    assert rpc(client, "xipl_get_status")["isError"]
    assert value(rpc(client, "xipl_search_documents", snapshot_id=old, query="VXCAD_SHARED_UNIQUE"))["results"]


def test_shared_rejects_missing_snapshot_unknown_document_and_auth(shared):
    client, _, _, _ = shared
    sid = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    assert rpc(client, "xipl_search_documents", query="anything")["isError"]
    assert rpc(client, "xipl_search_documents", snapshot_id="foreign", query="anything")["isError"]
    for doc in ("../manifest.json", "(사양서) Exposure.md", "C:/private.txt"):
        assert rpc(client, "xipl_get_document", snapshot_id=sid, document_id=doc)["isError"]
    assert rpc(client, "xipl_search_documents", snapshot_id=sid, query="a", product="VXvue")["isError"]
    client.headers["Authorization"] = "Bearer wrong"
    response = client.post("/vxvue-desktop/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401


def test_shared_rejects_tampered_snapshot_even_when_id_was_pinned(shared):
    client, _, _, _ = shared
    sid = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    root = desktop.get_settings().root
    original = root / "data/shared_knowledge/xipl/snapshots" / sid / "original"
    doc = Document()
    doc.add_paragraph("TAMPERED_CONTENT")
    doc.save(original / "XIPL User Manual.docx")
    assert rpc(client, "xipl_search_documents", snapshot_id=sid, query="TAMPERED_CONTENT")["isError"]


def test_shared_corrupt_word_is_not_published_and_docx_has_no_fake_page(shared):
    client, _, manifest, word = shared
    sid = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    assert rpc(client, "xipl_get_document_page", snapshot_id=sid, document_id=word.name, page=1)["isError"]
    word.write_bytes(b"not a docx file")
    data = json.loads(manifest.read_text(encoding="utf8"))
    next(a for a in data["assets"] if a["file_name"] == word.name)["sha256"] = hashlib.sha256(word.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data), encoding="utf8")
    assert rpc(client, "xipl_get_status")["isError"]
    assert value(rpc(client, "xipl_search_documents", snapshot_id=sid, query="VXCAD_SHARED_UNIQUE"))["results"]


def test_shared_rejects_unregistered_file_in_pinned_snapshot(shared):
    client, _, _, _ = shared
    sid = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    original = desktop.get_settings().root / "data/shared_knowledge/xipl/snapshots" / sid / "original"
    (original / "foreign.txt").write_text("FOREIGN_SECRET", encoding="utf8")
    assert rpc(client, "xipl_search_documents", snapshot_id=sid, query="FOREIGN_SECRET")["isError"]


def test_shared_missing_required_kind_does_not_publish_new_snapshot(shared):
    client, _, manifest, _ = shared
    sid = value(rpc(client, "xipl_get_status"))["snapshot_id"]
    data = json.loads(manifest.read_text(encoding="utf8"))
    data["assets"] = [a for a in data["assets"] if a["kind"] == "manual"]
    manifest.write_text(json.dumps(data), encoding="utf8")
    assert rpc(client, "xipl_get_status")["isError"]
    assert value(rpc(client, "xipl_search_documents", snapshot_id=sid, query="RELEASE_UNIQUE"))["results"]


def test_shared_release_note_filter_preserves_registered_kind(shared):
    client, _, _, _ = shared
    status = value(rpc(client, "xipl_get_status"))
    release = next(d for d in status["scope"]["documents"] if d["document_id"].endswith(".md"))
    assert release["document_kind"] == "release_note"
    result = value(rpc(client, "xipl_search_documents", snapshot_id=status["snapshot_id"],
                       query="RELEASE_UNIQUE", document_kind="release_note"))
    assert len(result["results"]) == 1
    assert result["results"][0]["document_kind"] == "release_note"
