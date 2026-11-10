"""Server-published skills are the reference; installed Claude/Codex copies follow them."""
import hashlib
import io
import json
import zipfile

import pytest

from scripts import sync_server_skills as sync

SERVER = 'http://qa.test'


def package(name, files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for path, data in files.items():
            archive.writestr(f'{name}/{path}', data)
    return buffer.getvalue()


def fake_server(packages, *, manifest_override=None):
    """Serve dynamic zips for vxvue/bellalun and the current.json → manifest → zip chain for alm/plm."""
    routes = {}
    for name, data in packages.items():
        if name in ('alm', 'plm'):
            release = '20261105-v1'
            manifest = json.dumps({'files': {f'{name}-skill.zip': {'sha256': hashlib.sha256(data).hexdigest()}}}).encode()
            routes[f'{SERVER}/static/{name}-guide/{release}/manifest.json'] = manifest_override or manifest
            routes[f'{SERVER}/static/{name}-guide/current.json'] = json.dumps(
                {'release_path': release, 'manifest_sha256': hashlib.sha256(manifest).hexdigest()}).encode()
            routes[f'{SERVER}/static/{name}-guide/{release}/{name}-skill.zip'] = data
        else:
            routes[SERVER + sync.DYNAMIC[name]] = data

    def fetch(url):
        if url not in routes:
            raise sync.SyncError(f'missing {url}')
        return routes[url]
    return fetch


def test_updates_stale_copies_in_every_root_with_backup(tmp_path):
    roots = [tmp_path / 'claude', tmp_path / 'codex']
    for root in roots:
        (root / 'vxvue-ai').mkdir(parents=True)
        (root / 'vxvue-ai/SKILL.md').write_text('old', encoding='utf-8')
        (root / 'vxvue-ai/local-note.md').write_text('keep', encoding='utf-8')
    fetch = fake_server({'vxvue-ai': package('vxvue-ai', {'SKILL.md': 'new'})})
    result = sync.sync(SERVER, roots, ['vxvue-ai'], fetch=fetch, backup_root=tmp_path / 'backups')
    assert result['status'] == 'UPDATED'
    for root in roots:
        assert (root / 'vxvue-ai/SKILL.md').read_text(encoding='utf-8') == 'new'
        assert (root / 'vxvue-ai/local-note.md').read_text(encoding='utf-8') == 'keep'
    backups = list((tmp_path / 'backups').rglob('SKILL.md'))
    assert len(backups) == 2 and all(path.read_text(encoding='utf-8') == 'old' for path in backups)


def test_identical_copies_are_left_untouched(tmp_path):
    root = tmp_path / 'claude'
    (root / 'alm').mkdir(parents=True)
    (root / 'alm/SKILL.md').write_bytes(b'same')
    fetch = fake_server({'alm': package('alm', {'SKILL.md': 'same'})})
    result = sync.sync(SERVER, [root], ['alm'], fetch=fetch, backup_root=tmp_path / 'backups')
    assert result['status'] == 'UNCHANGED'
    assert not (tmp_path / 'backups').exists()


def test_check_mode_reports_without_writing(tmp_path):
    root = tmp_path / 'codex'
    (root / 'plm').mkdir(parents=True)
    (root / 'plm/SKILL.md').write_text('old', encoding='utf-8')
    fetch = fake_server({'plm': package('plm', {'SKILL.md': 'new', 'references/a.md': 'A'})})
    result = sync.sync(SERVER, [root], ['plm'], fetch=fetch, backup_root=tmp_path / 'backups', check=True)
    assert result['status'] == 'OUTDATED'
    assert result['skills']['plm']['targets'][0]['changed'] == ['SKILL.md', 'references/a.md']
    assert (root / 'plm/SKILL.md').read_text(encoding='utf-8') == 'old'
    assert not (root / 'plm/references').exists()


def test_same_folder_through_two_roots_is_written_once(tmp_path):
    shared = tmp_path / 'codex'
    (shared / 'bellalun-server-ai').mkdir(parents=True)
    fetch = fake_server({'bellalun-server-ai': package('bellalun-server-ai', {'SKILL.md': 'new'})})
    result = sync.sync(SERVER, [shared, shared], ['bellalun-server-ai'], fetch=fetch, backup_root=tmp_path / 'b')
    assert len(result['skills']['bellalun-server-ai']['targets']) == 1


def test_manifest_hash_mismatch_refuses_before_any_write(tmp_path):
    root = tmp_path / 'claude'
    (root / 'alm').mkdir(parents=True)
    (root / 'alm/SKILL.md').write_text('old', encoding='utf-8')
    fetch = fake_server({'alm': package('alm', {'SKILL.md': 'new'})}, manifest_override=b'{"files": {}}')
    result = sync.sync(SERVER, [root], ['alm'], fetch=fetch, backup_root=tmp_path / 'backups')
    assert result['status'] == 'FAILED'
    assert (root / 'alm/SKILL.md').read_text(encoding='utf-8') == 'old'


@pytest.mark.parametrize('entry', ['../evil.md', 'other-skill/SKILL.md', '/abs.md'])
def test_package_paths_must_stay_inside_the_skill(tmp_path, entry):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('vxvue-ai/SKILL.md', 'ok')
        archive.writestr(entry, 'bad')
    fetch = fake_server({'vxvue-ai': buffer.getvalue()})
    result = sync.sync(SERVER, [tmp_path / 'claude'], ['vxvue-ai'], fetch=fetch, backup_root=tmp_path / 'b')
    assert result['status'] == 'FAILED'
    assert not (tmp_path / 'claude/vxvue-ai').exists()


def test_one_failed_skill_does_not_block_the_others(tmp_path):
    root = tmp_path / 'claude'
    fetch = fake_server({'plm': package('plm', {'SKILL.md': 'new'})})   # vxvue-ai is missing on the server
    result = sync.sync(SERVER, [root], ['vxvue-ai', 'plm'], fetch=fetch, backup_root=tmp_path / 'b')
    assert result['status'] == 'FAILED'
    assert result['skills']['vxvue-ai']['status'] == 'FAILED'
    assert (root / 'plm/SKILL.md').read_text(encoding='utf-8') == 'new'
