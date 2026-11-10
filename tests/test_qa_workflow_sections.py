"""Validates: REQ-VXVUECHATBOT-019, REQ-BELLALUNSHARED-005."""
import json

import pytest


def rules():
    return ('# QA\n'
            '<!-- qa-section: id=core; title="공통" -->\nCORE_KEEP\n<!-- /qa-section: id=core -->\n'
            '<!-- qa-section: id=spec; title="사양"; requires=core -->\nSPEC_KEEP\n<!-- /qa-section: id=spec -->\n'
            '<!-- qa-section: id=tc; title="TC"; requires=core,spec -->\nTC_KEEP\n<!-- /qa-section: id=tc -->\n'
            '<!-- qa-section: id=mail; title="검증 메일"; requires=core -->\nMAIL_KEEP\n<!-- /qa-section: id=mail -->\n'
            '<!-- qa-section: id=revlog; title="이력" -->\nHISTORY\n<!-- /qa-section: id=revlog -->\n')


def test_select_dependencies_inventory_and_legacy():
    from app.core.qa_workflow import select_sections
    text, meta = select_sections(rules(), 'tc')
    assert all(t in text for t in ('CORE_KEEP', 'SPEC_KEEP', 'TC_KEEP'))
    assert 'MAIL_KEEP' not in text and 'HISTORY' not in text
    assert meta['selected_sections'] == ['core', 'spec', 'tc']
    assert 'mail' in {s['id'] for s in meta['sections']}
    assert select_sections(rules(), None)[0] == rules()
    assert select_sections('old whole guide', 'tc')[0] == 'old whole guide'
    assert select_sections('old whole guide', 'tc')[1]['legacy_fallback'] is True


@pytest.mark.parametrize('bad', [
    '<!-- qa-section: id=core -->\nX',
    '<!-- qa-section: id=core -->\nX\n<!-- /qa-section: id=tc -->',
    '<!-- qa-section: id=bogus -->\nX\n<!-- /qa-section: id=bogus -->',
    '<!-- qa-section: id=tc; requires=missing -->\nX\n<!-- /qa-section: id=tc -->',
    '<!-- qa-section: id=core; requires=tc -->\nX\n<!-- /qa-section: id=core -->\n'
    '<!-- qa-section: id=tc; requires=core -->\nY\n<!-- /qa-section: id=tc -->',
])
def test_invalid_sections_never_silently_omit_rules(bad):
    from app.core.qa_workflow import select_sections
    with pytest.raises(ValueError):
        select_sections(bad, 'core')


def test_vxvue_real_workflow_selects_before_protecting_and_paginates():
    from app.modules.vxvue_chatbot.desktop import workflow
    current = {'id': 's', 'skill_text': 'ADAPTER', 'rules': rules(), 'skill_metadata': None}
    whole = workflow(current, {'section': 'mail'})
    assert 'MAIL_KEEP' in whole['text'] and 'TC_KEEP' not in whole['text']
    parts, offset = [], 0
    while True:
        part = workflow(current, {'section': 'mail', 'offset': offset, 'limit': 137})
        parts.append(part['text'])
        if not part['truncated']:
            break
        offset = part['next_offset']
    assert ''.join(parts) == whole['text']
    assert whole['selected_sections'] == ['core', 'mail']


def test_compaction_moves_repeated_metadata_without_changing_evidence():
    from app.core.qa_workflow import compact_evidence
    doc = {'document_id': 'x.xlsx', 'document_name': 'x.xlsx', 'sha256': 'abc',
           'version_mentions': ['1.2.3'] * 100, 'chunk_count': 200, 'warnings': ['partial']}
    original = {'results': [{**doc, 'evidence': 'EXACT', 'location': {'cell': f'A{n}'},
                            'details': {'strike': True}, 'evidence_truncated': False} for n in range(4)],
                'truncated': True, 'next_offset': 4, 'scope': {'errors': ['READ_FAILED']}}
    compact = compact_evidence(original)
    assert len(json.dumps(compact)) < len(json.dumps(original))
    assert compact['documents'][0]['version_mentions'] == doc['version_mentions']
    for a, b in zip(original['results'], compact['results']):
        assert {**compact['documents'][0], **b} == a
    assert compact['scope'] == original['scope'] and compact['next_offset'] == 4


def test_bellalun_workflow_schema_and_no_repeated_instructions():
    from app.modules.bellalun_shared.service import definitions
    schema = next(t for t in definitions() if t['name'] == 'bellalun_get_workflow')['inputSchema']
    assert 'section' in schema['properties']


def test_guide_lint_warns_on_size_and_rejects_new_revision_appendix():
    from app.core.qa_workflow import validate_guide, preservation
    before = rules()
    after = before.replace('CORE_KEEP', 'CORE_KEEP\n' + 'X' * 26000)
    result = validate_guide(after, 'Bellalun', baseline=before)
    assert result['warnings'] and not result['errors']
    bad = after.replace('CORE_KEEP', 'CORE_KEEP\n## Rev.9.0 강화 사항')
    assert validate_guide(bad, 'Bellalun', baseline=before)['errors']
    assert preservation(before, before.replace('TC_KEEP', ''))['missing'] == ['TC_KEEP']
    assert not preservation(before, after)['missing']


def test_sync_validation_blocks_deleted_rules_and_malformed_dependencies():
    from app.core.qa_workflow import validate_guide, select_sections
    assert validate_guide(rules().replace('TC_KEEP', ''), 'VXvue', rules())['errors']
    for malformed in ('requires=CORE', 'requires=core,', 'requires=core typo', 'require=core'):
        with pytest.raises(ValueError):
            select_sections(rules().replace('requires=core,spec', malformed), 'tc')
    old = rules().replace('TC_KEEP', 'TC_KEEP\n## Rev.1.0 강화 사항')
    assert not validate_guide(old.replace('\n', '\r\n'), 'VXvue', old)['errors']


def test_inventory_compaction_preserves_errors_hashes_and_marks_omitted_version_mentions():
    from app.core.qa_workflow import compact_inventory
    source = {'scope': {'errors': ['READ_FAILED'], 'documents': [
        {'document_id': 'x', 'sha256': 'abc', 'warnings': ['partial'], 'version_mentions': ['1.2.3'] * 100}]}}
    result = compact_inventory(source)
    doc = result['scope']['documents'][0]
    assert doc['sha256'] == 'abc' and doc['warnings'] == ['partial']
    assert doc['version_mentions_count'] == 100 and 'version_mentions' not in doc
    assert result['scope']['errors'] == source['scope']['errors']
    assert result['metadata_view'] == 'get_status(compact=false)'
    assert source['scope']['documents'][0]['version_mentions'] == ['1.2.3'] * 100


def test_shared_status_tool_honors_its_compact_schema(tmp_path):
    from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools
    root = tmp_path/'source'; root.mkdir()
    (root/'manual.md').write_text('# Version\n1.2.3 2.3.4', encoding='utf8')
    session = KnowledgeTools({'root':str(root),'index':str(tmp_path/'index.sqlite3'),'id':'s','documents':[]}, [], tmp_path/'trace')
    full = session.call('get_status', {'compact':False})
    compact = session.call('get_status', {'compact':True})
    assert len(full['scope']['documents'][0]['version_mentions']) == 2
    assert compact['scope']['documents'][0]['version_mentions_count'] == 2
