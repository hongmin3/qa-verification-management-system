# Validates: REQ-SYNC-003
# Validates: NFR-SEC-001
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip('bs4', reason='SRS 수집 PC의 선택 라이브러리')

from app.modules.knowledge.srs_crawler.richtext import normalize_rich_text


def test_image_resolved_replaces_src(tmp_path):
    img_file = tmp_path / "shot.png"
    img_file.write_bytes(b"fake-png-bytes")

    def resolve_image(filename):
        assert filename == "shot.png"
        return img_file, None

    html = '<p>before</p><img src="workitemimg:shot.png"/><p>after</p>'
    result = normalize_rich_text(html, resolve_image=resolve_image, resolve_link_title=lambda _id: None)

    assert img_file.resolve().as_uri() in result.html
    assert result.images == [{"filename": "shot.png", "local_path": str(img_file), "ok": True, "error": None}]


def test_image_failure_is_reported_not_silently_dropped():
    def resolve_image(filename):
        return None, "다운로드 실패"

    html = '<img src="workitemimg:missing.png"/>'
    result = normalize_rich_text(html, resolve_image=resolve_image, resolve_link_title=lambda _id: None)

    assert "이미지 로드 실패" in result.html
    assert result.images[0]["ok"] is False
    assert result.images[0]["error"] == "다운로드 실패"


def test_workitem_reference_resolved_to_visible_link():
    html = '<span class="polarion-rte-link" data-type="workItem" id="fake" data-item-id="VP-466"></span>'

    def resolve_link_title(item_id):
        assert item_id == "VP-466"
        return "External Program"

    result = normalize_rich_text(html, resolve_image=lambda f: (None, "n/a"), resolve_link_title=resolve_link_title)

    assert "VP-466 - External Program" in result.html
    assert result.links == [{"target_id": "VP-466", "resolved": True, "title": "External Program"}]


def test_broken_workitem_reference_is_flagged_not_blank():
    html = '<span class="polarion-rte-link" data-item-id="VP-9999999"></span>'
    result = normalize_rich_text(html, resolve_image=lambda f: (None, "n/a"), resolve_link_title=lambda _id: None)
    assert "확인 불가" in result.html
    assert result.links[0]["resolved"] is False


def test_strikethrough_and_underline_styles_preserved():
    html = (
        '<span style="text-decoration: line-through;">removed text</span>'
        '<span style="text-decoration: underline;">added text</span>'
    )
    result = normalize_rich_text(html, resolve_image=lambda f: (None, "n/a"), resolve_link_title=lambda _id: None)
    assert "line-through" in result.html
    assert "underline" in result.html


def test_script_tag_is_stripped():
    html = '<p>text</p><script>alert(1)</script>'
    result = normalize_rich_text(html, resolve_image=lambda f: (None, "n/a"), resolve_link_title=lambda _id: None)
    assert "<script" not in result.html


def test_rendered_polarion_hyperlink_rewritten_to_internal_anchor():
    html = (
        '<a href="/polarion/#/project/VXvue/workitem?id=VP-673" target="_top" class="polarion-Hyperlink">'
        '<img src="/polarion/icons/group/vieworks_srs3.png"/><span>VP-673</span></a>'
    )
    result = normalize_rich_text(html, resolve_image=lambda f: (None, "n/a"), resolve_link_title=lambda _id: "General")

    assert 'href="#srs-VP-673"' in result.html
    assert "/polarion/#/project" not in result.html
    assert any(l["target_id"] == "VP-673" for l in result.links)


def test_url_encoded_workitemimg_filename_is_decoded_before_matching():
    seen_filenames = []

    def resolve_image(filename):
        seen_filenames.append(filename)
        return None, "n/a"

    html = '<img src="workitemimg:screenshot-20250915-101834_%282%29.png"/>'
    normalize_rich_text(html, resolve_image=resolve_image, resolve_link_title=lambda _id: None)

    assert seen_filenames == ["screenshot-20250915-101834_(2).png"]


def test_empty_html_returns_empty():
    result = normalize_rich_text(None, resolve_image=lambda f: (None, "n/a"), resolve_link_title=lambda _id: None)
    assert result.html == ""
