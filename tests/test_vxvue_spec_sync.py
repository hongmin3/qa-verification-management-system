import httpx

from app.modules.knowledge.vxvue_spec_sync import _base_name, _replace_stale_revisions
import logging


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
