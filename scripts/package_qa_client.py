"""Build a private, credential-free QA client archive for a clean PC."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def package(skill_root, alias_root, bridge_root, vxvue_guide, output):
    sources = {}
    for prefix, root in [('local-skill', Path(skill_root)), ('alias', Path(alias_root)), ('bridge', Path(bridge_root))]:
        if not root.is_dir():
            raise ValueError('필요한 클라이언트 원본 폴더가 없습니다.')
        for path in sorted(root.rglob('*')):
            rel = path.relative_to(root)
            if not path.is_file() or any(p in ('config', '.cache', '__pycache__', 'backups') for p in rel.parts):
                continue
            allowed = (rel.as_posix() == 'SKILL.md' or
                       prefix == 'local-skill' and rel.parts[0] in ('references', 'scripts', 'tests', 'agents') and path.suffix in ('.md', '.py', '.yaml') or
                       prefix == 'bridge' and len(rel.parts) == 1 and path.suffix in ('.py', '.md') and not path.name.startswith(('setup', 'install_')))
            if not allowed:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('원본 범위 밖의 연결 파일은 묶지 않습니다.')
            sources[prefix + '/' + rel.as_posix()] = path.read_bytes()
    sources['guides/vxvue.md'] = Path(vxvue_guide).read_bytes()
    required = {'local-skill/SKILL.md', 'alias/SKILL.md', 'bridge/server.py', 'guides/vxvue.md'}
    if not required <= sources.keys():
        raise ValueError('필수 Skill·별칭·연결 코드·지침이 빠졌습니다.')
    config = Path(skill_root) / 'knowledge-config.json'
    knowledge_root = json.loads(config.read_text(encoding='utf-8')).get('knowledge_root', '') if config.exists() else ''
    manifest = {'schema': 1, 'source_home': str(Path.home()), 'source_knowledge_root': knowledge_root,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()},
                'excluded': ['credentials', 'config', 'cache', 'backups', 'product documents']}
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sources.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return {'files': len(sources), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'bytes': output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('skill-root', 'alias-root', 'bridge-root', 'vxvue-guide', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.skill_root, args.alias_root, args.bridge_root, args.vxvue_guide, args.output), ensure_ascii=False))


if __name__ == '__main__': main()
