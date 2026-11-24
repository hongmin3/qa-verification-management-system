"""QA 서버 장애 시 로컬 전환 (specs/qa-client-fallback.md).

장애는 127.0.0.1의 실제 HTTP 서버와 닫힌 포트로 만든다. 운영 서버는 건드리지 않는다.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'client/qa-server-fallback'
spec = importlib.util.spec_from_file_location('qa_fallback', SOURCE / 'scripts/qa_fallback.py')
fb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fb)

ENGINE = Path(os.environ.get('QA_FALLBACK_TEST_ENGINE',
                             Path.home() / '.codex/skills/bellalun-local-knowledge/scripts/knowledge.py'))
FAST = {'attempts': 3, 'timeout': 1.0, 'interval': 0.1, 'deadline': 10.0}


class Fake:
    """Routes: path -> (status, body bytes, delay seconds). Counts hits per path."""

    def __init__(self):
        self.routes, self.hits = {}, {}
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                fake.hits[self.path] = fake.hits.get(self.path, 0) + 1
                status, body, delay = fake.routes.get(self.path, (404, b'{}', 0))
                if delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def healthy(self):
        for name in ('alm', 'plm'):
            manifest = json.dumps({'release': f'{name}-r1', 'files': {}}).encode()
            self.routes[f'/static/{name}-guide/current.json'] = (200, json.dumps(
                {'release_path': 'r1', 'manifest_sha256': hashlib.sha256(manifest).hexdigest()}).encode(), 0)
            self.routes[f'/static/{name}-guide/r1/manifest.json'] = (200, manifest, 0)
        self.routes['/health'] = (200, b'{"status": "ok"}', 0)
        return self

    def all(self, status, delay=0):
        for path in list(self.routes):
            self.routes[path] = (status, self.routes[path][1], delay)


@pytest.fixture
def server():
    fake = Fake().healthy()
    yield fake
    fake.server.shutdown()


def closed_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{s.getsockname()[1]}'


def make_home(tmp_path):
    home = tmp_path / 'home'
    body = {
        'alm': '---\nname: alm\ndescription: x\n---\n# ALM\n',
        'plm': '---\nname: plm\ndescription: x\n---\n# PLM\n',
        'vxvue-ai': '---\nname: vxvue-ai\ndescription: x\n---\n<!-- vxvue-ai-managed: server -->\n# VXvue\n',
        'bellalun-server-ai': '---\nname: bellalun-server-ai\ndescription: x\n---\n# B\n',
    }
    for cli in ('.codex', '.claude'):
        for name, text in body.items():
            (home / cli / 'skills' / name).mkdir(parents=True)
            (home / cli / 'skills' / name / 'SKILL.md').write_bytes(text.encode())
    for name in ('bellalun-ai', 'bellalun-local-knowledge'):
        (home / '.codex/skills' / name).mkdir(parents=True)
        (home / '.codex/skills' / name / 'SKILL.md').write_bytes(f'---\nname: {name}\n---\n# local\n'.encode())
        fb.make_link(home / '.claude/skills' / name, home / '.codex/skills' / name)
    (home / '.claude/CLAUDE.md').write_bytes('# 사용자 지침\n\n- 기존 규칙\n'.encode())
    (home / '.codex/AGENTS.md').write_bytes('# 사용자 지침\n\n- 기존 규칙\n'.encode())
    return home


def installed(tmp_path, roots=None):
    home = make_home(tmp_path)
    knowledge = tmp_path / 'knowledge'
    knowledge.mkdir(exist_ok=True)
    result = fb.install(home, python=os.sys.executable, server_base='http://qa.invalid',
                        roots=roots or {'vxvue': knowledge, 'xipl': knowledge, 'bellalun': knowledge}, source=SOURCE)
    config = fb.load_config(home / '.codex/skills/qa-server-fallback/fallback-config.json')
    return home, config, result


# ---------------------------------------------------------------- probe

def test_healthy_server_keeps_server_first(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert {t['state'] for t in result['targets'].values()} == {'ok'}
    assert set(result['decision'].values()) == {'server'}
    assert fb.probe_exit(result) == 0
    assert all(t['attempts'] == 1 for t in result['targets'].values())


@pytest.mark.parametrize('status', [500, 502, 503, 504])
def test_5xx_is_outage_and_switches_to_local(tmp_path, server, status):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    server.all(status)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert set(result['decision'].values()) == {'local'}
    assert fb.probe_exit(result) == 10
    assert all(t['attempts'] == 3 for t in result['targets'].values())


def test_closed_port_is_outage_with_bounded_retries(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    result = fb.probe(config, FAST, closed_port(), repair=False)
    assert {t['state'] for t in result['targets'].values()} == {'outage'}
    assert all(t['attempts'] == 3 for t in result['targets'].values())


def test_slow_server_times_out_within_deadline(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001 — 응답 없는 서버를 무한정 기다리지 않는다."""
    home, config, _ = installed(tmp_path)
    server.all(200, delay=3)
    started = time.monotonic()
    result = fb.probe(config, {'attempts': 5, 'timeout': 0.5, 'interval': 0.1, 'deadline': 1.5}, server.base, repair=False)
    elapsed = time.monotonic() - started
    assert elapsed < 4
    assert {t['state'] for t in result['targets'].values()} == {'outage'}
    assert all(t['attempts'] < 5 for t in result['targets'].values())
    assert all(t['elapsed_s'] <= 2.0 for t in result['targets'].values())


