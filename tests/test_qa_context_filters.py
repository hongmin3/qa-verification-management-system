"""Validates: REQ-VXVUECHATBOT-019, REQ-BELLALUNSHARED-005."""
import json
from types import SimpleNamespace

import pytest


def test_polarion_compact_keeps_relations_links_and_strikethrough(tmp_path):
    from app.modules.vxvue_chatbot.desktop_polarion import PolarionLookup
    class Client:
        config = SimpleNamespace(project_id='VXvue')
        def _get(self, path, params):
            return {'data': {'id': 'VP-1', 'attributes': {'description': {
                'type': 'text/html', 'value': '<p style="font-family: Arial">Keep <s>OLD</s> CURRENT</p>'}},
                'relationships': {'linkedWorkItems': {'links': {'related': '/related'}}}}}
    lookup = PolarionLookup(tmp_path, Client())
    compact = lookup.item('VP-1', compact=True)
    payload = json.loads(compact['text'])
    item = payload['response']['data']
    assert 'style=' not in item['attributes']['description']['value']
    assert '<s>OLD</s>' in item['attributes']['description']['value']
    assert item['relationships']['linkedWorkItems']['links']['related'] == '/related'
    full = lookup.read(compact['result_id'], compact=False)
    assert 'style=' in full['text'] and compact['view'] == 'compact'


def test_xlsx_column_filter_retains_headers_and_validity(tmp_path):
    from openpyxl import Workbook
    from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
    root = tmp_path / 'source'; root.mkdir()
    path = root / 'TC.xlsx'
    book = Workbook(); sheet = book.active
    sheet.append(['TC_ID', 'Test Step', 'Expected', 'Result'])
    sheet.append(['TC-1', 'OPERATE', 'EXPECT', 'OLD_PASS'])
    book.save(path)
    corpus = Corpus(root, tmp_path / 'index.sqlite3')
    full = corpus.get_document('TC.xlsx', section='sheet:Sheet:row:2')
    result = corpus.get_document('TC.xlsx', section='sheet:Sheet:row:2', columns=['B', 'C'])
    row = result['results'][0]
    assert 'OPERATE' in row['evidence'] and 'EXPECT' in row['evidence'] and 'OLD_PASS' not in row['evidence']
    assert set(row['details']['cells']) == {'B2', 'C2'}
    assert set(row['details']['headers']) == {'B', 'C'}
    assert row['location']['cells'] == ['B2', 'C2']
    assert result['selection']['omitted_columns'] == ['A', 'D']
    assert 'OLD_PASS' in full['results'][0]['evidence']
    with pytest.raises(ValueError): corpus.get_document('TC.xlsx', columns=['B2'])


def test_changed_only_manual_keeps_original_numbers_and_all_revision_kinds(tmp_path):
    from app.modules.manual_review.shared_review import read_review
    rows = [{'number': 1, 'text': 'UNCHANGED', 'inserted': [], 'deleted': []},
            {'number': 2, 'text': 'NEW', 'inserted': ['NEW'], 'deleted': ['OLD']},
            {'number': 3, 'text': 'STRIKE', 'inserted': [], 'deleted': [], 'has_strike': True},
            {'number': 4, 'text': 'COMMENT', 'inserted': [], 'deleted': [], 'comment_ids': ['1']}]
    class Store:
        def inventory(self, sid): return {'sha256': 'abc', 'paragraphs': rows, 'comments': []}
    result = read_review(Store(), 'id', view='paragraphs', changed_only=True, limit=1)
    assert result['total'] == 3 and result['next_offset'] == 1
    assert result['results'][0]['number'] == 2
    assert result['selection']['omitted'] == 1
    assert len(read_review(Store(), 'id', view='paragraphs')['results']) == 4


def test_bellalun_server_export_excludes_local_references(tmp_path):
    from scripts.sync_bellalun_shared import export_common_skill
    skill = tmp_path / 'skill'; (skill / 'references').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('LOCAL_INSTALL_COMMAND', encoding='utf8')
    (skill / 'references/bellalun-qa-guide.md').write_text('QA_KEEP', encoding='utf8')
    (skill / 'references/session-review.md').write_text('REMOVED_REVIEW', encoding='utf8')
    knowledge = tmp_path / 'knowledge'; knowledge.mkdir()
    result = export_common_skill(skill, knowledge)
    text = (knowledge / '[Skill 지침] Bellalun Viewer.md').read_text(encoding='utf8')
    assert 'LOCAL_INSTALL_COMMAND' not in text and 'REMOVED_REVIEW' not in text
    assert 'bellalun_get_workflow' in text
    assert (skill / 'references/session-review.md').is_file()
    assert (knowledge / 'Bellalun Viewer QA 지침.md').read_text(encoding='utf8') == 'QA_KEEP'
