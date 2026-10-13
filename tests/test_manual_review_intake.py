"""Validates: REQ-MANUAL-019 — 파일 이름으로 검증 정보 채우기 (TEST-MANUAL-022)."""

from app.modules.manual_review.intake import suggest_revision_inputs

PRODUCTS = ["VXvue", "Bellalun Viewer"]
MANUAL_TYPES = {"VXvue": ["VXvue Service Manual", "VXvue DICOM Conformance Statement"], "Bellalun Viewer": []}


def _revision(revision_id, status="REVIEWED", round_number=0, label="V1.1.0 · W1", path="r.docx", manual="VXvue Service Manual", version="1.1.0"):
    return {
        "id": revision_id, "product": "VXvue", "manual_name": manual, "target_version": version, "status": status,
        "round_number": round_number, "revision_label": label, "source_path": path,
    }


def _suggest(file_name, versions=None, revisions=None, comments=0):
    return suggest_revision_inputs(
        file_name, PRODUCTS, MANUAL_TYPES, {"VXvue": versions if versions is not None else ["1.1.0"]}, revisions or [],
        open_comment_count=lambda _revision_id: comments,
    )


def test_suggests_product_manual_and_version_from_vxvue_filename():
    result = _suggest("(매뉴얼) VXvue Service Manual.V1.1.0W1_KO.docx")

    assert (result["product"], result["manual_name"], result["target_version"]) == ("VXvue", "VXvue Service Manual", "1.1.0")
    assert result["revision_label"] == "V1.1.0 · W1"
    assert result["file_revision"] == "V1.1.0W1"
    assert result["notes"] == []
    assert result["complete"] is True


def test_picks_latest_finished_parent_and_counts_open_comments():
    revisions = [
        _revision(10),
        _revision(12, round_number=1, label="V1.1.0 · W2"),
        _revision(13, status="FAILED", round_number=2, label="V1.1.0 · W3"),
        _revision(14, manual="VXvue DICOM Conformance Statement"),
    ]
    result = _suggest("(매뉴얼) VXvue Service Manual.V1.1.0W3_KO.docx", revisions=revisions, comments=3)

    assert result["parent_revision_id"] == 12
    assert result["parent_label"] == "#12 VXvue Service Manual V1.1.0 · W2"
    assert result["open_comment_count"] == 3
    assert result["revision_label"] == "V1.1.0 · W3"


def test_pdf_only_inherits_pdf_parent():
    revisions = [_revision(20, path="manual.docx"), _revision(21, status="BASELINE", label="V1.1.0 · Baseline", path="manual.pdf")]

    pdf = _suggest("VXvue Service Manual.V1.1.0.pdf", revisions=revisions)
    first_pdf = _suggest("VXvue Service Manual.V1.1.0.pdf", revisions=revisions[:1])

    assert pdf["parent_revision_id"] == 21 and pdf["revision_label"] == "V1.1.0 · W2"
    assert first_pdf["parent_revision_id"] is None and first_pdf["revision_label"] == "V1.1.0 · Baseline"


def test_document_version_suggests_latest_registered_product_version():
    result = _suggest("(매뉴얼) VXvue DICOM Conformance Statement.V4.4W1.docx", versions=["1.0.11", "1.1.0"])

    assert result["manual_name"] == "VXvue DICOM Conformance Statement"
    assert result["target_version"] == "1.1.0"
    assert any("V4.4" in note and "V1.1.0" in note for note in result["notes"])


def test_newer_version_with_same_first_number_starts_new_product_version():
    result = _suggest("(매뉴얼) VXvue Service Manual.V1.2.0W1_KO.docx", versions=["1.0", "1.1.0"])

    assert result["target_version"] == "1.2.0"
    assert result["notes"] == ["새 제품 버전 V1.2.0으로 시작합니다."]


def test_unknown_manual_name_is_kept_with_note():
    result = _suggest("(매뉴얼) VXvue Quick Guide.V1.1.0W1_KO.docx")

    assert result["manual_name"] == "VXvue Quick Guide"
    assert any("목록에 없는 매뉴얼" in note for note in result["notes"])


def test_file_round_that_differs_from_verification_round_is_noted():
    result = _suggest("(매뉴얼) VXvue Service Manual.V1.1.0W2_KO.docx")

    assert result["revision_label"] == "V1.1.0 · W1"
    assert "파일 이름은 W2이지만 검증 회차는 W1입니다." in result["notes"]


def test_unknown_product_leaves_blank_and_incomplete():
    result = _suggest("(매뉴얼) xMRT Manual_V1.1W1_KO.docx")

    assert result["product"] == ""
    assert result["complete"] is False
    assert "파일 이름에서 제품을 찾지 못했습니다." in result["notes"]