def test_limits_reject_unbounded_settings(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    for bad in ({'attempts': 6}, {'timeout': 16}, {'deadline': 0}):
        with pytest.raises(ValueError):
            fb.probe(config, dict(FAST, **bad), 'http://127.0.0.1:1', repair=False)


def test_local_socket_permission_denial_is_not_server_outage():
    import errno
    import urllib.error
    denied = PermissionError(errno.EACCES, "socket access denied")
    assert fb.classify(denied) == 'auth_error'
    assert fb.classify(urllib.error.URLError(denied)) == 'auth_error'
    state = fb.probe_target('target', lambda _: (_ for _ in ()).throw(denied), FAST)
    assert state['state'] == 'auth_error' and state['attempts'] == 1


def test_fallback_install_resources_match_canonical_guard():
    resources = fb.guard_resources(SOURCE)
    common = SOURCE.parents[1] / 'deploy/qa-validation'
    assert set(resources) == {'scripts/qa_document_guard.py', 'references/legacy-validation.md'}
    assert all(content.encode('utf-8') == (common / rel).read_bytes() for rel, content in resources.items())


@pytest.mark.parametrize('status', [401, 403, 407])
def test_auth_errors_stop_without_retry_or_fallback(tmp_path, server, status):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    server.all(status)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert {t['state'] for t in result['targets'].values()} == {'auth_error'}
    assert set(result['decision'].values()) == {'stop'}
    assert fb.probe_exit(result) == 20
    assert all(t['attempts'] == 1 for t in result['targets'].values())


def test_hash_mismatch_is_integrity_error(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    server.routes['/static/alm-guide/r1/manifest.json'] = (200, b'{"release": "tampered"}', 0)
    server.routes['/static/plm-guide/current.json'] = (200, b'not json', 0)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert result['targets']['guide:alm']['state'] == 'integrity_error'
    assert result['targets']['guide:plm']['state'] == 'integrity_error'
    assert result['decision']['alm'] == 'stop' and result['decision']['plm'] == 'stop'
    assert result['decision']['vxvue'] == 'server'


def test_other_4xx_and_unhealthy_server_stop(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    server.routes['/static/alm-guide/current.json'] = (404, b'{}', 0)
    server.routes['/health'] = (200, b'{"status": "degraded"}', 0)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert result['targets']['guide:alm']['state'] == 'server_error'
    assert result['targets']['mcp']['state'] == 'server_error'
    assert result['decision']['bellalun'] == 'stop'


def test_recovery_returns_to_server_on_next_probe(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001 — 장애 상태를 저장해 두고 계속 쓰지 않는다."""
    home, config, _ = installed(tmp_path)
    server.all(503)
    assert set(fb.probe(config, FAST, server.base, repair=False)['decision'].values()) == {'local'}
    server.all(200)
    again = fb.probe(config, FAST, server.base, repair=False)
    assert set(again['decision'].values()) == {'server'}
    saved = json.loads((Path(config['state_dir']) / 'last-probe.json').read_text(encoding='utf-8'))
    assert saved['decision'] == again['decision']


def test_partial_outage_decides_per_product(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-001 — ALM 교육자료만 멈춰도 다른 제품은 서버를 쓴다."""
    home, config, _ = installed(tmp_path)
    server.routes['/static/alm-guide/current.json'] = (503, b'', 0)
    result = fb.probe(config, FAST, server.base, repair=False)
    assert result['decision'] == {'alm': 'local', 'plm': 'server', 'vxvue': 'server', 'xipl': 'server', 'bellalun': 'server'}


def test_cli_exit_codes(tmp_path, server, capsys):
    """Validates: REQ-QACLIENTFALLBACK-001"""
    home, config, _ = installed(tmp_path)
    cfg = str(home / '.codex/skills/qa-server-fallback/fallback-config.json')
    args = ['--config', cfg, 'probe', '--attempts', '2', '--timeout', '1', '--interval', '0.1', '--no-repair']
    assert fb.main(args + ['--server-base', server.base]) == 0
    assert fb.main(args + ['--server-base', closed_port()]) == 10
    out = capsys.readouterr().out
    assert '"local"' in out


# ---------------------------------------------------------------- install, overlays, identity

def test_install_is_repeatable_and_preserves_identity(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-004, REQ-QACLIENTFALLBACK-003"""
    home, config, first = installed(tmp_path)
    assert first['ok'], first['problems']
    assert first['backups'], '기존 Skill과 전역 지침을 바꾸기 전에 백업한다'
    assert all(Path(b).exists() for b in first['backups'])
    codex = home / '.codex/skills/qa-server-fallback'
    claude = home / '.claude/skills/qa-server-fallback'
    assert fb.is_link(claude) and claude.resolve() == codex.resolve()
    rendered = (codex / 'SKILL.md').read_text(encoding='utf-8')
    assert '{{' not in rendered and os.sys.executable.replace('\\', '/') in rendered
    for rel in ('scripts/qa_document_guard.py', 'references/legacy-validation.md'):
        assert (codex / rel).read_bytes() == (SOURCE.parents[1] / 'deploy/qa-validation' / rel).read_bytes()
    for cli in ('.codex', '.claude'):
        vx = (home / cli / 'skills/vxvue-ai/SKILL.md').read_bytes().decode()
        assert vx.startswith('---\nname: vxvue-ai\ndescription: x\n---\n<!-- vxvue-ai-managed: server -->\n# VXvue\n')
        assert vx.count('qa-server-fallback:begin') == 1 and 'qa-server-fallback/SKILL.md' in vx
    for name in ('bellalun-ai', 'bellalun-local-knowledge'):
        assert fb.is_link(home / '.claude/skills' / name)
        assert (home / '.codex/skills' / name / 'SKILL.md').read_bytes().decode().count('qa-server-fallback:begin') == 1
    for path in (home / '.claude/CLAUDE.md', home / '.codex/AGENTS.md'):
        text = path.read_bytes().decode()
        assert text.startswith('# 사용자 지침\n\n- 기존 규칙\n') and text.count('qa-server-fallback:begin') == 1
    assert (home / '.claude/CLAUDE.md').read_bytes() == (home / '.codex/AGENTS.md').read_bytes()
    second = fb.install(home, python=os.sys.executable, server_base='http://qa.invalid',
                        roots={'vxvue': tmp_path / 'knowledge', 'xipl': tmp_path / 'knowledge', 'bellalun': tmp_path / 'knowledge'}, source=SOURCE)
    assert second['ok'] and second['changed'] == [] and second['overlay_changes'] == [] and second['backups'] == []


def test_overlay_texts_cover_each_skill_scope():
    """Validates: REQ-QACLIENTFALLBACK-003"""
    spec = json.loads((SOURCE / 'overlays.json').read_text(encoding='utf-8'))
    assert set(spec['skills']) == {'alm', 'plm', 'vxvue-ai', 'bellalun-server-ai', 'bellalun-ai', 'bellalun-local-knowledge'}
    assert all('{{FALLBACK_SKILL}}' in text for text in spec['skills'].values())
    assert '개인 PAT' in spec['skills']['alm'] and '교육자료 미확인' in spec['skills']['alm']
    assert 'Teamcenter' in spec['skills']['plm']
    skill = (SOURCE / 'SKILL.md').read_text(encoding='utf-8')
    for phrase in ('XIPL.STUDIO', '인터넷 검색', 'auth_error', 'integrity_error', '같은 `snapshot`', '서버 우선으로 돌아간다', '추측하지 않는다'):
        assert phrase in skill


def test_server_resync_then_probe_repairs_overlay(tmp_path, server):
    """Validates: REQ-QACLIENTFALLBACK-004 — 서버 설치 명령이 블록 없는 원본으로 바꿔도 다음 확인에서 되살린다."""
    home, config, _ = installed(tmp_path)
    target = home / '.claude/skills/vxvue-ai/SKILL.md'
    target.write_bytes(b'---\nname: vxvue-ai\n---\n<!-- vxvue-ai-managed: v2 -->\n# new server text\n')
    assert not fb.check(config)['ok']
    result = fb.probe(config, FAST, server.base)
    repaired = [r for r in result['overlay_repair'] if r['path'] == str(target)]
    assert repaired and repaired[0]['status'] == 'added' and Path(repaired[0]['backup']).exists()
    assert target.read_bytes().decode().startswith('---\nname: vxvue-ai\n---\n<!-- vxvue-ai-managed: v2 -->\n# new server text\n')
    # 서버 갱신 내용은 Codex 쪽에 아직 없으므로 두 CLI 사본 차이는 보고된다(동기화가 맞춘다).
    report = fb.check(config)
    assert any('vxvue-ai' in p for p in report['problems'])


def test_install_refuses_to_replace_real_folder(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-004"""
    home = make_home(tmp_path)
    (home / '.claude/skills/qa-server-fallback').mkdir()
    (tmp_path / 'k').mkdir()
    with pytest.raises(RuntimeError, match='기존 폴더'):
        fb.install(home, python=os.sys.executable, roots={'vxvue': tmp_path / 'k', 'xipl': tmp_path / 'k', 'bellalun': tmp_path / 'k'}, source=SOURCE)


def test_split_overlay_matches_sync_format():
    """Validates: REQ-QACLIENTFALLBACK-005 — 설치 도구와 동기화가 같은 블록 경계를 쓴다."""
    from scripts import sync_server_skills as sync
    text = fb.with_overlay('server\n', 'body', '<!-- qa-server-fallback:begin -->', '<!-- qa-server-fallback:end -->')
    base, block = sync.split_overlay(text.encode())
    assert base == b'server\n' and fb.split_overlay(text, '<!-- qa-server-fallback:begin -->', '<!-- qa-server-fallback:end -->') == ('server\n', block.decode())


# ---------------------------------------------------------------- local evidence

needs_engine = pytest.mark.skipif(not ENGINE.is_file(), reason='Bellalun 검색 엔진(knowledge.py)이 이 PC에 없다')


def synthetic_docx(path, paragraphs):
    import zipfile
    body = ''.join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body>{body}</w:body></w:document>')


def local_config(tmp_path):
    vx = tmp_path / 'vx'
    (vx / '_이전판/2026-11-12').mkdir(parents=True)
    (vx / '[Skill 지침] VXvue.md').write_text('# 지침\n합성 규칙: Reject 확인\n', encoding='utf-8')
    (vx / '(사양서) VXvue 사양서1(261124).md').write_text('# 사양\nSRS-001 합성 Reject 버튼은 영상을 거부한다.\n', encoding='utf-8')
    (vx / '_이전판/2026-11-12/(사양서) VXvue 사양서1(261012).md').write_text('SRS-001 옛 판 Reject 문구\n', encoding='utf-8')
    xi = tmp_path / 'xipl'
    xi.mkdir()
    synthetic_docx(xi / 'XIPL User Manual.V3.6.0_KO.docx', ['합성 XIPL 가상 메모리 설정', '두 번째 문단'])
    (xi / 'SPEC.html').write_text('<p>다른 프로젝트 문서 가상 메모리</p>', encoding='utf-8')
    config = fb.default_config(tmp_path / 'home', os.sys.executable, 'http://qa.invalid', {'vxvue': vx, 'xipl': xi, 'bellalun': tmp_path / 'none'})
    config['engine'] = str(ENGINE)
    config['products']['vxvue']['required'] = ['[Skill 지침] VXvue.md', '(사양서) VXvue 사양서*', '(TC)*.xlsx']
    return config


@needs_engine
def test_local_search_uses_current_edition_only(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-002"""
    config = local_config(tmp_path)
    status = fb.local_status(config, 'vxvue')
    assert status['mode'] == 'local' and status['indexed'] == 2
    assert status['excluded_count'] == 1 and status['excluded'][0].startswith('_이전판/')
    assert status['missing'] == ['(TC)*.xlsx']
    assert any('자료일 2026-11-24' in f['version'] for f in status['files'])
    assert status['not_local'] and 'freshness_note' in status
    found = fb.local_command(config, 'vxvue', 'search', query=['Reject'])
    paths = {r['relative_path'] for r in found['results']}
    assert paths == {'(사양서) VXvue 사양서1(261124).md', '[Skill 지침] VXvue.md'}
    assert found['snapshot'] == status['snapshot']
    hit = next(r for r in found['results'] if r['relative_path'].startswith('(사양서)'))
    unit = fb.local_command(config, 'vxvue', 'get', id=hit['id'], neighbors=0)
    assert '영상을 거부한다' in json.dumps(unit, ensure_ascii=False)


@needs_engine
def test_xipl_indexes_only_the_manual_and_caches_are_separate(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-002"""
    config = local_config(tmp_path)
    status = fb.local_status(config, 'xipl')
    assert [f['file'] for f in status['files']] == ['XIPL User Manual.V3.6.0_KO.docx']
    assert 'V3.6.0_KO' not in status['files'][0]['version'] and 'V3.6.0' in status['files'][0]['version']
    assert status['missing'] == [] and 'XIPL.STUDIO 매뉴얼' in status['not_local']
    found = fb.local_command(config, 'xipl', 'search', query=['가상 메모리'])
    assert {r['relative_path'] for r in found['results']} == {'XIPL User Manual.V3.6.0_KO.docx'}
    fb.local_status(config, 'vxvue')
    caches = {Path(config['products'][p]['cache']) for p in ('vxvue', 'xipl')}
    assert len(caches) == 2 and all((c / 'corpus.json').is_file() for c in caches)
    vx_units = json.loads((Path(config['products']['vxvue']['cache']) / 'corpus.json').read_text(encoding='utf-8'))['units']
    assert not any('XIPL' in u['relative_path'] for u in vx_units)


@needs_engine
def test_missing_knowledge_folder_is_reported_not_hidden(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-002"""
    config = local_config(tmp_path)
    with pytest.raises(FileNotFoundError, match='bellalun 지식 폴더가 없습니다'):
        fb.local_status(config, 'bellalun')
    with pytest.raises(ValueError):
        fb.local_status(config, 'unknown')


@needs_engine
def test_snapshot_changes_when_source_changes(tmp_path):
    """Validates: REQ-QACLIENTFALLBACK-002 — 작업 도중 원문이 바뀌면 판 ID가 달라져 혼합을 알아챈다."""
    config = local_config(tmp_path)
    first = fb.local_status(config, 'vxvue')['snapshot']
    (tmp_path / 'vx/(사양서) VXvue 사양서1(261124).md').write_text('# 사양\nSRS-001 수정됨\n', encoding='utf-8')
    assert fb.local_status(config, 'vxvue')['snapshot'] != first
