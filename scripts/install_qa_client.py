"""Verify and restore the private QA client archive; never overwrite divergent files."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile


def install(archive, knowledge_root, home=None, python=None):
    home = Path(home or Path.home()).resolve()
    knowledge_root = Path(knowledge_root).resolve()
    if not knowledge_root.is_dir():
        raise ValueError('기존 Bellalun 지식 폴더를 지정하세요.')
    python = python or sys.executable
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) > 2000 or len(set(n.casefold() for n in names)) != len(names) or sum(i.file_size for i in bundle.infolist()) > 64 * 1024 * 1024:
            raise ValueError('자료 묶음의 크기·중복 이름을 확인하세요.')
        manifest = json.loads(bundle.read('manifest.json'))
        if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict) or set(names) != {'manifest.json', *manifest['files']}:
            raise ValueError('자료 묶음 목록이 맞지 않습니다.')
        targets = {}
        roots = {'local-skill': '.codex/skills/bellalun-local-knowledge',
                 'alias': '.codex/skills/bellalun-ai', 'bridge': '.local/share/bellalun-ai',
                 'guides': '.local/share/qa-client-handoff/guides'}
        old_home = manifest.get('source_home', '')
        for name, expected in manifest['files'].items():
            rel = PurePosixPath(name)
            if '\\' in name or ':' in name or rel.is_absolute() or '..' in rel.parts or len(rel.parts) < 2 or rel.parts[0] not in roots:
                raise ValueError('자료 묶음 경로가 설치 범위를 벗어납니다.')
            if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                raise ValueError('자료 묶음 해시 형식이 맞지 않습니다.')
            data = bundle.read(name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('자료 묶음 파일 해시가 다릅니다.')
            if old_home and name.endswith('.md'):
                text = data.decode('utf-8-sig')
                old_knowledge = manifest.get('source_knowledge_root', '')
                if old_knowledge:
                    text = text.replace(old_knowledge.replace('\\', '/'), str(knowledge_root).replace('\\', '/')).replace(old_knowledge, str(knowledge_root))
                text = text.replace(old_home.replace('\\', '/'), str(home).replace('\\', '/')).replace(old_home, str(home))
                data = text.encode('utf-8')
            target = home / roots[rel.parts[0]] / Path(*rel.parts[1:])
            if not target.resolve().is_relative_to(home):
                raise ValueError('설치 대상 연결이 사용자 폴더 밖을 가리킵니다.')
            targets[target] = data
    skill = home / roots['local-skill']
    targets[skill/'knowledge-config.json'] = json.dumps({'knowledge_root': str(knowledge_root), 'python_executable': python}, ensure_ascii=False, indent=2).encode('utf8')
    connection = {'mcpServers': {'bellalun-ai': {'command': python, 'args': ['-X', 'utf8', str(home / roots['bridge'] / 'server.py')]}}}
    targets[home/'.local/share/qa-client-handoff/mcp-connection.json'] = json.dumps(connection, ensure_ascii=False, indent=2).encode('utf8')
    for path, data in targets.items():
        if path.is_symlink() or not path.resolve().is_relative_to(home):
            raise ValueError('설치 대상 연결이 사용자 폴더 밖을 가리킵니다.')
        if path.exists() and path.read_bytes() != data:
            raise ValueError('기존 파일이 다릅니다. 비교 후 병합하세요: ' + str(path))
    changed = []
    for path, data in targets.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); changed.append(str(path))
        if path.read_bytes() != data:
            raise ValueError('설치 후 파일 재조회가 다릅니다.')
    return {'verified': len(manifest['files']), 'changed': changed, 'skill': str(skill),
            'connection': str(home/'.local/share/qa-client-handoff/mcp-connection.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--knowledge-root', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(install(args.archive, args.knowledge_root), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
