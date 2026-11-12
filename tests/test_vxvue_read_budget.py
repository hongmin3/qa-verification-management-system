"""Validates: REQ-VXVUECHATBOT-021 (응답 크기 상한·문단 범위 조회·동의어 확장·core 제외 읽기), REQ-VXVUECHATBOT-020."""
import json

import pytest

from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus


# ---- C1 응답 상한: 결과 건수만 줄이고 본문은 그대로 ------------------------------------

def test_budget_defers_results_without_shortening_evidence():
    from app.modules.vxvue_chatbot.desktop import fit_response_budget
    rows = [{'document_id': f'd{i}', 'chunk_id': f'd{i}::u', 'evidence': 'X' * 5000} for i in range(10)]
    value = {'results': rows, 'documents': [{'document_id': f'd{i}'} for i in range(10)],
             'returned': 10, 'truncated': False, 'next_offset': 10}
    out = fit_response_budget(value, offset=20, budget=16000)
    assert len(json.dumps(out, ensure_ascii=False)) <= 16000 + 400
    kept = len(out['results'])
    assert 1 <= kept < 10
    assert all(r['evidence'] == 'X' * 5000 for r in out['results'])
    assert out['truncated'] is True and out['next_offset'] == 20 + kept and out['returned'] == kept
    assert out['response_budget']['deferred_results'] == 10 - kept
    assert {d['document_id'] for d in out['documents']} == {r['document_id'] for r in out['results']}


def test_budget_keeps_single_oversized_result_whole():
    from app.modules.vxvue_chatbot.desktop import fit_response_budget
    value = {'results': [{'evidence': 'Y' * 50000}], 'truncated': False}
    out = fit_response_budget(value, budget=1000)
    assert out['results'][0]['evidence'] == 'Y' * 50000
    assert out['response_budget']['single_result_over_budget'] is True
    assert out['truncated'] is False


def test_small_response_is_unchanged():
    from app.modules.vxvue_chatbot.desktop import fit_response_budget
    value = {'results': [{'evidence': 'z'}]}
    assert fit_response_budget(value) is value


# ---- C2 offset은 결과 순번, 문단 번호는 paragraph_from/to --------------------------------

def test_get_document_paragraph_range_uses_source_paragraph_numbers(tmp_path):
    from docx import Document
    root = tmp_path / 'docs'; root.mkdir(); doc = Document()
    for i in range(12):
        doc.add_paragraph(f'P{i:02d} text' if i % 3 else '')  # 빈 문단은 구간이 되지 않아 순번과 문단 번호가 어긋난다
    doc.save(root / 'manual.docx')
    corpus = Corpus(root, tmp_path / 'index')
    by_offset = corpus.get_document('manual.docx', offset=2, limit=1)['results'][0]
    assert by_offset['location']['paragraph'] != 2
    ranged = corpus.get_document('manual.docx', paragraph_from=4, paragraph_to=8, limit=10)
    paragraphs = [r['location']['paragraph'] for r in ranged['results']]
    assert paragraphs and all(4 <= p <= 8 for p in paragraphs)
    assert ranged['matched'] == len(paragraphs)
    with pytest.raises(ValueError):
        corpus.get_document('manual.docx', paragraph_from=5, paragraph_to=4)


def test_tool_descriptions_state_offset_semantics():
    from app.modules.vxvue_chatbot.mcp_server import tools
    spec = {t['name']: t for t in tools()}
    assert '문단 번호가 아니다' in spec['get_document']['description']
    assert {'paragraph_from', 'paragraph_to'} <= set(spec['get_document']['inputSchema']['properties'])
    assert 'offset' in spec['find_testcases']['inputSchema']['properties']


def test_find_testcases_offset_continues(tmp_path):
    import openpyxl
    root = tmp_path / 'docs'; root.mkdir()
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(['TC ID', 'Title'])
    for i in range(8):
        ws.append([f'TC-{i}', f'Bucky restart case {i}'])
    wb.save(root / '(TC) sample.xlsx')
    corpus = Corpus(root, tmp_path / 'index')
    first = corpus.find_testcases('Bucky', limit=2)
    second = corpus.find_testcases('Bucky', limit=2, offset=first['next_offset'])
    assert {r['chunk_id'] for r in first['results']}.isdisjoint({r['chunk_id'] for r in second['results']})


# ---- C4 동의어 ------------------------------------------------------------------------

def test_synonym_variants_and_any_expansion():
    from app.core.qa_synonyms import expand_any, variants
    v = variants('버키 재실행')
    assert 'Bucky 재실행' in v and '버키 restart' in v and len(v) <= 4
    assert variants('Receptor') and 'Receptor' not in variants('Receptor')
    assert variants('관련 없는 검색어') == []
    q, added = expand_any('버키 유지')
    assert 'Bucky' in added and 'remember' in added and q.startswith('버키 유지')


