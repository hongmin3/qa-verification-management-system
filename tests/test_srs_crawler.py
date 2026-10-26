"""Validates: REQ-SYNC-003. 합성 문서로 배포 실패와 수집 경계를 검증한다."""
import importlib
import json
from pathlib import Path

import fitz
import pytest


def module(name):
    return importlib.import_module('app.modules.knowledge.srs_crawler.' + name)


def pdf(path, text='synthetic specification'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 50), text)
        doc.save(path)
    return path


def test_publish_preserves_other_documents_and_verifies_final_files(tmp_path):
    publish = module('publish')
    live = tmp_path / 'knowledge'
    old = pdf(live / '(사양서) Spec(261012).pdf', 'old')
    manual = pdf(live / '(매뉴얼) Manual.pdf', 'manual')
    before = manual.read_bytes()
    new = pdf(tmp_path / 'source' / '(사양서) Spec(261013).pdf')
    result = publish.archive_and_publish(knowledge_folder=live, generated_pdfs=[new], file_date='261013')
    assert result.published == [new.name]
    assert (live / new.name).read_bytes() == new.read_bytes()
    assert not old.exists()
    assert manual.read_bytes() == before
    assert list((tmp_path / 'ORG').rglob(old.name))


def test_invalid_pdf_never_replaces_previous_specification(tmp_path):
    publish = module('publish')
    old = pdf(tmp_path / 'knowledge' / '(사양서) Spec.pdf')
    before = old.read_bytes()
    bad = tmp_path / 'source.pdf'
    bad.write_bytes(b'not a PDF')
    with pytest.raises((ValueError, RuntimeError)):
        publish.archive_and_publish(knowledge_folder=old.parent, generated_pdfs=[bad], file_date='261013')
    assert old.read_bytes() == before


def test_failed_final_readback_rolls_back_same_name_overwrite(tmp_path, monkeypatch):
    publish = module('publish')
    old = pdf(tmp_path / 'knowledge' / '(사양서) Spec.pdf', 'old')
    before = old.read_bytes()
    new = pdf(tmp_path / 'source' / old.name, 'new')
    replace = Path.replace
    corrupted = []
    def corrupt_once(path, target):
        result = replace(path, target)
        if Path(target) == old and not corrupted:
            corrupted.append(True)
            old.write_bytes(b'corrupt final destination')
        return result
    monkeypatch.setattr(Path, 'replace', corrupt_once)
    with pytest.raises((ValueError, RuntimeError)):
        publish.archive_and_publish(knowledge_folder=old.parent, generated_pdfs=[new], file_date='261013')
    assert corrupted
    assert old.read_bytes() == before


def test_publish_does_not_use_restricted_tempfile_directory(tmp_path, monkeypatch):
    import tempfile
    publish = module('publish')
    def restricted(*args, **kwargs):
        raise AssertionError('tempfile directory grants only owner access')
    monkeypatch.setattr(tempfile, 'mkdtemp', restricted)
    new = pdf(tmp_path / 'source' / '(사양서) Spec.pdf')
    publish.archive_and_publish(knowledge_folder=tmp_path / 'knowledge', generated_pdfs=[new], file_date='261013')
    assert (tmp_path / 'knowledge' / new.name).read_bytes() == new.read_bytes()


def test_os_lock_rejects_overlap_and_allows_next_run(tmp_path):
    locks = module('run_lock')
    path = tmp_path / 'run.lock'
    with locks.RunLock(path):
        with pytest.raises(locks.LockHeldError):
            with locks.RunLock(path):
                pytest.fail('overlapping run acquired the lock')
    with locks.RunLock(path):
        pass


def test_native_sync_ignores_unfinished_newer_date(tmp_path):
    from app.modules.knowledge import vxvue_spec_sync as sync
    old = tmp_path / '2026-10-12'
    old.mkdir()
    old_pdf = pdf(old / 'pdf' / 'spec.pdf')
    record = module('publish').inspect_pdf(old_pdf)
    (old / 'manifest.json').write_text(json.dumps({'status': 'SUCCESS', 'published': True, 'files': [record]}))
    (tmp_path / '2026-10-13').mkdir()
    assert sync._latest_date_dir(tmp_path, require_complete=True) == old


