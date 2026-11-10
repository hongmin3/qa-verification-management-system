"""Bellalun live Redmine tools share the VXvue reader with a Bellalun project allow-list."""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.modules.vxvue_chatbot import redmine


def cfg():
    return SimpleNamespace(secrets=SimpleNamespace(redmine_host='https://redmine.example.test', redmine_api_key='test-key'))


def issue(i, project=75, **extra):
    return {'id': i, 'project': {'id': project, 'name': 'QA Defect'}, 'status': {'id': 5, 'name': '완료'},
            'subject': f'이슈 {i}', 'description': '', 'journals': [], 'custom_fields': [], **extra}


def test_fulltext_keeps_closed_hits_and_reports_custom_field_match():
    seen = []

    def transport(request):
        seen.append(request)
        if request.url.path == '/projects/75/search.json':
            return httpx.Response(200, json={'results': [{'id': 22225, 'type': 'issue-closed'}, {'id': 16509, 'type': 'issue'}],
                                             'total_count': 2, 'offset': 0, 'limit': 50})
        if request.url.path == '/issues/22225.json':
            return httpx.Response(200, json={'issue': issue(22225, custom_fields=[
                {'id': 76, 'name': '원인/분석', 'value': '생산 공정 메뉴얼 75p Network 설정'}])})
        return httpx.Response(200, json={'issue': issue(16509, journals=[{'notes': 'COM 포트 미검출'}])})

    result = redmine.BellalunRedmineLookup(cfg(), transport=httpx.MockTransport(transport)).search('메뉴얼', full_text=True)
    assert [i['id'] for i in result['items']] == [22225]
    assert result['items'][0]['matched_in'] == ['원인/분석']
    assert not result['errors'] and result['project_ids'] == [75]
    params = seen[0].url.params
    assert 'open_issues' not in params and 'titles_only' not in params


def test_bellalun_scope_rejects_vxvue_projects_and_issues():
    lookup = redmine.BellalunRedmineLookup(cfg(), transport=httpx.MockTransport(lambda r: pytest.fail('request sent')))
    with pytest.raises(ValueError, match='75, 86, 35, 37'):
        lookup.search('x', project_ids=[61])
    vx = redmine.BellalunRedmineLookup(cfg(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'issue': issue(5, project=61)})))
    with pytest.raises(ValueError, match='Bellalun'):
        vx.issue(5)
    bl = redmine.RedmineLookup(cfg(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'issue': issue(5)})))
    with pytest.raises(ValueError, match='VXvue'):
        bl.issue(5)


def test_bellalun_detail_is_stored_apart_from_vxvue_results(tmp_path):
    lookup = redmine.BellalunRedmineLookup(cfg(), root=tmp_path, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'issue': issue(7, journals=[{'id': 1, 'notes': 'public'}, {'id': 2, 'private_notes': True, 'notes': 'secret'}])})))
    result = lookup.issue(7)
    assert (tmp_path / 'data/bellalun_shared/redmine' / f"{result['result_id']}.json").exists()
    assert not (tmp_path / 'data/vxvue_desktop/redmine').exists()
    assert 'secret' not in result['text']
    vx_reader = object.__new__(redmine.RedmineLookup)
    vx_reader.root = tmp_path / redmine.RedmineLookup.SUBDIR
    with pytest.raises(ValueError):
        vx_reader.read(result['result_id'])


def test_bellalun_tools_are_listed_and_routed_without_snapshot(monkeypatch):
    from app.modules.bellalun_shared import service
    names = {t['name'] for t in service.definitions()}
    assert {'bellalun_get_redmine_issue', 'bellalun_search_redmine_issues', 'bellalun_read_redmine_result'} <= names
    schema = next(t for t in service.definitions() if t['name'] == 'bellalun_search_redmine_issues')['inputSchema']
    assert schema['properties']['project_ids']['items']['enum'] == [75, 86, 35, 37]
    assert 'snapshot_id' not in schema['properties']
    calls = []
    monkeypatch.setattr(redmine, 'bellalun_call', lambda name, args: calls.append((name, args)) or {'ok': True})
    monkeypatch.setattr(service, 'snapshot', lambda *a, **k: pytest.fail('Redmine must not need a document snapshot'))
    assert service.call('bellalun_search_redmine_issues', {'keywords': '포트', 'full_text': True}, None) == {'ok': True}
    assert calls == [('bellalun_search_redmine_issues', {'keywords': '포트', 'full_text': True})]
    with pytest.raises(ValueError):
        service.call('bellalun_get_redmine_issue', {'issue_id': 1, 'project_ids': [61]}, None)


def test_bellalun_call_strips_prefix_and_uses_bellalun_scope(monkeypatch):
    used = []
    monkeypatch.setattr(redmine, 'lookup', lambda cls=None: used.append(cls) or SimpleNamespace(search=lambda **kw: kw))
    assert redmine.bellalun_call('bellalun_search_redmine_issues', {'keywords': 'x'}) == {'keywords': 'x'}
    assert used == [redmine.BellalunRedmineLookup]


def test_vxvue_definitions_are_unchanged_by_product_split():
    names = [t['name'] for t in redmine.definitions()]
    assert names == ['get_redmine_issue', 'search_redmine_issues', 'read_redmine_result']
    schema = redmine.definitions()[1]['inputSchema']
    assert schema['properties']['project_ids']['items']['enum'] == [61, 29, 107, 33, 41]
    assert json.dumps(redmine.definitions(), ensure_ascii=False).count('Bellalun') == 0
