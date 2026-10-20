"""Team upload/download entry point for the existing VXvue desktop guide."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from .shared_review import ReviewStore, _identifier
from .source_inventory import MAX_FILE_BYTES


def registered_source(root, document_id):
    base = Path(root) / 'data/product_knowledge/vxvue'
    try:
        manifest = json.loads((base / 'manifest.json').read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('등록된 지식 목록을 읽을 수 없습니다.') from exc
    record = next((x for x in manifest.get('assets', []) if x.get('file_name') == document_id
                   and x.get('kind') == 'manual' and not x.get('error')), None)
    if not record or Path(document_id).name != document_id or '\\' in document_id:
        raise ValueError('등록된 매뉴얼 ID를 확인하세요.')
    original = (base / 'original').resolve()
    path = (original / 'manual' / document_id).resolve()
    if not path.is_relative_to(original) or not path.is_file():
        raise ValueError('등록된 원본을 찾을 수 없습니다.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != record.get('sha256'):
        raise ValueError('등록 원본의 해시가 다릅니다. 지식 동기화를 확인하세요.')
    return ReviewStore(root).add(data, document_id)


def review_router(root_provider):
    router = APIRouter()

    def page(message='', status=200):
        choices = []
        try:
            manifest = json.loads((Path(root_provider()) / 'data/product_knowledge/vxvue/manifest.json').read_text('utf-8'))
            choices = [x['file_name'] for x in manifest.get('assets', []) if x.get('kind') == 'manual'
                       and Path(x.get('file_name', '')).suffix.lower() in ('.docx', '.pdf') and not x.get('error')]
        except (OSError, ValueError):
            pass
        options = ''.join(f'<option value="{html.escape(n, quote=True)}">{html.escape(n)}</option>' for n in choices)
        template = (Path(__file__).parent / 'templates/shared-review-upload.html').read_text('utf-8')
        return HTMLResponse(template.replace('{{OPTIONS}}', options).replace('{{RESULT}}', message), status_code=status,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @router.get('', response_class=HTMLResponse)
    @router.get('/', response_class=HTMLResponse)
    def upload_page():
        return page()

    @router.post('/upload', response_class=HTMLResponse)
    async def upload(request: Request):
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or origin and urlsplit(origin).netloc != request.headers.get('host'):
            return page('<p>같은 서버의 업로드 화면에서 다시 시도하세요.</p>', 403)
        try:
            size = int(request.headers.get('content-length', '0'))
            if not 0 < size <= 3 * MAX_FILE_BYTES + 65536:
                raise ValueError('업로드 전체 크기를 확인하세요(파일별 최대 128MB).')
            form = await request.form(max_files=3, max_fields=4, max_part_size=65536)
            manual = form.get('manual')
            document_id = str(form.get('document_id') or '')
            uploaded = manual is not None and getattr(manual, 'filename', '')
            if bool(uploaded) == bool(document_id):
                raise ValueError('등록 문서를 선택하거나 새 파일 하나를 업로드하세요.')
            store = ReviewStore(root_provider())
            if uploaded:
                data = await manual.read(MAX_FILE_BYTES + 1)
                source = store.add(data, manual.filename)
            else:
                source = registered_source(root_provider(), document_id)
            for field, basis in (('saved_pdf', 'saved'), ('final_pdf', 'final')):
                item = form.get(field)
                if item is not None and getattr(item, 'filename', ''):
                    if Path(item.filename).suffix.lower() != '.pdf':
                        raise ValueError('대응 출력본은 PDF 파일이어야 합니다.')
                    attached = store.attach_pdf(source['source_id'], await item.read(MAX_FILE_BYTES + 1), basis)
                    source['source_id'] = attached['source_id']
            sid = source['source_id']
            message = f'<section class="result"><h2>검토 자료 준비 완료</h2><p>{html.escape(source["name"])}</p><p>자료 ID</p><pre>{sid}</pre><p>VXvue AI 대화에 아래 문장을 입력하세요.</p><pre>자료 {sid}의 매뉴얼을 최신 QA 지침과 사양·릴리즈노트·메모를 바탕으로 검토해 주세요. 현재 문구는 원문 그대로 인용하고 결과 보고서를 만들어 주세요.</pre></section>'
            return page(message)
        except (ValueError, OSError) as exc:
            return page('<p class="error">' + html.escape(str(exc)) + '</p>', 400)

    @router.get('/files/{report_id}/{filename}')
    def download(report_id: str, filename: str):
        from fastapi import HTTPException
        try:
            _identifier(report_id)
        except ValueError:
            raise HTTPException(404, '보고서를 찾을 수 없습니다.')
        if filename not in ('manual_review.html', 'manual_review.xlsx', 'report.json'):
            raise HTTPException(404, '보고서 파일을 확인하세요.')
        path = ReviewStore(root_provider()).base / 'reports' / report_id / filename
        if not path.is_file():
            raise HTTPException(404, '보고서를 찾을 수 없습니다.')
        return FileResponse(path, filename=filename, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
