import httpx

from app.modules.knowledge.vxvue_spec_sync import _base_name, _replace_stale_revisions
import logging

import json
from types import SimpleNamespace
import pytest

import app.modules.knowledge.vxvue_spec_sync as sync


@pytest.mark.parametrize("legacy_state", [False, True])
def test_sync_success_for_one_server_does_not_skip_another(tmp_path, monkeypatch, legacy_state):
    """Validates: REQ-SYNC-001. 올린 서버마다 상태를 따로 확인한다."""
    pdf_dir = tmp_path / "crawler" / "2026-10-02" / "pdf"
    pdf_dir.mkdir(parents=True)
    pdf = pdf_dir / "spec(261002).pdf"
    pdf.write_bytes(b"fixture PDF")
    state_file = tmp_path / "state.json"
    if legacy_state:
        state_file.write_text(json.dumps({pdf.name: sync._file_signature(pdf)}), encoding="utf-8")
    monkeypatch.setattr(sync, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(sync, "_paths", lambda: (tmp_path / "lock", state_file, tmp_path / "sync.log"))
    monkeypatch.setattr(sync, "load_product_config", lambda _: SimpleNamespace(
        product="VXvue", version="1", specification=SimpleNamespace(
            source="alm_crawler", crawler_output_dir=str(tmp_path / "crawler"), filename_patterns=["*.pdf"])))
    monkeypatch.setattr(sync, "extract_document_text", lambda _: "Specification")
    uploads = []

    def handler(request):
        if request.method == "POST":
            uploads.append(str(request.url))
            return httpx.Response(303)
        return httpx.Response(200, json=[])

    client_type = httpx.Client
    monkeypatch.setattr(sync.httpx, "Client", lambda: client_type(transport=httpx.MockTransport(handler)))
    assert sync.run("http://server-a")["status"] == "SUCCESS"
    assert sync.run("http://server-b")["status"] == "SUCCESS"
    assert sync.run("http://server-b/")["status"] == "SUCCESS"
    assert uploads == ["http://server-a/knowledge/specification", "http://server-b/knowledge/specification"]
    if legacy_state:
        assert json.loads(state_file.read_text(encoding="utf-8"))[pdf.name] == sync._file_signature(pdf)


def test_base_name_strips_trailing_date():
    assert _base_name("(사양서) VXvue 사양서1(260831).pdf") == "(사양서) VXvue 사양서1.pdf"
    assert _base_name("(사양서) Licence Manager SRS 사양서(260824).pdf") == "(사양서) Licence Manager SRS 사양서.pdf"


def test_base_name_leaves_names_without_date_suffix_untouched():
    assert _base_name("System Integration Guide for VXvue.V1.0.11_KO.pdf") == "System Integration Guide for VXvue.V1.0.11_KO.pdf"


def test_replace_stale_revisions_deletes_only_matching_base_name():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[
                {"id": 1, "name": "(사양서) VXvue 사양서1(260824).pdf"},
                {"id": 2, "name": "(사양서) VXvue 사양서2(260824).pdf"},
                {"id": 3, "name": "(사양서) VXvue 사양서1(260831).pdf"},
            ])
        return httpx.Response(303)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    removed = _replace_stale_revisions(client, "http://test", "VXvue", "(사양서) VXvue 사양서1(260831).pdf", logging.getLogger("test"))

    assert removed == 1
    delete_calls = [url for method, url in calls if method == "POST"]
    assert delete_calls == ["http://test/knowledge/delete/1"]