def test_native_sync_rejects_pdf_that_differs_from_completed_manifest(tmp_path):
    from app.modules.knowledge import vxvue_spec_sync as sync
    folder = tmp_path / '2026-10-13'
    original = pdf(folder / 'pdf' / 'spec.pdf')
    record = module('publish').inspect_pdf(original)
    (folder / 'manifest.json').write_text(json.dumps({'status': 'SUCCESS', 'published': True, 'files': [record]}))
    original.write_bytes(b'incomplete replacement')
    assert sync._latest_date_dir(tmp_path, require_complete=True) is None


def test_publication_failure_restores_knowledge_before_returning(tmp_path):
    publish = module('publish')
    old = pdf(tmp_path / 'knowledge' / '(사양서) Spec.pdf', 'old')
    before = old.read_bytes()
    new = pdf(tmp_path / 'source' / old.name, 'new')
    def fail_completion(result):
        raise OSError('cannot publish complete output')
    with pytest.raises(OSError):
        publish.archive_and_publish(knowledge_folder=old.parent, generated_pdfs=[new],
                                    file_date='261013', finalize=fail_completion)
    assert old.read_bytes() == before


def test_config_uses_current_secrets_and_root_relative_outputs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    cfg = module('config')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/srs-crawler.yaml').write_text('projects:\n  - id: Demo\npartition:\n  vxvue_project_id: Demo\n  vxvue_groups:\n    - file_key: spec\n      display_name: Spec\n      modules: ["01"]\n')
    settings = SimpleNamespace(root=tmp_path, secrets=SimpleNamespace(polarion_host='https://alm.example', polarion_token='synthetic-token'), get=lambda key, default=None: default)
    monkeypatch.setattr(cfg, 'get_settings', lambda: settings)
    monkeypatch.setattr(cfg, 'load_product_config', lambda slug: SimpleNamespace(knowledge_source=SimpleNamespace(dir=str(tmp_path / 'knowledge'))))
    loaded = cfg.load_config()
    assert loaded.host == 'https://alm.example'
    assert loaded.base_dir == tmp_path / 'output/srs_crawler'
    assert loaded.snapshots_dir == tmp_path / 'data/srs_crawler/snapshots'
    assert loaded.knowledge_folder == tmp_path / 'knowledge'


def test_pdf_worker_executes_native_package(tmp_path):
    pytest.importorskip('playwright', reason='SRS 수집 PC의 선택 라이브러리')
    converter = module('pdf')
    html = tmp_path / 'simple.html'
    html.write_text('<h1>Integrated crawler smoke</h1>', encoding='utf-8')
    result = converter.html_to_pdf(html, tmp_path / 'simple.pdf', timeout_seconds=90)
    assert result.ok, result.error
    with fitz.open(result.path) as doc:
        assert 'Integrated crawler smoke' in doc[0].get_text()


def pipeline_fixture(tmp_path, monkeypatch, fail_second=False):
    pytest.importorskip('bs4', reason='SRS 수집 PC의 선택 라이브러리')
    from types import SimpleNamespace
    engine = module('pipeline')
    cfg = SimpleNamespace(base_dir=tmp_path/'output', snapshots_dir=tmp_path/'snapshots',
        logs_dir=tmp_path/'logs', archive_dir=tmp_path/'archive', knowledge_folder=tmp_path/'knowledge',
        filename_date_format='%y%m%d', filename_prefix='(사양서) ', host='https://alm.example', token='synthetic',
        verify_ssl=True, timeout_seconds=10, request_interval_seconds=0, page_size=100,
        projects=[SimpleNamespace(id='Demo'), SimpleNamespace(id='Other')],
        known_problem_srs=[], pdf_timeout_seconds=10, min_expected_srs_ratio=.95, require_all_pdfs=True,
        vxvue_project_id='Demo', vxvue_groups=[SimpleNamespace(file_key='spec', display_name='Spec', modules=['01'])],
        single_file_projects=[SimpleNamespace(project_id='Other', file_key='other', display_name='Other')])
    class Client:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def ping(self, project):
            if fail_second and project == 'Other': raise RuntimeError('synthetic network failure')
    roots = []
    def collect(client, project, config, image_root, stats):
        roots.append(image_root)
        stats.total_items = stats.expected_total = 1
        return [{'uid':project.id+'/D-1', 'id':'D-1', 'project_id':project.id, 'module_key':'01', 'title':'synthetic', 'content_html':'<p>synthetic</p>'}]
    def render(group, **kwargs):
        path = pdf(kwargs['pdf_path'])
        return module('pdf').PdfResult(path=path,size_bytes=path.stat().st_size,page_count=1,ok=True), []
    monkeypatch.setattr(engine, 'PolarionClient', Client)
    monkeypatch.setattr(engine, 'collect_project', collect)
    monkeypatch.setattr(engine, 'render_group_pdf_with_recovery', render)
    return engine, cfg, roots


