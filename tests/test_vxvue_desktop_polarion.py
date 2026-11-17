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


def _fields_client(calls=None):
    def get(path, params):
        if calls is not None:
            calls.append(params)
        return {"data": [
            {"id": "VXvue/VP-1", "attributes": {"status": "open", "rndReviewResult": "accept", "created": "2026-01-01",
                                                 "description": {"type": "text/html", "value": "<p style='font-size:10pt'>x</p>" * 50}},
             "relationships": {"author": {"data": {"id": "u1"}}}},
            {"id": "VXvue/VP-2", "attributes": {"status": "closed"}}],
            "meta": {"totalCount": 2}}
    return SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), _get=get)


# Validates: REQ-VXVUECHATBOT-024
def test_query_fields_keeps_only_requested_attributes_and_reports_absent(tmp_path):
    calls = []
    lookup = PolarionLookup(tmp_path, _fields_client(calls))
    full = lookup.query("type:issue")
    result = lookup.query("type:issue", fields=["status", "rndReviewResult"])
    assert calls[-1]["query"] == "type:issue" and calls[-1]["fields[workitems]"] == "@all"
    body = json.loads(result["text"]) if not result["truncated"] else None
    assert body is not None
    assert body["fields"] == ["status", "rndReviewResult"]
    assert body["rows"][0] == {"id": "VXvue/VP-1", "status": "open", "rndReviewResult": "accept"}
    assert body["rows"][1] == {"id": "VXvue/VP-2", "status": "closed", "absent": ["rndReviewResult"]}
    assert "response" not in body and "readable" not in body
    assert result["items"] == ["VXvue/VP-1", "VXvue/VP-2"]
    assert result["total_chars"] < full["total_chars"] / 3
    again = lookup.read(result["result_id"])
    assert again["text"] == result["text"]


# Validates: REQ-VXVUECHATBOT-024
def test_query_fields_validation(tmp_path):
    lookup = PolarionLookup(tmp_path, _fields_client())
    for bad in ([], ["x"] * 31, ["bad name"], "status", [1]):
        with pytest.raises(ValueError):
            lookup.query("type:issue", fields=bad)


def _png(width=40, height=20):
    import fitz
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pix.clear_with(200)
    return pix.tobytes("png")


# Validates: REQ-VXVUECHATBOT-025
def test_attachment_image_is_rendered_for_that_work_item_only(tmp_path):
    seen = []
    def get_bytes(path, max_bytes):
        seen.append((path, max_bytes))
        return _png()
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), get_bytes=get_bytes)
    lookup = PolarionLookup(tmp_path, client)
    content = lookup.attachment("vp-12", "1-expected screen.png")
    assert seen[0][0] == "/projects/VXvue/workitems/VP-12/attachments/1-expected%20screen.png/content"
    assert seen[0][1] == 10 * 1024 * 1024
    meta = json.loads(content[0]["text"])
    assert meta["vp_id"] == "VP-12" and meta["attachment_id"] == "1-expected screen.png"
    assert meta["visual_status"] == "rendered-not-reviewed"
    assert content[1]["type"] == "image" and content[1]["mimeType"] == "image/png"
    # The full REST id is accepted only for the same project and work item.
    lookup.attachment("VP-12", "VXvue/VP-12/2-actual.png")
    assert seen[-1][0].endswith("/VP-12/attachments/2-actual.png/content")
    for vp, att in (("VP-12", "VXvue/VP-13/2-actual.png"), ("VP-12", "Other/VP-12/2.png"), ("VP-12", "../x.png"),
                    ("VP-12", "a/b"), ("VP-12", ""), ("../VP-1", "1.png")):
        with pytest.raises(ValueError):
            lookup.attachment(vp, att)
    assert not list((tmp_path / "data/vxvue_desktop/polarion").glob("*")) if (tmp_path / "data/vxvue_desktop/polarion").exists() else True


# Validates: REQ-VXVUECHATBOT-025
def test_large_or_transparent_attachment_is_scaled_to_rgb_png(tmp_path):
    import base64
    import fitz
    big = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 3600, 900), True)
    big.clear_with(120)
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), get_bytes=lambda p, m: big.tobytes("png"))
    content = PolarionLookup(tmp_path, client).attachment("VP-1", "1.png")
    meta = json.loads(content[0]["text"])
    assert (meta["width"], meta["height"]) == (1800, 450)
    shown = fitz.Pixmap(base64.b64decode(content[1]["data"]))
    assert (shown.width, shown.height, shown.alpha) == (1800, 450, 0)


# Validates: REQ-VXVUECHATBOT-025
def test_attachment_rejects_non_images_and_propagates_errors(tmp_path):
    client = SimpleNamespace(config=SimpleNamespace(project_id="VXvue"), get_bytes=lambda p, m: b"%PDF-1.4 not an image")
    lookup = PolarionLookup(tmp_path, client)
    with pytest.raises(ValueError, match="이미지"):
        lookup.attachment("VP-1", "1-doc.pdf")
    client.get_bytes = lambda p, m: (_ for _ in ()).throw(PolarionError("HTTP 404"))
    with pytest.raises(PolarionError):
        lookup.attachment("VP-1", "1.png")


# Validates: REQ-VXVUECHATBOT-025
def test_client_attachment_bytes_use_get_and_size_cap(monkeypatch):
    from app.modules.daily_qa import polarion as module
    from app.modules.daily_qa.settings import PolarionSettings
    cfg = PolarionSettings(host="https://alm.example", token="pat", project_id="VXvue", srs_query="x",
                           issue_query="y", page_size=2, request_interval_seconds=0)
    client = module.ReadOnlyPolarionClient(cfg)
    class Resp:
        def __init__(self, code, body): self.status_code, self.content = code, body
        def raise_for_status(self): pass
    calls = []
    monkeypatch.setattr(module.httpx, "get", lambda url, **kw: calls.append((url, kw)) or Resp(200, b"x" * 11))
    assert client.get_bytes("/projects/VXvue/workitems/VP-1/attachments/1.png/content", 20) == b"x" * 11
    assert calls[0][0] == "https://alm.example/polarion/rest/v1/projects/VXvue/workitems/VP-1/attachments/1.png/content"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer pat"
    with pytest.raises(module.PolarionError, match="10"):
        client.get_bytes("/p", 10)
    monkeypatch.setattr(module.httpx, "get", lambda url, **kw: Resp(403, b""))
    with pytest.raises(module.PolarionError, match="403"):
        client.get_bytes("/p", 20)
