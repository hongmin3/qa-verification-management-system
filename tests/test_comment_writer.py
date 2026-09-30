import zipfile

from docx import Document

from app.modules.manual_review.comment_writer import comment_text_for, insert_comments, output_filename

W_NS_DECL = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
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


def _write_minimal_docx(path, body: str) -> None:
    xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS_DECL}><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)


def test_output_filename_matches_spec_convention():
    assert output_filename("VXvue Service Manual", "V1.1.0 W2") == "VXvue Service Manual.V1.1.0W2_KO_AI검토.docx"


def test_output_filename_sanitizes_unsafe_characters():
    assert "/" not in output_filename("A/B", "W1")


def test_comment_text_for_pass_returns_none():
    assert comment_text_for({"decision": "PASS"}) is None


def test_comment_text_for_none_decision_returns_none():
    assert comment_text_for({"decision": None}) is None


def test_comment_text_for_uses_ai_qa_comment():
    change = {"decision": "SUPPLEMENT_REQUIRED", "ai_judgment": {"qa_comment": "조건이 누락되었습니다."}}
    assert comment_text_for(change) == "조건이 누락되었습니다."


def test_comment_text_for_appends_qa_note():
    change = {"decision": "MODIFICATION_REQUIRED", "ai_judgment": {"qa_comment": "문제"}, "qa_note": "재확인 요망"}
    assert comment_text_for(change) == "문제 (QA 메모: 재확인 요망)"


def test_comment_text_for_qa_override_to_pass_suppresses_comment():
    change = {"decision": "SUPPLEMENT_REQUIRED", "qa_decision": "PASS"}
    assert comment_text_for(change) is None


def test_comment_text_for_falls_back_when_no_qa_comment():
    change = {"decision": "MISSING_SUSPECTED"}
    assert comment_text_for(change) == "[MISSING_SUSPECTED] 검토가 필요합니다."


def test_insert_comments_anchors_to_tracked_change_paragraph(tmp_path):
    body = (
        '<w:p><w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>Trial License는 재발급할 수 있다.</w:t></w:r></w:ins></w:p>"
        "<w:p><w:r><w:t>변경 없는 문단.</w:t></w:r></w:p>"
    )
    revision_path = tmp_path / "revision.docx"
    _write_minimal_docx(revision_path, body)
    changes = [{"paragraph_index": 0, "functional": True, "decision": "MODIFICATION_REQUIRED", "ai_judgment": {"qa_comment": "SRS와 다릅니다."}}]
    output_path = tmp_path / "out.docx"

    inserted = insert_comments(revision_path, changes, output_path)

    assert inserted == 1
    assert output_path.exists()
    result_doc = Document(str(output_path))
    comments = list(result_doc.comments)
    assert len(comments) == 1
    assert comments[0].author == "QA AI"
    assert comments[0].text == "SRS와 다릅니다."