def test_failed_second_project_keeps_first_collected_snapshot_for_diagnosis(tmp_path, monkeypatch):
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch, fail_second=True)
    with pytest.raises(RuntimeError): engine.run(cfg)
    partial = list((cfg.snapshots_dir / '.runs').rglob('D-1.json'))
    assert len(partial) == 1
    assert json.loads(partial[0].read_text(encoding='utf-8'))['project_id'] == 'Demo'
    failed = json.loads(next((cfg.base_dir/'.runs').rglob('manifest.json')).read_text(encoding='utf-8'))
    assert failed['stats'][0]['total_items'] == 1
    assert not list(cfg.base_dir.glob('????-??-??/manifest.json'))


def test_forced_recollection_gets_new_image_cache(tmp_path, monkeypatch):
    engine, cfg, roots = pipeline_fixture(tmp_path, monkeypatch)
    assert engine.run(cfg)['status'] == 'SUCCESS'
    assert engine.run(cfg, force=True)['status'] == 'SUCCESS'
    assert roots[0] != roots[2]


def test_no_publish_never_reuses_published_success(tmp_path, monkeypatch):
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch)
    prior = engine.run(cfg)
    before = {p.name:p.read_bytes() for p in cfg.knowledge_folder.iterdir()}
    preview = engine.run(cfg, publish=False)
    assert preview['status'] == 'PREVIEW'
    assert preview['published'] is False and preview['reused'] is False
    assert before == {p.name:p.read_bytes() for p in cfg.knowledge_folder.iterdir()}
    assert json.loads((cfg.base_dir/prior['date']/'manifest.json').read_text(encoding='utf-8'))['run_id'] == prior['run_id']


def test_completion_manifest_write_failure_is_recorded_failed(tmp_path, monkeypatch):
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch)
    prior = engine.run(cfg)
    write_json = engine._json
    def fail_public(path, value):
        if path.parent.name == 'public': raise OSError('synthetic completion write failure')
        return write_json(path, value)
    monkeypatch.setattr(engine, '_json', fail_public)
    with pytest.raises(OSError): engine.run(cfg, force=True)
    failed = [json.loads(p.read_text(encoding='utf-8')) for p in (cfg.base_dir/'.runs').glob('*/manifest.json')]
    assert sorted(r['status'] for r in failed) == ['FAILED', 'SUCCESS']
    assert all(not r['published'] for r in failed if r['status'] == 'FAILED')
    assert json.loads((cfg.base_dir/prior['date']/'manifest.json').read_text(encoding='utf-8'))['run_id'] == prior['run_id']


def test_failure_records_the_error_message_for_diagnosis(tmp_path, monkeypatch, caplog):
    """Validates: REQ-SYNC-003. 2026-10-21 실패는 'OSError' 만 남아 원인을 알 수 없었다."""
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch)
    engine.run(cfg)
    write_json = engine._json
    def fail_public(path, value):
        if path.parent.name == 'public': raise PermissionError(13, '다른 프로세스가 파일을 사용 중', str(path))
        return write_json(path, value)
    monkeypatch.setattr(engine, '_json', fail_public)
    with caplog.at_level('ERROR', logger='srs_automation'), pytest.raises(OSError):
        engine.run(cfg, force=True)
    failed = [json.loads(p.read_text(encoding='utf-8')) for p in (cfg.base_dir/'.runs').glob('*/manifest.json')]
    [record] = [r for r in failed if r['status'] == 'FAILED']
    assert record['error'] == 'PermissionError'
    assert '다른 프로세스가 파일을 사용 중' in record['error_detail'] and 'manifest.json' in record['error_detail']
    assert any('다른 프로세스가 파일을 사용 중' in r.getMessage() for r in caplog.records)


