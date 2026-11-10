"""Export the common Bellalun Markdown and use the existing incremental uploader."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MARKER = '<!-- bellalun-common-export:v1 -->\n'
ADAPTER = Path(__file__).resolve().parents[1] / 'deploy/bellalun-skill/bellalun-server-ai/SKILL.md'


def _read_adapter():
    # A core.autocrlf checkout must not change the exported server document or its hashes.
    return ADAPTER.read_bytes().replace(b'\r\n', b'\n')


def _write(path, data):
    if path.exists() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name); handle.write(data)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def export_common_skill(skill_root, knowledge_root, backup_root=None):
    skill_root, knowledge_root = Path(skill_root).resolve(), Path(knowledge_root).resolve()
    if not knowledge_root.is_dir():
        raise ValueError('지식 폴더에 접근할 수 없습니다. 빈 폴더로 대체하지 않습니다.')
    paths = [skill_root / 'SKILL.md', *sorted((skill_root / 'references').rglob('*.md'))]
    qa = skill_root / 'references/bellalun-qa-guide.md'
    if qa not in paths or len(paths) < 3:
        raise ValueError('공통 Skill·통합 QA·참조 문서를 확인하세요.')
    payloads = []
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(skill_root):
            raise ValueError('공통 Skill 밖의 참조 파일은 내보내지 않습니다.')
        data = path.read_bytes(); text = data.decode('utf-8-sig')
        if not text.strip():
            raise ValueError('공통 Skill 또는 참조 문서가 비어 있습니다.')
        for link in re.findall(r'references[/\\][^`\n"\x27()<>\[\]]+?\.md', text):
            if '*' in link or '?' in link:
                continue  # A documented file pattern is not a literal reference link.
            linked = skill_root / link.replace('\\', '/')
            if not linked.is_file() or not linked.resolve().is_relative_to(skill_root / 'references'):
                raise ValueError('공통 Skill이 참조하는 문서를 읽을 수 없습니다.')
        payloads.append((path, data))
    records = [{'path': p.relative_to(skill_root).as_posix(), 'sha256': hashlib.sha256(d).hexdigest()} for p, d in payloads]
    body = '# Bellalun 공통 Skill 동기화본\n\n원본의 로컬 명령은 서버 연결에서 실행하지 않는다. 서버 도구 지침을 우선한다.\n'
    # Local install/retrieval/review references are not executable on the server.
    # Keep them intact locally and export the maintained server connector instead.
    adapter_data = _read_adapter()
    body += '\n\n## 서버 연결 지침\n' + adapter_data.decode('utf-8-sig')
    records = [r for r in records if r['path'] == 'references/bellalun-qa-guide.md']
    records.append({'path': 'server-adapter/SKILL.md', 'sha256': hashlib.sha256(adapter_data).hexdigest()})
    data = (MARKER + '<!-- source-hashes ' + json.dumps(records, ensure_ascii=False, sort_keys=True) + ' -->\n' + body).encode('utf8')
    if any(p.read_bytes() != d for p, d in payloads):
        raise ValueError('내보내기 도중 공통 Skill이 바뀌었습니다. 다시 실행하세요.')
    target = knowledge_root / '[Skill 지침] Bellalun Viewer.md'
    if target.exists() and not target.read_text(encoding='utf-8-sig').startswith(MARKER):
        raise ValueError('기존 수동 작성 파일은 덮어쓰지 않습니다.')
    qa_target = knowledge_root / 'Bellalun Viewer QA 지침.md'
    qa_data = dict(payloads)[qa]
    from app.core.qa_workflow import validate_guide
    validation = validate_guide(qa_data.decode('utf-8-sig'), 'Bellalun',
                                qa_target.read_text(encoding='utf-8-sig') if qa_target.exists() else None)
    if validation['errors']:
        raise ValueError('; '.join(validation['errors']))
    if _read_adapter() != adapter_data:
        raise ValueError('내보내기 도중 서버 연결 지침이 바뀌었습니다.')
    if qa_target.exists() and qa_target.read_bytes() != qa_data:
        backup = Path(backup_root or Path(__file__).resolve().parents[1] / 'data/bellalun_sync/backups')
        backup.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-qa-guide.md'
        shutil.copy2(qa_target, backup / name)
    _write(qa_target, qa_data); _write(target, data)
    return {'path': str(target), 'sha256': hashlib.sha256(data).hexdigest(),
            'qa_sha256': hashlib.sha256(qa_data).hexdigest(), 'files': records, 'validation': validation}


def main():
    parser = argparse.ArgumentParser(description='Bellalun 공통 지침 내보내기와 서버 변경분 동기화')
    parser.add_argument('--skill-root', required=True, type=Path)
    parser.add_argument('--knowledge-root', required=True, type=Path)
    parser.add_argument('--upload-to', required=True)
    parser.add_argument('--only-when-requested', action='store_true',
                        help='서버 화면의 수집 요청이 있을 때만 올린다 (5분 간격 예약용, REQ-KNOW-024)')
    args = parser.parse_args()
    try:
        os.environ['QA_KNOWLEDGE_DIR_BELLALUN_VIEWER'] = str(args.knowledge_root.resolve())
        from app.core.knowledge_push import push_product

        def sync(product, target_url):
            exported = export_common_skill(args.skill_root, args.knowledge_root)
            result = push_product(product, target_url)
            return {**result, 'export': exported}

        if args.only_when_requested:
            from app.core.config import get_settings
            from app.core.knowledge_push import fetch_collect_request
            from app.core.knowledge_retry import run_due
            result = run_due('Bellalun Viewer', args.upload_to, state_dir=get_settings().root / 'data' / 'knowledge_retry',
                             push=sync, check_request=lambda: fetch_collect_request('Bellalun Viewer', args.upload_to),
                             daily=False)
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 1 if result['status'] == 'FAILED' else 0
        result = sync('Bellalun Viewer', args.upload_to)
        print(json.dumps({'export': result.pop('export'), 'upload': result}, ensure_ascii=False, default=str))
        return 0 if result['status'] == 'SUCCESS' else 1
    except (OSError, ValueError) as exc:
        print('Bellalun 동기화 실패: ' + type(exc).__name__ + ': ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
