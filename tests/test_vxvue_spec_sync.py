import httpx

from app.modules.knowledge.vxvue_spec_sync import _base_name, _replace_stale_revisions
import logging

import json
from datetime import date
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
    today = date(2026, 10, 3)   # 수집본(10-02) 다음 날. 오래된 수집본 판정과 섞이지 않게 한다.
    assert sync.run("http://server-a", today=today)["status"] == "SUCCESS"
    assert sync.run("http://server-b", today=today)["status"] == "SUCCESS"
    assert sync.run("http://server-b/", today=today)["status"] == "SUCCESS"
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


def _stale_env(tmp_path, monkeypatch, folder_date: str):
    pdf_dir = tmp_path / "crawler" / folder_date / "pdf"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "spec.pdf").write_bytes(b"fixture PDF")
    monkeypatch.setattr(sync, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(sync, "_paths", lambda: (tmp_path / "lock", tmp_path / "state.json", tmp_path / "sync.log"))
    monkeypatch.setattr(sync, "load_product_config", lambda _: SimpleNamespace(
        product="VXvue", version="1", specification=SimpleNamespace(
            source="alm_crawler", crawler_output_dir=str(tmp_path / "crawler"), filename_patterns=["*.pdf"])))
    monkeypatch.setattr(sync, "extract_document_text", lambda _: "Specification")
    client_type = httpx.Client
    handler = lambda request: httpx.Response(303) if request.method == "POST" else httpx.Response(200, json=[])
    monkeypatch.setattr(sync.httpx, "Client", lambda: client_type(transport=httpx.MockTransport(handler)))


def test_stale_collection_is_partial_even_when_upload_succeeds(tmp_path, monkeypatch):
    """Validates: REQ-SYNC-001. 2026-10-23(금)에 10-19(월) 수집본이면 10-20·21·22 가 빠졌다."""
    _stale_env(tmp_path, monkeypatch, "2026-10-19")

    first = sync.run("http://server", today=date(2026, 10, 23))
    again = sync.run("http://server", today=date(2026, 10, 23))   # 변경 없음 결과에도 적용

    for result in (first, again):
        assert result["status"] == "PARTIAL"
        assert result["detail"].startswith("최신 수집본이 오래됐습니다(마지막 완료 2026-10-19, 빠진 평일 3일). SRS 수집(QA_SRS_Collect)을 확인하세요. / ")
    assert "업로드 1건" in first["detail"] and "변경 없음" in again["detail"]


@pytest.mark.parametrize(("folder_date", "today"), [
    ("2026-10-23", date(2026, 10, 26)),   # 금요일 수집본을 월요일 03:30 에 올림: 사이는 주말뿐
    ("2026-10-22", date(2026, 10, 23)),   # 어제 수집본
    ("2026-10-23", date(2026, 10, 23)),   # 오늘 수집본(수동 실행)
])
def test_fresh_collection_stays_success(tmp_path, monkeypatch, folder_date, today):
    """Validates: REQ-SYNC-001."""
    _stale_env(tmp_path, monkeypatch, folder_date)

    assert sync.run("http://server", today=today)["status"] == "SUCCESS"


def test_missing_weekdays_counts_only_weekdays_between():
    """Validates: REQ-SYNC-001."""
    assert sync.missing_weekdays(date(2026, 10, 19), date(2026, 10, 23)) == 3
    assert sync.missing_weekdays(date(2026, 10, 23), date(2026, 10, 26)) == 0
    assert sync.missing_weekdays(date(2026, 10, 23), date(2026, 10, 27)) == 1
    assert sync.missing_weekdays(date(2026, 10, 23), date(2026, 10, 23)) == 0
