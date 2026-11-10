"""REQ-BELLALUNSHARED-001..003: synthetic sources and authenticated real RPC."""
import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import zipfile

import fitz
import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.vxvue_chatbot import desktop


@pytest.fixture
def shared(tmp_path, monkeypatch):
    base = tmp_path / 'data/product_knowledge/bellalun-viewer'
    assets = []
    for kind, name, text in (
        ('specification', '(사양서) Exposure.pdf', 'EXPOSURE_UNIQUE'),
        ('manual', '(매뉴얼) Operation.docx', 'MANUAL_UNIQUE'),
        ('testcase', '(TC) Existing.md', 'SRS-TEST-001 TC_UNIQUE'),
        ('qa_rules', 'Bellalun Viewer QA 지침.md', '# 통합 QA\nQA_UNIQUE\nUse C:/Users/Example/Private.md then read "Apply".\n' + '시험 기준\n' * 100),
        ('skill_instruction', '[Skill 지침] Bellalun Viewer.md', '# 공통 Skill\nSKILL_UNIQUE\n개인 경로: `C:/Users/Example/Private/Skill.md`'),
    ):
        path = base / 'original' / kind / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.pdf':
            doc = fitz.open(); doc.new_page().insert_text((72, 72), text); doc.save(path); doc.close()
        elif path.suffix == '.docx':
            doc = Document(); doc.add_paragraph(text); doc.save(path)
        else:
            path.write_text(text, encoding='utf8')
        assets.append({'kind': kind, 'file_name': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = base / 'manifest.json'
    manifest.write_text(json.dumps({'product': 'Bellalun Viewer', 'assets': assets}), encoding='utf8')
    token = tmp_path / 'data/vxvue_desktop/access-token.txt'
    token.parent.mkdir(parents=True); token.write_text('x' * 48)
    monkeypatch.setattr(desktop, 'get_settings', lambda: SimpleNamespace(root=tmp_path))
    desktop.reset_cache()
    app = FastAPI(); app.include_router(desktop.router, prefix='/vxvue-desktop')
    with TestClient(app, client=('10.0.0.5', 50000)) as client:
        client.headers['Authorization'] = 'Bearer ' + 'x' * 48
        yield client, base, manifest
    desktop.reset_cache()


def rpc(client, name, **args):
    response = client.post('/vxvue-desktop/rpc', json={'jsonrpc': '2.0', 'id': 1,
        'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
    assert response.status_code == 200
    return response.json()['result']


def value(result):
    assert not result.get('isError'), result
    return json.loads(result['content'][0]['text'])


def test_bellalun_status_search_workflow_pagination_and_pdf(shared):
    client, base, _ = shared
    listed = client.post('/vxvue-desktop/rpc', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).json()
    definitions = {t['name']: t for t in listed['result']['tools']}
    assert definitions['bellalun_get_workflow']['annotations']['readOnlyHint'] is True
    status = value(rpc(client, 'bellalun_get_status'))
    assert status['scope']['files'] == 5
    assert status['snapshot_id'].startswith('bellalun-')
    sid = status['snapshot_id']
    found = value(rpc(client, 'bellalun_search_documents', snapshot_id=sid, query='MANUAL_UNIQUE'))
    assert len(found['results']) == 1
    source = value(rpc(client, 'bellalun_get_document', snapshot_id=sid, document_id='(매뉴얼) Operation.docx'))
    assert 'MANUAL_UNIQUE' in json.dumps(source)
    assert str(base) not in json.dumps(source) + json.dumps(status)
    whole = value(rpc(client, 'bellalun_get_workflow', snapshot_id=sid))
    parts, offset = [], 0
    while True:
        part = value(rpc(client, 'bellalun_get_workflow', snapshot_id=sid, offset=offset, limit=137))
        parts.append(part['text'])
        if not part['truncated']: break
        offset = part['next_offset']
    assert ''.join(parts) == whole['text']
    assert 'QA_UNIQUE' in whole['text'] and 'SKILL_UNIQUE' in whole['text']
    assert 'C:/Users/' not in whole['text']
    exported = value(rpc(client, 'bellalun_get_document', snapshot_id=sid, document_id='[Skill 지침] Bellalun Viewer.md'))
    assert 'C:/Users/' not in json.dumps(exported)
    qa_source = value(rpc(client, 'bellalun_get_document', snapshot_id=sid, document_id='Bellalun Viewer QA 지침.md'))
    assert 'Apply' in json.dumps(qa_source) and 'C:/Users/' not in json.dumps(qa_source)
    assert 'Polarion' in whole['text']  # explicitly excludes VXvue-only rules
    page = rpc(client, 'bellalun_get_document_page', snapshot_id=sid, document_id='(사양서) Exposure.pdf', page=1)
    assert any(c['type'] == 'image' for c in page['content'])
    assert rpc(client, 'bellalun_get_document_page', snapshot_id=sid, document_id='(매뉴얼) Operation.docx', page=1)['isError']


def test_bellalun_old_snapshot_and_failed_sources(shared):
    client, base, manifest = shared
    old = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    path = base / 'original/qa_rules/Bellalun Viewer QA 지침.md'
    path.write_text('# 바뀐 QA\nUPDATED_UNIQUE', encoding='utf8')
    assert rpc(client, 'bellalun_get_status')['isError']
    data = json.loads(manifest.read_text(encoding='utf8'))
    next(a for a in data['assets'] if a['kind'] == 'qa_rules')['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data), encoding='utf8')
    new = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    assert old != new
    assert 'QA_UNIQUE' in value(rpc(client, 'bellalun_get_workflow', snapshot_id=old))['text']
    assert 'UPDATED_UNIQUE' in value(rpc(client, 'bellalun_get_workflow', snapshot_id=new))['text']
    data['assets'] = [a for a in data['assets'] if a['kind'] != 'testcase']
    manifest.write_text(json.dumps(data), encoding='utf8')
    assert rpc(client, 'bellalun_get_status')['isError']
    assert value(rpc(client, 'bellalun_search_documents', snapshot_id=old, query='TC_UNIQUE'))['results']


def test_bellalun_manual_review_uses_own_snapshot_and_selection(shared):
    client, _, _ = shared
    sid = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    summary = value(rpc(client, 'bellalun_get_manual_review', snapshot_id=sid,
                        document_id='(매뉴얼) Operation.docx'))
    assert summary['paragraph_count'] == 1
    selected = value(rpc(client, 'bellalun_get_manual_review', snapshot_id=sid,
                         source_id=summary['source_id'], view='paragraphs', changed_only=True))
    assert selected['total'] == 0 and selected['selection']['omitted'] == 1
    assert rpc(client, 'bellalun_get_manual_review', snapshot_id=sid, document_id='VXvue.docx')['isError']


def test_bellalun_rejects_foreign_id_paths_arguments_tampering_and_auth(shared):
    client, _, _ = shared
    sid = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    for args in ({}, {'snapshot_id': 'xipl-foreign'}, {'snapshot_id': sid, 'product': 'VXvue'}):
        assert rpc(client, 'bellalun_search_documents', query='anything', **args)['isError']
    for doc in ('../manifest.json', 'C:/private.txt', 'VXvue.pdf'):
        assert rpc(client, 'bellalun_get_document', snapshot_id=sid, document_id=doc)['isError']
    for args in ({'offset': -1}, {'limit': 12001}, {'offset': True}):
        assert rpc(client, 'bellalun_get_workflow', snapshot_id=sid, **args)['isError']
    original = desktop.get_settings().root / 'data/shared_knowledge/bellalun/snapshots' / sid / 'original'
    (original / '(TC) Existing.md').write_text('TAMPERED', encoding='utf8')
    assert rpc(client, 'bellalun_search_documents', snapshot_id=sid, query='TAMPERED')['isError']
    client.headers['Authorization'] = 'Bearer wrong'
    assert client.post('/vxvue-desktop/rpc', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).status_code == 401


def test_bellalun_export_and_config(tmp_path):
    from scripts.sync_bellalun_shared import export_common_skill
    from app.core.product_config import load_product_config
    from app.core.product_knowledge import classify
    skill = tmp_path / 'skill'; (skill / 'references').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('# Skill\n[조사](references/investigation.md)', encoding='utf8')
    (skill / 'references/investigation.md').write_text('# 조사\nINVESTIGATION_UNIQUE\n지원 범위: `references/**/*.md`', encoding='utf8')
    (skill / 'references/bellalun-qa-guide.md').write_text('# 통합 QA\nQA_UNIQUE', encoding='utf8')
    target = tmp_path / 'knowledge'; target.mkdir()
    (target / 'unrelated.md').write_text('PRESERVE')
    result = export_common_skill(skill, target)
    content = Path(result['path']).read_bytes()
    assert b'INVESTIGATION_UNIQUE' not in content  # local-only references stay on the PC
    assert b'bellalun_get_workflow' in content
    assert export_common_skill(skill, target)['sha256'] == result['sha256']
    assert (target / 'Bellalun Viewer QA 지침.md').read_bytes() == (skill / 'references/bellalun-qa-guide.md').read_bytes()
    assert (target / 'unrelated.md').read_text() == 'PRESERVE'
    (skill / 'references/bellalun-qa-guide.md').write_text('')
    with pytest.raises(ValueError): export_common_skill(skill, target)
    assert Path(result['path']).read_bytes() == content
    cfg = load_product_config('bellalun-viewer')
    assert classify('Bellalun Viewer QA 지침.md', cfg.knowledge_source.classify) == 'qa_rules'
    assert classify('(사양서) Exposure.pdf', cfg.knowledge_source.classify) == 'specification'


def test_bellalun_rejects_modified_search_index(shared):
    client, _, _ = shared
    sid = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    from app.modules.bellalun_shared import service
    index = Path(service.snapshot(desktop.get_settings().root, sid)['index'])
    with sqlite3.connect(index) as db:
        db.execute("UPDATE chunks SET text='INDEX_TAMPERED'")
    assert rpc(client, 'bellalun_search_documents', snapshot_id=sid, query='INDEX_TAMPERED')['isError']


def test_bellalun_restart_rebuild_does_not_reuse_old_wal(shared):
    client, _, _ = shared
    sid = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    from app.modules.bellalun_shared import service
    index = Path(service.snapshot(desktop.get_settings().root, sid)['index'])
    code = "import sqlite3,os,sys; db=sqlite3.connect(sys.argv[1]); db.execute(\"UPDATE chunks SET text='WAL_TAMPERED'\"); db.commit(); os._exit(0)"
    subprocess.run([sys.executable, '-c', code, str(index)], check=True)
    assert Path(str(index) + '-wal').is_file()
    service.reset_cache()
    sid = value(rpc(client, 'bellalun_get_status'))['snapshot_id']
    source = value(rpc(client, 'bellalun_get_document', snapshot_id=sid, document_id='(TC) Existing.md'))
    assert 'WAL_TAMPERED' not in json.dumps(source)
    assert 'TC_UNIQUE' in json.dumps(source)


def test_sparse_workbook_preserves_real_values_comments_and_formulas(tmp_path):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from app.modules.vxvue_chatbot.skill.scripts import knowledge as k
    path = tmp_path / '(TC) Sparse.xlsx'
    book = Workbook(); sheet = book.active
    sheet['A1'] = 'TC_ID'; sheet['B1'] = 'Test Step'; sheet['A2'] = 'TC-1'; sheet['B2'] = 'Operate'
    sheet['C2'] = '=1+1'; sheet['D2'].comment = Comment('NOTE_ONLY', 'Synthetic')
    sheet['XFD1048576'].number_format = '0.00'; book.save(path)
    sheets = k.read_workbook(path, sparse=True)
    record = sheets[0]
    assert record['max_row'] == 2 and record['max_column'] == 4
    assert k.cell(record, 'B2')['text'] == 'Operate'
    assert k.cell(record, 'C2')['formula'] == '1+1'
    assert k.cell(record, 'D2')['comment'] == 'NOTE_ONLY'


def test_sparse_workbook_losslessly_compacts_id_only_runs(tmp_path):
    from openpyxl import Workbook
    from app.modules.vxvue_chatbot.skill.scripts import knowledge as k
    path = tmp_path / '(TC) IDs.xlsx'
    book = Workbook(); sheet = book.active
    sheet.append(['TC_ID', 'Test Step'])
    for row in range(2, 103): sheet.cell(row, 1, 'TC_SYN_' + str(row - 1))
    sheet.cell(103, 1, 'TC_SYN_102'); sheet.cell(103, 2, 'REAL_STEP')
    book.save(path)
    record = k.read_workbook(path, sparse=True)[0]
    seq = record['id_only_sequences'][0]
    assert (seq['first_row'], seq['last_row'], seq['first_value'], seq['last_value'], seq['prefix']) == (2, 102, 1, 101, 'TC_SYN_')
    assert len(record['cells']) < 10
    assert k.cell(record, 'B103')['text'] == 'REAL_STEP'


def test_sparse_workbook_keeps_rows_with_inherited_merged_steps(tmp_path):
    from openpyxl import Workbook
    from app.modules.vxvue_chatbot.skill.scripts import corpus, knowledge as k
    path = tmp_path / '(TC) Merged.xlsx'
    book = Workbook(); sheet = book.active
    sheet.append(['TC_ID', 'Test Step'])
    for row in range(2, 103): sheet.cell(row, 1, 'TC_SYN_' + str(row - 1))
    sheet['B2'] = 'SHARED_REAL_STEP'; sheet.merge_cells('B2:B102'); book.save(path)
    chunks, _ = corpus.xlsx_chunks(path, workbook_reader=lambda p: k.read_workbook(p, sparse=True))
    middle = next(c for c in chunks if c['location'].get('row') == 51)
    assert 'TC_SYN_50' in middle['text'] and 'SHARED_REAL_STEP' in middle['text']
    assert not any(c.get('details', {}).get('id_only_range') for c in chunks)


def test_bellalun_download_contains_portable_skill(shared):
    client, _, _ = shared
    response = client.get('/vxvue-desktop/bellalun-skill.zip')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ['bellalun-server-ai/SKILL.md']
        text = archive.read(archive.namelist()[0]).decode('utf8')
    assert 'bellalun_get_workflow' in text and 'C:/Users/' not in text
    guide = Path(__file__).parents[1] / 'app/web/static/skills/bellalun/index.html'
    text = guide.read_text(encoding='utf8')
    for word in ('Claude Desktop', 'Claude Code', 'Codex', 'bellalun_get_status', 'Redmine', '동기화'):
        assert word in text
    release = guide.parent / 'releases/2026-11-10.2/bellalun-server-ai-skill.zip'
    with zipfile.ZipFile(release) as archive, zipfile.ZipFile(io.BytesIO(response.content)) as dynamic:
        assert archive.namelist() == dynamic.namelist()
        assert all(archive.read(name) == dynamic.read(name) for name in archive.namelist())


def test_bellalun_export_ignores_adapter_checkout_line_endings(tmp_path, monkeypatch):
    """A CRLF checkout (core.autocrlf) must not change the exported server document."""
    import scripts.sync_bellalun_shared as sync
    skill = tmp_path / 'skill'; (skill / 'references').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('# Skill\n', encoding='utf8')
    (skill / 'references/investigation.md').write_text('# 조사\n', encoding='utf8')
    (skill / 'references/bellalun-qa-guide.md').write_text('# 통합 QA\nQA_UNIQUE', encoding='utf8')
    text = sync.ADAPTER.read_bytes().replace(b'\r\n', b'\n')
    digests = []
    for name, data in (('lf', text), ('crlf', text.replace(b'\n', b'\r\n'))):
        adapter = tmp_path / name / 'SKILL.md'; adapter.parent.mkdir()
        adapter.write_bytes(data)
        monkeypatch.setattr(sync, 'ADAPTER', adapter)
        target = tmp_path / f'knowledge-{name}'; target.mkdir()
        result = sync.export_common_skill(skill, target)
        assert b'\r\n' not in Path(result['path']).read_bytes()
        digests.append((result['sha256'], result['files']))
    assert digests[0] == digests[1]