def test_insert_comments_anchors_to_specific_change_not_whole_paragraph(tmp_path):
    """한 문단에 변경이 2건이면 각 Comment는 자신의 변경 요소에만 앵커링돼야 한다 — 문단
    전체(예전 동작)로 뭉치면 두 Comment가 서로의 텍스트까지 감싸게 된다."""
    body = (
        "<w:p>"
        "<w:r><w:t>문장 시작. </w:t></w:r>"
        '<w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>첫 번째 삽입.</w:t></w:r></w:ins>"
        "<w:r><w:t> 중간 문장. </w:t></w:r>"
        '<w:del w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:delText>두 번째 삭제.</w:delText></w:r></w:del>"
        "</w:p>"
    )
    revision_path = tmp_path / "revision.docx"
    _write_minimal_docx(revision_path, body)
    changes = [
        {"paragraph_index": 0, "change_index_in_paragraph": 0, "functional": True, "decision": "MODIFICATION_REQUIRED", "ai_judgment": {"qa_comment": "삽입 관련 지적"}},
        {"paragraph_index": 0, "change_index_in_paragraph": 1, "functional": True, "decision": "SUPPLEMENT_REQUIRED", "ai_judgment": {"qa_comment": "삭제 관련 지적"}},
    ]
    output_path = tmp_path / "out.docx"

    inserted = insert_comments(revision_path, changes, output_path)
    assert inserted == 2

    with zipfile.ZipFile(output_path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    # 두 Comment의 anchor가 서로의 텍스트를 침범하지 않고, 순서대로 정확히 자기 변경만 감싼다.
    start_1 = xml.index("문장 시작")
    range_start_1 = xml.index("commentRangeStart")
    insertion_text = xml.index("첫 번째 삽입")
    range_end_1 = xml.index("commentRangeEnd")
    middle_text = xml.index("중간 문장")
    range_start_2 = xml.index("commentRangeStart", range_end_1)
    deletion_text = xml.index("두 번째 삭제")
    range_end_2 = xml.index("commentRangeEnd", range_start_2)

    assert start_1 < range_start_1 < insertion_text < range_end_1 < middle_text < range_start_2 < deletion_text < range_end_2


def test_insert_comments_skips_pass_and_non_functional(tmp_path):
    body = "<w:p><w:r><w:t>변경 없음.</w:t></w:r></w:p>"
    revision_path = tmp_path / "revision.docx"
    _write_minimal_docx(revision_path, body)
    changes = [
        {"paragraph_index": 0, "functional": True, "decision": "PASS"},
        {"paragraph_index": 0, "functional": False, "decision": "MODIFICATION_REQUIRED"},
    ]
    output_path = tmp_path / "out.docx"

    inserted = insert_comments(revision_path, changes, output_path)

    assert inserted == 0


def test_insert_comments_skips_out_of_range_paragraph_index(tmp_path):
    body = "<w:p><w:r><w:t>단일 문단.</w:t></w:r></w:p>"
    revision_path = tmp_path / "revision.docx"
    _write_minimal_docx(revision_path, body)
    changes = [{"paragraph_index": 5, "functional": True, "decision": "SUPPLEMENT_REQUIRED"}]
    output_path = tmp_path / "out.docx"

    inserted = insert_comments(revision_path, changes, output_path)

    assert inserted == 0


# Validates: REQ-MANUAL-016
def test_output_filename_uses_real_revision_label_without_middle_dot():
    """화면이 만드는 실제 리비전 표기("V1.1.0 · W2")로 확인한다. 가운뎃점이 남으면 안 된다."""
    from app.modules.manual_review.router import _revision_label

    label = _revision_label("1.1.0", 0)
    assert label == "V1.1.0 · W2"
    assert output_filename("VXvue Service Manual", label) == "VXvue Service Manual.V1.1.0W2_KO_AI검토.docx"


# Validates: REQ-MANUAL-015
def test_insert_comments_detailed_reports_which_changes_got_comments(tmp_path):
    from app.modules.manual_review.comment_writer import insert_comments_detailed

    body = (
        '<w:p><w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>Trial License는 재발급할 수 있다.</w:t></w:r></w:ins></w:p>"
        "<w:p><w:r><w:t>변경 없는 문단.</w:t></w:r></w:p>"
    )
    revision_path = tmp_path / "revision.docx"
    _write_minimal_docx(revision_path, body)
    changes = [
        {"id": 7, "paragraph_index": 0, "functional": True, "decision": "MODIFICATION_REQUIRED", "ai_judgment": {"qa_comment": "SRS와 다릅니다."}},
        {"id": 8, "paragraph_index": 9, "functional": True, "decision": "SUPPLEMENT_REQUIRED"},
        {"id": 9, "paragraph_index": 1, "functional": True, "decision": "PASS"},
    ]

    inserted = insert_comments_detailed(revision_path, changes, tmp_path / "out.docx")

    assert [(item["change"]["id"], item["text"]) for item in inserted] == [(7, "SRS와 다릅니다.")]