def test_redmine_subject_search_merges_synonym_variants(monkeypatch):
    from app.modules.vxvue_chatbot.redmine import RedmineLookup
    lookup = object.__new__(RedmineLookup)
    calls = []

    def fake_get(path, params=None):
        calls.append(params.get('subject'))
        text = params['subject'][1:]
        rows = []
        if params['project_id'] == 61:
            if text == '버키 재실행':
                rows = [{'id': 1, 'subject': '버키 재실행 A'}]
            elif text == 'Bucky 재실행':
                rows = [{'id': 1, 'subject': '버키 재실행 A'}, {'id': 2, 'subject': 'Bucky 재실행 B'}]
        rows = [{**r, 'project': {'id': 61, 'name': 'I'}, 'status': {'id': 1, 'name': '신규'}} for r in rows]
        return {'issues': rows, 'total_count': len(rows), 'offset': 0, 'limit': params['limit']}
    monkeypatch.setattr(lookup, '_get', fake_get, raising=False)
    monkeypatch.setattr(lookup, '_authorize_issue', lambda row: None, raising=False)
    result = lookup.search('버키 재실행', page_size=20)
    ids = [i['id'] for i in result['items']]
    assert ids == [1, 2]
    assert result['items'][1]['matched_query'] == 'Bucky 재실행'
    assert 'matched_query' not in result['items'][0]
    assert result['synonym_expansion']['added'] == 1
    plain = lookup.search('버키 재실행', page_size=20, expand_synonyms=False)
    assert [i['id'] for i in plain['items']] == [1] and 'synonym_expansion' not in plain


# ---- C5 exclude_core ------------------------------------------------------------------

def rules():
    return ('# QA\n<!-- qa-section: id=core; title="공통" -->\nCORE_KEEP\n<!-- /qa-section: id=core -->\n'
            '<!-- qa-section: id=spec; title="사양"; requires=core -->\nSPEC_KEEP\n<!-- /qa-section: id=spec -->\n'
            '<!-- qa-section: id=issue; title="이슈"; requires=core,spec -->\nISSUE_KEEP\n<!-- /qa-section: id=issue -->\n')


def test_exclude_core_returns_task_and_non_core_prerequisites_only():
    from app.core.qa_workflow import select_sections
    text, meta = select_sections(rules(), 'issue', exclude_core=True)
    assert 'ISSUE_KEEP' in text and 'SPEC_KEEP' in text and 'CORE_KEEP' not in text and '# QA' not in text
    assert meta['selected_sections'] == ['spec', 'issue'] and meta['excluded_sections'] == ['core']
    full, _ = select_sections(rules(), 'issue')
    assert 'CORE_KEEP' in full
    for bad in ('core', 'full', None):
        with pytest.raises(ValueError):
            select_sections(rules(), bad, exclude_core=True)


def test_desktop_workflow_exclude_core_skips_connection_text():
    from app.modules.vxvue_chatbot.desktop import workflow
    current = {'id': 's', 'rules': rules(), 'skill_text': 'SKILL_TEXT', 'skill_metadata': {}}
    whole = workflow(current, {'section': 'issue'})
    lean = workflow(current, {'section': 'issue', 'exclude_core': True})
    assert 'SKILL_TEXT' in whole['text'] and 'SKILL_TEXT' not in lean['text']
    assert 'ISSUE_KEEP' in lean['text'] and lean['total_chars'] < whole['total_chars']
    with pytest.raises(ValueError):
        workflow(current, {'section': 'issue', 'exclude_core': 'yes'})


# ---- C3 검증 결과와 전송 payload 분리 -----------------------------------------------------

def test_protected_draft_reports_checks_passed_and_pc_payload_source():
    from app.modules.vxvue_chatbot import desktop
    from tests.test_vxvue_redmine_draft import BODY
    response = desktop.dispatch({'method': 'tools/call', 'params': {'name': 'validate_redmine_draft', 'arguments': dict(
        subject='(DK INNOGEN) [에뮬레이터연동] 현상', description=BODY, project_id=61, tracker_id=7, priority_id=2)}})
    result = json.loads(response['content'][0]['text'])
    assert result['ok'] is False and result['checks_passed'] is True and result['payload_source'] == 'pc'
    assert 'payload' not in result and 'draft_sha256' not in result


def test_pc_rules_file_is_standalone_and_matches_server_hash():
    import importlib.util
    from pathlib import Path
    from app.modules.vxvue_chatbot.redmine_draft import validate
    from tests.test_vxvue_redmine_draft import BODY
    path = Path('app/modules/vxvue_chatbot/redmine_draft_rules.py')
    spec = importlib.util.spec_from_file_location('standalone_rules', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    args = dict(subject='(DK INNOGEN) [에뮬레이터연동] 현상', description=BODY, project_id=61, tracker_id=7, priority_id=2,
                custom_fields=[{'id': 54, 'value': '2767'}])
    assert module.validate(**args)['draft_sha256'] == validate(**args)['draft_sha256']
    assert 'import' not in ''.join(l for l in path.read_text(encoding='utf-8').splitlines() if l.startswith('from .'))
