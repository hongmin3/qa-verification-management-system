"""Validates: REQ-BELLALUNSHARED-006. Synthetic client files only."""
import json
from pathlib import Path
import zipfile

import pytest


def test_private_archive_excludes_credentials_and_restores_with_hash_checks(tmp_path):
    from scripts.package_qa_client import package
    from scripts.install_qa_client import install
    skill = tmp_path/'skill'; (skill/'scripts').mkdir(parents=True)
    (skill/'SKILL.md').write_text('SKILL_KEEP', encoding='utf8')
    (skill/'scripts/a.py').write_text('print(1)', encoding='utf8')
    (skill/'config').mkdir(); (skill/'config/redmine.json').write_text('DO_NOT_PACKAGE')
    alias = tmp_path/'alias'; alias.mkdir(); (alias/'SKILL.md').write_text('ALIAS')
    bridge = tmp_path/'bridge'; bridge.mkdir(); (bridge/'server.py').write_text('BRIDGE')
    guide = tmp_path/'guide.md'; guide.write_text('QA_GUIDE')
    archive = tmp_path/'private.zip'
    package(skill, alias, bridge, guide, archive)
    with zipfile.ZipFile(archive) as z:
        assert not any('config/' in n for n in z.namelist())
        assert b'DO_NOT_PACKAGE' not in b''.join(z.read(n) for n in z.namelist())
    home = tmp_path/'home'; knowledge = tmp_path/'knowledge'; knowledge.mkdir()
    result = install(archive, knowledge, home, 'python.exe')
    assert (home/'.codex/skills/bellalun-local-knowledge/SKILL.md').read_text() == 'SKILL_KEEP'
    config = json.loads((home/'.codex/skills/bellalun-local-knowledge/knowledge-config.json').read_text(encoding='utf8'))
    assert config['knowledge_root'] == str(knowledge.resolve())
    assert install(archive, knowledge, home, 'python.exe')['changed'] == []
    (home/'.codex/skills/bellalun-local-knowledge/SKILL.md').write_text('EXISTING_USER_EDIT')
    with pytest.raises(ValueError): install(archive, knowledge, home, 'python.exe')


def test_archive_tampering_is_rejected_before_any_install(tmp_path):
    from scripts.install_qa_client import install
    archive = tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('manifest.json',json.dumps({'schema':1,'files':{'../escape':'0'*64}}))
        z.writestr('../escape','BAD')
    with pytest.raises(ValueError): install(archive,tmp_path,tmp_path/'home','python')
    assert not (tmp_path/'home').exists()


def test_generated_connection_cannot_escape_via_existing_directory_link(tmp_path):
    import hashlib
    import os
    from scripts.install_qa_client import install
    archive = tmp_path/'client.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('manifest.json', json.dumps({'schema':1,'files':{'local-skill/SKILL.md':hashlib.sha256(b'OK').hexdigest()}}))
        z.writestr('local-skill/SKILL.md', b'OK')
    home = tmp_path/'home'; parent = home/'.local/share'; parent.mkdir(parents=True)
    outside = tmp_path/'outside'; outside.mkdir()
    link = parent/'qa-client-handoff'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError): install(archive, tmp_path, home, 'python')
        assert not (outside/'mcp-connection.json').exists()
        assert not (home/'.codex').exists()
    finally:
        if os.name == 'nt': os.rmdir(link)
        else: link.unlink()