def test_failure_detail_hides_secret_looking_text(tmp_path, monkeypatch):
    """Validates: REQ-SYNC-003. 비밀 값이 들어 있을 수 있는 문구는 가린다."""
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch)
    engine.run(cfg)
    write_json = engine._json
    def fail_public(path, value):
        if path.parent.name == 'public': raise OSError('Authorization: Bearer abc123')
        return write_json(path, value)
    monkeypatch.setattr(engine, '_json', fail_public)
    with pytest.raises(OSError):
        engine.run(cfg, force=True)
    failed = [json.loads(p.read_text(encoding='utf-8')) for p in (cfg.base_dir/'.runs').glob('*/manifest.json')]
    [record] = [r for r in failed if r['status'] == 'FAILED']
    assert 'abc123' not in json.dumps(record, ensure_ascii=False)
    assert record['error_detail'].startswith('[가림')


def test_crawler_does_not_retry_invalid_comment_request(tmp_path, monkeypatch):
    import httpx
    import time
    transport = module('polarion_client')
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400)
    factory = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs))
    def single_get(url, **kwargs):
        kwargs.pop('verify', None)
        with factory(transport=httpx.MockTransport(handler)) as temporary:
            return temporary.get(url, **kwargs)
    monkeypatch.setattr(httpx, 'get', single_get)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    client = transport.PolarionClient('https://alm.example', 'synthetic', request_interval_seconds=0)
    with pytest.raises(transport.PolarionApiError): client.get_comments('Demo', 'D-1')
    assert len(calls) == 1


def test_staging_cleanup_failure_does_not_turn_completed_deployment_into_failure(tmp_path, monkeypatch):
    publish = module('publish')
    def locked(path): raise PermissionError('synthetic antivirus lock')
    monkeypatch.setattr(publish.shutil, 'rmtree', locked)
    new = pdf(tmp_path / 'source' / '(사양서) Spec.pdf')
    result = publish.archive_and_publish(knowledge_folder=tmp_path/'knowledge', generated_pdfs=[new], file_date='261013')
    assert result.published == [new.name]
    assert (tmp_path/'knowledge'/new.name).read_bytes() == new.read_bytes()


def test_failed_public_readback_restores_both_deployment_locations(tmp_path, monkeypatch):
    engine, cfg, _ = pipeline_fixture(tmp_path, monkeypatch)
    prior = engine.run(cfg)
    before = {p.name:p.read_bytes() for p in cfg.knowledge_folder.iterdir()}
    inspect = engine.inspect_pdf
    failed = []
    def unreadable(path):
        if path.parent.parent == cfg.base_dir/prior['date'] and not failed:
            failed.append(True)
            raise ValueError('synthetic final public readback failure')
        return inspect(path)
    monkeypatch.setattr(engine, 'inspect_pdf', unreadable)
    with pytest.raises(ValueError): engine.run(cfg, force=True)
    assert failed
    assert before == {p.name:p.read_bytes() for p in cfg.knowledge_folder.iterdir()}
    assert json.loads((cfg.base_dir/prior['date']/'manifest.json').read_text(encoding='utf-8'))['run_id'] == prior['run_id']


def test_collect_schedule_retries_a_failed_start():
    """Validates: REQ-SYNC-003. 2026-10-23 09:00 은 PC 재시작 중이라 시작하지 못했고 다음 실행이 사흘 뒤였다."""
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'install_srs_schedule.ps1').read_text(encoding='utf-8-sig')
    settings = next(line for line in script.splitlines() if line.strip().startswith('$settings = New-ScheduledTaskSettingsSet'))
    assert '-RestartCount 3' in settings
    assert '-RestartInterval (New-TimeSpan -Minutes 10)' in settings
    assert '-StartWhenAvailable' in settings and '-MultipleInstances IgnoreNew' in settings


def test_collect_schedule_runs_after_10am_and_syncs_right_after():
    """Validates: REQ-SYNC-003, REQ-SYNC-001. 담당자 PC 는 평일 10:00 에 켜진다(2026-10-26 사용자 결정).
    03:30·09:00 예약은 PC 가 꺼져 있어 늘 만회 실행에 기대었고, 10-26 에는 그 만회가 부팅 직후 로그온 실패로 빠졌다."""
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'install_srs_schedule.ps1').read_text(encoding='utf-8-sig')
    assert "-At '10:20'" in script and "-At '09:00'" not in script
    assert '-AtLogOn' in script and "Delay = 'PT15M'" in script
    assert '-Action @($collectAction, $syncAction)' in script
    assert 'Disable-ScheduledTask -TaskName $oldSync.TaskName' in script
    assert "-At '11:00'" in script and '-Trigger $almTrigger' in script
