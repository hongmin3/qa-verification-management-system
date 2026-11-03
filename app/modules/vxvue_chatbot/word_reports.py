"""Create Word reports from the privately installed Guide template."""
import re
import uuid
from pathlib import Path

from docx import Document
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


def template_path(root):
    return Path(root) / 'data/vxvue_desktop/word-template.docx'


def definitions():
    string = {'type': 'string', 'maxLength': 20000}
    return [{'name': 'make_word_report', 'description': '사용자가 워드/Word/DOCX 파일을 요청하면 기본 Guide 양식으로 제출용 문서를 생성한다. 제목·본문·표를 전달한다. 문서번호·개정·날짜·작성자·검토자·승인자는 제공받은 값만 사용하며 미지정 값은 빈칸이다. 서명·승인·검증 결과를 만들지 않는다. 반환된 URL을 다운로드 링크로 안내한다. 별도 양식 요청은 우선한다.',
             'inputSchema': {'type': 'object', 'properties': {
                 'title': string, 'document_number': string, 'revision': string, 'date': string,
                 'creator': string, 'reviewer': string, 'approver': string,
                 'sections': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'items': {
                     'type': 'object', 'properties': {'heading': string,
                         'paragraphs': {'type': 'array', 'maxItems': 200, 'items': string},
                         'table': {'type': 'array', 'maxItems': 200, 'items': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': string}}},
                     'required': ['heading'], 'additionalProperties': False}}},
                 'required': ['title', 'sections'], 'additionalProperties': False},
             'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}}]


def create(root, title, sections, **metadata):
    keys = ('document_number', 'revision', 'date', 'creator', 'reviewer', 'approver')
    if not isinstance(title, str) or not title.strip() or len(title) > 500:
        raise ValueError('문서 제목은 1~500자입니다.')
    if set(metadata) - set(keys) or any(not isinstance(v, str) or len(v) > 500 for v in metadata.values()):
        raise ValueError('문서 정보는 항목당 500자까지 입력하세요.')
    if not isinstance(sections, list) or not 1 <= len(sections) <= 100:
        raise ValueError('본문은 1~100개 절로 입력하세요.')
    if len(str(sections)) > 500000:
        raise ValueError('본문 입력이 너무 큽니다.')
    source = template_path(root)
    if not source.is_file():
        raise ValueError('기본 Guide Word 양식이 설치되지 않았습니다.')
    doc = Document(source)
    values = {'title': title, **{k: metadata.get(k, '') for k in keys}}
    parts = [doc.element, *[p._element for s in doc.sections for p in (s.header, s.first_page_header, s.even_page_header, s.footer, s.first_page_footer, s.even_page_footer)]]
    for part in parts:
        for node in part.xpath('.//w:t'):
            for key, value in values.items():
                node.text = (node.text or '').replace('{{' + key + '}}', value)
    for section in sections:
        if not isinstance(section, dict) or set(section) - {'heading', 'paragraphs', 'table'}:
            raise ValueError('본문 절의 형식을 확인하세요.')
        heading, paragraphs, rows = section.get('heading'), section.get('paragraphs', []), section.get('table', [])
        if not isinstance(heading, str) or len(heading) > 20000 or not isinstance(paragraphs, list) or len(paragraphs) > 200 or any(not isinstance(p, str) or len(p) > 20000 for p in paragraphs):
            raise ValueError('절 제목과 본문 형식을 확인하세요.')
        if not isinstance(rows, list) or len(rows) > 200 or any(not isinstance(r, list) or not 1 <= len(r) <= 12 or any(not isinstance(c, str) or len(c) > 20000 for c in r) for r in rows) or (rows and any(len(r) != len(rows[0]) for r in rows)):
            raise ValueError('표의 행과 열 구성을 확인하세요.')
        doc.add_heading(heading, level=1)
        for text in paragraphs:
            doc.add_paragraph(text)
        if rows:
            table = doc.add_table(rows=0, cols=len(rows[0]), style='Table Grid')
            for row in rows:
                for cell, text in zip(table.add_row().cells, row):
                    cell.text = text
    report_id = uuid.uuid4().hex
    folder = Path(root) / 'data/vxvue_desktop/word-reports' / report_id
    folder.mkdir(parents=True, exist_ok=False)
    doc.save(folder / 'report.docx')
    return {'report_id': report_id, 'url': f'/vxvue-desktop/word-reports/{report_id}/report.docx', 'filename': 'report.docx'}


def routes(root_provider):
    router = APIRouter()

    @router.get('/word-reports/{report_id}/report.docx')
    def download(report_id: str):
        if not re.fullmatch('[0-9a-f]{32}', report_id):
            raise HTTPException(404, '문서를 찾을 수 없습니다.')
        path = Path(root_provider()) / 'data/vxvue_desktop/word-reports' / report_id / 'report.docx'
        if not path.is_file():
            raise HTTPException(404, '문서를 찾을 수 없습니다.')
        return FileResponse(path, filename='report.docx', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
