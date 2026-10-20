"""Local entry to the same deterministic helpers used by the shared MCP tools."""
import argparse
import json
from pathlib import Path

from .shared_review import ReviewStore, build_report


def main(argv):
    parser = argparse.ArgumentParser(description='공통 매뉴얼 자료 추출·쪽수 대조·원문 인용 보고서')
    parser.add_argument('command', choices=['inventory', 'pages', 'report'])
    parser.add_argument('--manual')
    parser.add_argument('--source-id')
    parser.add_argument('--workspace-root', default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument('--saved-pdf')
    parser.add_argument('--final-pdf')
    parser.add_argument('--findings')
    args = parser.parse_args(argv)
    try:
        store = ReviewStore(args.workspace_root)
        if args.command == 'inventory':
            if not args.manual:
                raise ValueError('--manual 경로가 필요합니다.')
            path = Path(args.manual)
            source = store.add(path.read_bytes(), path.name)
            data = store.inventory(source['source_id'])
            print(json.dumps({'source_id': source['source_id'], 'paragraphs': len(data['paragraphs']),
                              'comments': len(data['comments']), 'source_unchanged': True}, ensure_ascii=False))
        elif args.command == 'pages':
            if not args.source_id or not (args.saved_pdf or args.final_pdf):
                raise ValueError('--source-id와 대응 PDF 경로가 필요합니다.')
            for name in ('saved', 'final'):
                path = getattr(args, name + '_pdf')
                if path:
                    result = store.attach_pdf(args.source_id, Path(path).read_bytes(), name)
                    args.source_id = result['source_id']
                    print(json.dumps({k: v for k, v in result.items() if k != 'paragraphs'}, ensure_ascii=False))
        else:
            if not args.source_id or not args.findings:
                raise ValueError('--source-id와 --findings JSON 경로가 필요합니다.')
            records = json.loads(Path(args.findings).read_text('utf-8'))
            options = records if isinstance(records, dict) else {'findings': records}
            print(json.dumps(build_report(store, args.source_id, **options), ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print('입력 오류: ' + str(exc))
        return 2
