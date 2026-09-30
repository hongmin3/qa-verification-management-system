import zipfile

from docx import Document
from fastapi.testclient import TestClient

import app.modules.manual_review.router as manual_review_router
import app.modules.knowledge.router as knowledge_router
from app.core.storage import Storage
from app.main import app

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _write_minimal_docx(path) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>변경 없음.</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)


def test_home_renders_empty_state(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).get("/manual-review")

    assert response.status_code == 200
    assert "아직 등록된 검증 이력이 없습니다." in response.text


def test_navigation_is_manual_review_specific():
    response = TestClient(app).get("/manual-review")
    assert response.status_code == 200
    assert 'href="/"' in response.text
    assert 'href="/manual-review/guide"' in response.text
    assert 'href="/knowledge"' in response.text
    assert 'href="/impact-analyzer"' not in response.text
    assert 'href="/analyses"' not in response.text


def test_home_shows_registered_srs_sources(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.ensure_product("VXvue")
    storage.add_document("specification", "VXvue", "1.2.0", "", "VXvue SRS.pdf", tmp_path / "srs.pdf")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).get("/manual-review")

    assert response.status_code == 200
    assert "현재 검증 근거 SRS" in response.text
    assert "VXvue SRS.pdf" in response.text


def test_product_added_in_knowledge_appears_in_manual_review(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(knowledge_router, "storage", storage)
    monkeypatch.setattr(manual_review_router, "storage", storage)
    client = TestClient(app)

    added = client.post("/knowledge/products", data={"product": "신규 장비"}, follow_redirects=False)
    response = client.get("/manual-review")

    assert added.status_code == 303
    assert '<option value="신규 장비">신규 장비</option>' in response.text
    assert "Knowledge에서 제품을 추가" in response.text


def test_vxvue_manual_types_are_suggested_and_custom_input_is_allowed(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.ensure_product("VXvue")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).get("/manual-review")

    assert response.status_code == 200
    assert 'name="manual_name" list="manual-types-list"' in response.text
    assert "VXvue Service Manual" in response.text
    assert "VXvue DICOM Conformance Statement" in response.text


def test_revision_label_policy_separates_version_and_qa_round():
    assert manual_review_router._normalize_target_version(" V1.1.0 ") == "1.1.0"
    assert manual_review_router._revision_label("1.1.0", None) == "V1.1.0 · W1"
    assert manual_review_router._revision_label("1.1.0", 0) == "V1.1.0 · W2"
    assert manual_review_router._revision_label("1.1.0", None, True) == "V1.1.0 · Baseline"


def test_manual_review_rejects_product_not_registered_in_knowledge(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).post(
        "/manual-review/revisions",
        files={"file": ("change.docx", b"not-read-in-background", "application/octet-stream")},
        data={"product": "미등록 제품", "manual_name": "Custom Manual", "target_version": "1.0"},
    )

    assert response.status_code == 400
    assert "Knowledge에서 제품을 먼저 추가" in response.json()["detail"]


def test_upload_rejects_unsupported_extension(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).post(
        "/manual-review/revisions",
        files={"file": ("change.txt", b"plain", "text/plain")},
        data={"product": "VXvue", "manual_name": "Service Manual", "target_version": "1.0"},
    )

    assert response.status_code == 400


def test_view_missing_revision_returns_404(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).get("/manual-review/revisions/999/view")

    assert response.status_code == 404


def test_view_renders_change_and_ai_judgment(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "2026-08-01", 0, "Trial License는 재발급할 수 있다.", functional=True)
    storage.update_manual_change_judgment(
        change_id, "MODIFICATION_REQUIRED", 0.7,
        {"decision": "MODIFICATION_REQUIRED", "confidence": 0.7, "reason_codes": [], "problem": "삭제 사양 재사용", "recommended_manual_text": "", "qa_comment": "QA 코멘트 초안", "evidence": [], "needs_human_review": False, "prompt_version": 1},
    )

    response = TestClient(app).get(f"/manual-review/revisions/{revision_id}/view")

    assert response.status_code == 200
    assert "삭제 사양 재사용" in response.text
    assert "QA 코멘트 초안" in response.text
    assert "수정 필요" in response.text  # MODIFICATION_REQUIRED의 한국어 표시


def test_view_renders_missing_suspected_release_findings(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    storage.add_release_finding(revision_id, "release_note", "Added", "매뉴얼에 반영 안 된 기능", status="MISSING_SUSPECTED")
    storage.add_release_finding(revision_id, "release_note", "Added", "정상 반영된 기능", status="FOUND", matched_change_id=1)

    response = TestClient(app).get(f"/manual-review/revisions/{revision_id}/view")

    assert response.status_code == 200
    assert "매뉴얼에 반영 안 된 기능" in response.text
    assert "정상 반영된 기능" not in response.text  # FOUND 항목은 누락 의심 섹션에 표시하지 않음


def test_qa_can_confirm_cross_manual_impact(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    impact_id = storage.add_cross_manual_impact(
        revision_id, "Operation Manual", "operation.pdf", "release.docx", "Changed",
        "로그인 흐름", "로그인 화면 설명", 1.25,
    )

    view = TestClient(app).get(f"/manual-review/revisions/{revision_id}/view")
    updated = TestClient(app).post(
        f"/manual-review/revisions/{revision_id}/cross-manual/{impact_id}/status",
        data={"qa_status": "IMPACT_CONFIRMED"}, follow_redirects=False,
    )

    assert "다른 Manual 영향 확인" in view.text
    assert "Operation Manual" in view.text
    assert updated.status_code == 303
    assert storage.list_cross_manual_impacts(revision_id)[0]["qa_status"] == "IMPACT_CONFIRMED"


def test_qa_decision_override_persists(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "2026-08-01", 0, "문구", functional=True)
    storage.update_manual_change_judgment(change_id, "SUPPLEMENT_REQUIRED", 0.6, {"decision": "SUPPLEMENT_REQUIRED", "confidence": 0.6})

    response = TestClient(app).post(
        f"/manual-review/revisions/{revision_id}/changes/{change_id}/qa-decision",
        data={"qa_decision": "PASS", "qa_note": "QA가 직접 확인함"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    change = storage.get_manual_change(change_id)
    assert change["qa_decision"] == "PASS"
    assert change["qa_note"] == "QA가 직접 확인함"


def test_comment_docx_download_returns_400_when_nothing_to_flag(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_path = tmp_path / "r.docx"
    _write_minimal_docx(revision_path)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", revision_path)
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "", 0, "문구", functional=True)
    storage.update_manual_change_judgment(change_id, "PASS", 0.9, {"decision": "PASS"})

    response = TestClient(app).get(f"/manual-review/revisions/{revision_id}/comment-docx")

    assert response.status_code == 400


def test_comment_docx_download_uses_product_specific_author(monkeypatch, tmp_path):
    """제품명이 코드에 고정되어 있지 않고 revision.product로부터 조립되는지 확인 —
    다른 제품(예: Bellalun Viewer)으로도 그대로 재사용 가능해야 한다."""
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_path = tmp_path / "r.docx"
    _write_minimal_docx(revision_path)
    revision_id = storage.add_manual_revision("Bellalun Viewer", "Operation Manual", "W1", revision_path)
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "", 0, "문구", functional=True)
    storage.update_manual_change_judgment(change_id, "SUPPLEMENT_REQUIRED", 0.6, {"decision": "SUPPLEMENT_REQUIRED", "qa_comment": "조건 추가 필요"})

    response = TestClient(app).get(f"/manual-review/revisions/{revision_id}/comment-docx")

    assert response.status_code == 200
    saved_path = tmp_path / "downloaded.docx"
    saved_path.write_bytes(response.content)
    comments = list(Document(str(saved_path)).comments)
    assert len(comments) == 1
    assert comments[0].author == "Bellalun Viewer QA AI"


def test_comment_docx_download_missing_revision_returns_404(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)

    response = TestClient(app).get("/manual-review/revisions/999/comment-docx")

    assert response.status_code == 404


def test_qa_decision_for_wrong_revision_returns_404(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "2026-08-01", 0, "문구", functional=True)

    response = TestClient(app).post(
        f"/manual-review/revisions/999999/changes/{change_id}/qa-decision",
        data={"qa_decision": "PASS"},
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_qa_can_confirm_prior_comment_as_resolved(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    round1 = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "w1.docx")
    change_id = storage.add_manual_change(round1, "insertion", "연구소", "", 0, "조건 설명", functional=True)
    comment_id = storage.add_manual_comment(change_id, round_number=1, comment_text="조건을 보강하세요.")
    round2 = storage.add_manual_revision(
        "VXvue", "Service Manual", "W2", tmp_path / "w2.docx", round_number=1,
        parent_revision_id=round1, baseline_revision_id=round1,
    )

    response = TestClient(app).post(
        f"/manual-review/revisions/{round2}/comments/{comment_id}/status",
        data={"status": "RESOLVED"}, follow_redirects=False,
    )

    assert response.status_code == 303
    comment = storage.get_manual_comment(comment_id)
    assert comment["status"] == "RESOLVED"
    assert comment["resolved_in_revision_id"] == round2


# Validates: REQ-MANUAL-002
def test_start_revision_rejected_when_daily_token_limit_exceeded(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.ensure_product("VXvue")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    monkeypatch.setattr(manual_review_router, "_daily_token_status", lambda: {"used": 1200, "limit": 1000, "exceeded": True})

    response = TestClient(app).post(
        "/manual-review/revisions",
        files={"file": ("change.docx", b"not-read", "application/octet-stream")},
        data={"product": "VXvue", "manual_name": "Service Manual", "target_version": "1.1.0"},
    )

    assert response.status_code == 429
    assert "1,200" in response.json()["detail"] and "1,000" in response.json()["detail"]
    assert storage.list_versions("VXvue") == []


# Validates: REQ-MANUAL-002
def test_rejected_parent_check_leaves_no_product_version(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    storage.ensure_product("VXvue")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    monkeypatch.setattr(manual_review_router, "_daily_token_status", lambda: {"used": 0, "limit": 0, "exceeded": False})

    response = TestClient(app).post(
        "/manual-review/revisions",
        files={"file": ("change.docx", b"not-read", "application/octet-stream")},
        data={"product": "VXvue", "manual_name": "Service Manual", "target_version": "9.9.9", "parent_revision_id": "424242"},
    )

    assert response.status_code == 400
    assert "9.9.9" not in storage.list_versions("VXvue")


# Validates: REQ-MANUAL-002
def test_reference_doc_reuse_requires_same_product_version(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    old_path = tmp_path / "rn-1.0.docx"
    old_path.write_bytes(b"x")
    storage.add_document("release_note", "VXvue", "1.0.0", "", "RN 1.0.docx", old_path)

    assert manual_review_router._register_or_reuse_reference_doc("release_note", "VXvue", "1.1.0", "V1.1.0 · W1", None) is None

    same_path = tmp_path / "rn-1.1.docx"
    same_path.write_bytes(b"x")
    storage.add_document("release_note", "VXvue", "1.1.0", "", "RN 1.1.docx", same_path)
    storage.add_document("release_note", "VXvue", "1.2.0", "", "RN 1.2.docx", tmp_path / "rn-1.2.docx")

    assert manual_review_router._register_or_reuse_reference_doc("release_note", "VXvue", "1.1.0", "V1.1.0 · W1", None) == same_path


# Validates: REQ-MANUAL-002
def test_uploaded_reference_doc_is_registered_with_product_version(monkeypatch, tmp_path):
    import io

    from starlette.datastructures import UploadFile as StarletteUploadFile

    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    monkeypatch.setattr(manual_review_router, "_manual_revision_dir", lambda: tmp_path)
    upload = StarletteUploadFile(io.BytesIO(b"doc"), filename="RN.docx")

    path = manual_review_router._register_or_reuse_reference_doc("release_note", "VXvue", "1.1.0", "V1.1.0 · W1", upload)

    assert path is not None and path.parent == tmp_path
    assert [doc["version"] for doc in storage.active_documents("release_note", "VXvue")] == ["1.1.0"]


# Validates: REQ-MANUAL-014
def test_qa_decision_rejects_unknown_value(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "W1", tmp_path / "r.docx")
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "", 0, "문구", functional=True)

    response = TestClient(app).post(
        f"/manual-review/revisions/{revision_id}/changes/{change_id}/qa-decision",
        data={"qa_decision": "LOOKS_FINE"}, follow_redirects=False,
    )

    assert response.status_code == 400
    assert storage.get_manual_change(change_id)["qa_decision"] is None


# Validates: REQ-MANUAL-015
def test_comment_docx_download_saves_comments_once_per_change(monkeypatch, tmp_path):
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    monkeypatch.setattr(manual_review_router, "_comment_output_dir", lambda: tmp_path / "out")
    revision_path = tmp_path / "r.docx"
    _write_minimal_docx(revision_path)
    revision_id = storage.add_manual_revision("VXvue", "Service Manual", "V1.1.0 · W1", revision_path)
    change_id = storage.add_manual_change(revision_id, "insertion", "연구소", "", 0, "문구", functional=True)
    storage.update_manual_change_judgment(change_id, "SUPPLEMENT_REQUIRED", 0.6, {"decision": "SUPPLEMENT_REQUIRED", "qa_comment": "조건 추가 필요"})
    client = TestClient(app)

    first = client.get(f"/manual-review/revisions/{revision_id}/comment-docx")
    second = client.get(f"/manual-review/revisions/{revision_id}/comment-docx")

    assert first.status_code == 200 and second.status_code == 200
    saved = storage.list_open_comments_for_revision(revision_id)
    assert [(item["change_id"], item["comment_text"], item["status"]) for item in saved] == [(change_id, "조건 추가 필요", "OPEN")]


# Validates: REQ-MANUAL-017
def test_guide_describes_current_features_and_claude_route():
    response = TestClient(app).get("/manual-review/guide")

    assert response.status_code == 200
    text = response.text
    assert "Revision 표기를 입력" not in text
    assert "준비 중: Cross-Manual" not in text
    assert "제품 버전" in text
    assert "Claude" in text and "scripts/manual_review_local.py" in text


# Validates: REQ-MANUAL-002
def test_daily_token_status_uses_shared_core_calculation(monkeypatch, tmp_path):
    """하루 토큰 한도 검사는 공용 계산(app.core.usage)을 이 화면의 저장소로 부른다 (OPEN_QUESTIONS 8-9)."""
    storage = Storage(tmp_path / "app.db")
    monkeypatch.setattr(manual_review_router, "storage", storage)
    seen = []

    def fake_status(given_storage=None, now=None):
        seen.append(given_storage)
        return {"used": 7, "limit": 5, "exceeded": True}

    monkeypatch.setattr(manual_review_router, "daily_token_status", fake_status)

    assert manual_review_router._daily_token_status() == {"used": 7, "limit": 5, "exceeded": True}
    assert seen == [storage]
