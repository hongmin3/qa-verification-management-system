import zipfile

from app.modules.manual_review.docx_track_changes import extract_track_changes, extract_track_changes_from_xml

W_NS_DECL = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _document_xml(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {W_NS_DECL}><w:body>{body}</w:body></w:document>"
    ).encode("utf-8")


def test_extracts_insertion_and_deletion():
    body = (
        "<w:p>"
        '<w:ins w:id="1" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>신규 문구를 추가한다.</w:t></w:r></w:ins>"
        '<w:del w:id="2" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:delText>기존 문구를 삭제한다.</w:delText></w:r></w:del>"
        "</w:p>"
    )
    result = extract_track_changes_from_xml(_document_xml(body))

    assert [c.kind for c in result.changes] == ["insertion", "deletion"]
    assert result.changes[0].text == "신규 문구를 추가한다."
    assert result.changes[0].author == "연구소"
    assert result.changes[1].text == "기존 문구를 삭제한다."
    assert result.plain_text == "신규 문구를 추가한다."


def test_change_index_in_paragraph_counts_within_same_paragraph():
    """한 문단에 변경이 여러 건이면 comment_writer.py가 각각 다른 위치에 앵커링할 수 있도록
    문단 안에서의 순번을 붙인다 (0부터)."""
    body = (
        "<w:p>"
        "<w:r><w:t>문장 시작. </w:t></w:r>"
        '<w:ins w:id="1" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>첫 번째 삽입.</w:t></w:r></w:ins>"
        "<w:r><w:t> 중간 문장. </w:t></w:r>"
        '<w:del w:id="2" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:delText>두 번째 삭제.</w:delText></w:r></w:del>"
        "</w:p>"
    )
    result = extract_track_changes_from_xml(_document_xml(body))

    assert [c.change_index_in_paragraph for c in result.changes] == [0, 1]
    assert [c.paragraph_index for c in result.changes] == [0, 0]


def test_extracts_move_from_and_move_to():
    body = (
        '<w:p><w:moveFrom w:id="1" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>이동되는 문단</w:t></w:r></w:moveFrom></w:p>"
        '<w:p><w:moveTo w:id="2" w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t>이동되는 문단</w:t></w:r></w:moveTo></w:p>"
    )
    result = extract_track_changes_from_xml(_document_xml(body))

    assert [c.kind for c in result.changes] == ["move_from", "move_to"]
    assert result.plain_text == "이동되는 문단"


def test_plain_text_keeps_unchanged_runs_alongside_tracked_changes():
    body = (
        "<w:p><w:r><w:t>변경되지 않은 문장.</w:t></w:r>"
        '<w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z">'
        "<w:r><w:t> 추가된 문장.</w:t></w:r></w:ins></w:p>"
    )
    result = extract_track_changes_from_xml(_document_xml(body))

    assert result.plain_text == "변경되지 않은 문장. 추가된 문장."


def test_extract_track_changes_reads_real_docx_zip(tmp_path):
    path = tmp_path / "revised.docx"
    body = '<w:p><w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z"><w:r><w:t>추가</w:t></w:r></w:ins></w:p>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _document_xml(body).decode("utf-8"))

    result = extract_track_changes(path)

    assert result.changes[0].kind == "insertion"
    assert result.plain_text == "추가"


def test_image_inside_tracked_change_requires_human_review():
    body = (
        '<w:p><w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z"><w:r><w:drawing>'
        '<wp:docPr xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" id="1" name="로그인 화면"/>'
        '</w:drawing></w:r></w:ins></w:p>'
    )

    result = extract_track_changes_from_xml(_document_xml(body))

    assert len(result.changes) == 1
    assert result.changes[0].kind == "image_insertion"
    assert result.changes[0].text == "이미지 변경 (로그인 화면)"
    assert result.changes[0].review_required is True


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {W_NS_DECL}>"
    '<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="VTitle2"><w:name w:val="V Title 2"/><w:basedOn w:val="1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="VText0Number"><w:name w:val="V Text Number"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
    '<w:style w:type="character" w:styleId="1Char"><w:name w:val="제목 1 Char"/></w:style>'
    "</w:styles>"
).encode("utf-8")


# Validates: REQ-MANUAL-005
def test_records_section_heading_and_paragraph_text():
    from app.modules.manual_review.docx_track_changes import heading_style_ids

    body = (
        '<w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:r><w:t>로그인</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="VTitle2"/></w:pPr><w:r><w:t>잠금 정책</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>계정이 </w:t></w:r>"
        '<w:del w:author="연구소" w:date="2026-08-01T00:00:00Z"><w:r><w:delText>5분</w:delText></w:r></w:del>'
        '<w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z"><w:r><w:t>10분</w:t></w:r></w:ins>'
        "<w:r><w:t> 동안</w:t><w:br/><w:t>잠긴다.</w:t></w:r></w:p>"
    )
    result = extract_track_changes_from_xml(_document_xml(body), heading_style_ids(_STYLES))

    assert heading_style_ids(_STYLES) == {"1", "VTitle2"}
    assert [change.section_heading for change in result.changes] == ["잠금 정책", "잠금 정책"]
    assert result.changes[0].paragraph_text == "계정이 10분 동안\n잠긴다."


# Validates: REQ-MANUAL-005
def test_outline_level_alone_is_not_heading():
    from app.modules.manual_review.docx_track_changes import heading_style_ids

    body = (
        '<w:p><w:pPr><w:pStyle w:val="VText0Number"/></w:pPr><w:r><w:t>VXvue를 실행하십시오.</w:t></w:r></w:p>'
        '<w:p><w:ins w:author="연구소" w:date="2026-08-01T00:00:00Z"><w:r><w:t>새 문장</w:t></w:r></w:ins></w:p>'
    )
    result = extract_track_changes_from_xml(_document_xml(body), heading_style_ids(_STYLES))

    assert result.changes[0].section_heading == ""
