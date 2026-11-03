"""Validates: REQ-VXVUECHATBOT-018."""
import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.modules.vxvue_chatbot.word_reports import create, template_path, routes


def test_report_and_download(tmp_path):
    path = template_path(tmp_path)
    path.parent.mkdir(parents=True)
    doc = Document()
    doc.add_paragraph('{{title}} {{creator}} {{document_number}}')
    doc.save(path)
    original = path.read_bytes()
    result = create(tmp_path, '보고서', [{'heading': '결과', 'paragraphs': ['미확인'], 'table': [['항목', '내용'], ['A', 'B']]}])
    output = path.parent / 'word-reports' / result['report_id'] / 'report.docx'
    report = Document(output)
    assert report.paragraphs[0].text == '보고서  '
    assert report.tables[0].cell(1, 1).text == 'B'
    assert path.read_bytes() == original
    app = FastAPI()
    app.include_router(routes(lambda: tmp_path), prefix='/vxvue-desktop')
    client = TestClient(app)
    assert client.get(result['url']).content == output.read_bytes()
    assert client.get('/vxvue-desktop/word-reports/invalid/report.docx').status_code == 404
    with pytest.raises(ValueError):
        create(tmp_path, 'x', [{'heading': 'h', 'table': [['a'], ['b', 'c']]}])


def test_missing_template(tmp_path):
    with pytest.raises(ValueError, match='Guide'):
        create(tmp_path, 'x', [{'heading': 'h'}])


def test_tool_available():
    from app.modules.vxvue_chatbot.mcp_server import tools
    from app.modules.vxvue_chatbot.desktop import definitions
    for source in (tools(), definitions()):
        tool = next(t for t in source if t['name'] == 'make_word_report')
        assert tool['annotations']['readOnlyHint'] is False
