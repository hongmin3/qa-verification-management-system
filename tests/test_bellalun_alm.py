"""REQ-BELLALUNSHARED-007."""
from types import SimpleNamespace

import fitz
import httpx
import pytest

from app.modules.knowledge.srs_crawler.collector import CollectStats, fetch_raw_items
from app.modules.knowledge.srs_crawler.config import ProjectSpec
from app.modules.knowledge.srs_crawler.polarion_client import PolarionClient
from app.modules.knowledge.srs_crawler.publish import archive_and_publish
from app.modules.vxvue_chatbot import desktop
from app.modules.vxvue_chatbot.desktop_polarion import PolarionLookup


def test_livedoc_and_target_version_both_define_the_production_export():
    project = 'BellalunViewer'
    active = f'{project}/VP-1'
    dev = f'{project}/VP-2'
    outside = f'{project}/VP-3'

    def row(uid, target):
        return {'id': uid, 'relationships': {'targetVersion': {'data': {'id': f'{project}/{target}'}}}}

    client = SimpleNamespace(
        get_total_count=lambda project, query: 1 if 'targetVersion.KEY' in query else 3,
        iter_workitems=lambda *args, **kwargs: iter([
            row(active, 'Bellalun_1_0_12'), row(dev, 'Bellalun_2_0_0'), row(outside, 'unassigned'),
        ]),
        document_scope=lambda *args: ({active, dev}, {'id': 'synthetic-doc', 'status': 'draft'}),
    )
    spec = ProjectSpec(project, 'type:srs', document_space='Requirements', document_name='Synthetic',
                       target_version='Bellalun_1_0_12')
    stats = CollectStats(project)
    records = fetch_raw_items(client, spec, stats)
    assert [item['id'] for item in records] == [active]
    assert records[0]['_document_source']['status'] == 'draft'
    assert stats.expected_total == 1 and not stats.errors


def test_scoped_publication_keeps_legacy_and_other_version(tmp_path):
    folder = tmp_path / 'knowledge'
    folder.mkdir()

    def pdf(path):
        with fitz.open() as doc:
            doc.new_page().insert_text((20, 20), 'Synthetic SRS')
            doc.save(path)
        return path

    legacy = pdf(folder / '(사양서) OneNote.pdf')
    other = pdf(folder / '(사양서) 2.0.0(261119).pdf')
    old = pdf(folder / '(사양서) 1.0.12(261119).pdf')
    original = {path.name: path.read_bytes() for path in (legacy, other, old)}
    latest = pdf(tmp_path / '(사양서) 1.0.12(261120).pdf')
    result = archive_and_publish(knowledge_folder=folder, generated_pdfs=[latest],
                                 file_date='261120', replace_pattern='(사양서) 1.0.12(*).pdf')
    assert result.retained == [old.name]
    assert not old.exists()
    assert (folder / latest.name).read_bytes() == latest.read_bytes()
    assert {path.name: path.read_bytes() for path in (legacy, other)} == {
        path.name: original[path.name] for path in (legacy, other)
    }


def test_live_calls_are_project_scoped_and_results_cannot_cross_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, 'get_settings', lambda: SimpleNamespace(root=tmp_path))
    paths = []

    def lookup(project='VXvue'):
        def get(path, params):
            paths.append(path)
            return {'data': {'id': f'{project}/VP-1', 'attributes': {'title': project}}}
        client = SimpleNamespace(_get=get, config=SimpleNamespace(project_id=project))
        return PolarionLookup(tmp_path, client, project=project)

    monkeypatch.setattr(desktop, 'polarion_lookup', lookup)
    result = desktop.call('bellalun_get_polarion_item', {'vp_id': 'VP-1'})
    read = desktop.call('bellalun_read_polarion_result', {'result_id': result['result_id']})
    assert 'BellalunViewer/VP-1' in read['text']
    desktop.call('get_polarion_item', {'vp_id': 'VP-1'})
    assert paths == ['/projects/BellalunViewer/workitems/VP-1', '/projects/VXvue/workitems/VP-1']
    with pytest.raises(ValueError, match='만료'):
        desktop.call('read_polarion_result', {'result_id': result['result_id']})


def test_attachment_download_retries_a_disconnected_response(tmp_path, monkeypatch):
    monkeypatch.setattr('app.modules.knowledge.srs_crawler.polarion_client.time.sleep', lambda _: None)
    requests = []

    def response(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError('connection closed', request=request)
        return httpx.Response(200, content=b'\x89PNG\r\n\x1a\n')

    target = tmp_path / 'image.png'
    with PolarionClient('https://polarion.example', 'synthetic') as client:
        client.session.close()
        client.session = httpx.Client(transport=httpx.MockTransport(response))
        client.download_attachment('https://polarion.example/attachment', target)
    assert len(requests) == 2
    assert target.read_bytes() == b'\x89PNG\r\n\x1a\n'
    assert not target.with_suffix('.png.part').exists()
