"""Validates: REQ-VXVUECHATBOT-012."""
import json
from types import SimpleNamespace
import pytest
from app.modules.vxvue_chatbot.desktop_polarion import PolarionLookup
from app.modules.daily_qa.polarion import PolarionError


def test_filter_preserved_all_attributes_and_relationships_paginated(tmp_path):
    calls = []
    query = r"! NOT status:verified AND author.id:(1234567 7654321) AND NOT title:\[보류\] AND NOT defectScore.KEY:grade_d AND NOT occurredVersion.KEY:VXvue_1_1_0_0022"
    def get(path, params):
        calls.append((path, params))
        return {"data": [{"id": "VXvue/VP-42", "attributes": {"description": {"type": "text/html", "value": "<p>근거</p>"}, "customField": "keep"}, "relationships": {"linkedWorkItems": {"data": [{"id": "VXvue/VP-43"}]}}}], "links": {"next": "page2"}, "meta": {"totalCount": 51}}
    lookup = PolarionLookup(tmp_path, SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), _get=get))
    result = lookup.query(query, page=1, page_size=10)
    assert calls[0][1]["query"] == query
    assert result["next_page"] == 2 and result["total_count"] == 51
    body = result["text"]
    while result["truncated"]:
        result = lookup.read(result["result_id"], result["next_offset"], 7)
        body += result["text"]
    raw = json.loads(body)
    assert raw["response"]["data"][0]["attributes"]["customField"] == "keep"
    assert raw["response"]["data"][0]["relationships"]["linkedWorkItems"]["data"][0]["id"] == "VXvue/VP-43"
    assert raw["readable"][0]["description"] == "근거"


def test_exact_vp_section_and_errors_never_become_empty_results(tmp_path):
    calls = []
    def get(path, params):
        if not path.endswith(("/comments", "/attachments", "/linkedworkitems")):
            assert "page[size]" not in params and "page[number]" not in params
        calls.append(path)
        item = {"id": "VXvue/VP-1234", "attributes": {"title": "Spec"}}
        return {"data": item if not path.endswith(("/comments", "/attachments", "/linkedworkitems")) else [item]}
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), _get=get)
    lookup = PolarionLookup(tmp_path, client)
    lookup.item("vp-1234")
    assert calls == ["/projects/VXvue/workitems/VP-1234"]
    lookup.item("VP-1234", section="comments", page=2)
    assert calls[-1].endswith("/VP-1234/comments")
    lookup.item("VP-1234", section="links")
    assert calls[-1].endswith("/VP-1234/linkedworkitems")
    with pytest.raises(ValueError):
        lookup.item("../VP-1")
    with pytest.raises(ValueError):
        lookup.read("../../secret", 0, 100)
    with pytest.raises(ValueError):
        lookup.query("x", page_size=51)
    client._get = lambda *a: (_ for _ in ()).throw(PolarionError("HTTP 401"))
    with pytest.raises(PolarionError):
        lookup.query("id:VP-1234")


def test_sensitive_values_protected_before_chunking_json_remains_complete(tmp_path):
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), _get=lambda *args: {
        "data": [{"id": "VXvue/VP-1", "attributes": {"description": {"value": "patient id: SECRET123 server 10.13.0.5"}}}],
        "links": {"self": "http://10.13.0.5/polarion/VP-1"}})
    lookup = PolarionLookup(tmp_path, client)
    first = lookup.query("id:VP-1")
    body = ""
    offset = 0
    while True:
        piece = lookup.read(first["result_id"], offset, 3)
        body += piece["text"]
        if not piece["truncated"]:
            break
        offset = piece["next_offset"]
    parsed = json.loads(body)
    assert parsed["protection"]["applied"]
    assert "SECRET123" not in body and "10.13.0.5" not in body
    assert parsed["response"]["data"][0]["id"] == "VXvue/VP-1"


def test_total_count_keeps_paging_and_malformed_success_is_not_zero(tmp_path):
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), _get=lambda *a: {
        "data": [{"id": "VXvue/VP-1"}], "meta": {"totalCount": 3}})
    lookup = PolarionLookup(tmp_path, client)
    assert lookup.query("type:srs", page_size=1)["next_page"] == 2
    client._get = lambda *a: {"data": [], "meta": {"totalCount": 3}}
    with pytest.raises(ValueError, match="비었습니다"):
        lookup.query("type:srs", page_size=1)
    client._get = lambda *a: {"errors": [{"detail": "bad query"}]}
    with pytest.raises(ValueError, match="0건"):
        lookup.query("type:srs")
