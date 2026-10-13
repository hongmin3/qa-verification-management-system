"""PC에서 SRS 수집·PDF 생성·지식 폴더 배포를 실행한다 (REQ-SYNC-003)."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.console import configure_stdout
configure_stdout()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--from-snapshot', metavar='YYYY-MM-DD')
    parser.add_argument('--no-publish', action='store_true')
    args = parser.parse_args(argv)
    try:
        from app.modules.knowledge.srs_crawler.config import load_config, ConfigError
        config = load_config()
        missing = [name for name in ('bs4', 'playwright', 'fitz') if importlib.util.find_spec(name) is None]
        if missing:
            print('SRS 수집 라이브러리가 없습니다: ' + ', '.join(missing))
            return 2
        if args.check or args.dry_run:
            print('설정 정상: 프로젝트 ' + ', '.join(p.id for p in config.projects))
            print('출력: ' + str(config.base_dir))
            print('지식 폴더: ' + str(config.knowledge_folder))
            print('수집·배포 미실행')
            return 0
    except (OSError, ValueError, ConfigError) as exc:
        print('SRS 수집 설정 오류: ' + type(exc).__name__)
        return 2
    try:
        from app.modules.knowledge.srs_crawler.pipeline import run
        result = run(config, snapshot_date=args.from_snapshot, force=args.force, publish=not args.no_publish)
        print(f'{result["status"]}: PDF {len(result["files"])}개, 배포={result["published"]}, 재사용={result["reused"]}')
        return 0
    except Exception as exc:
        print('SRS 수집·배포 실패: ' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
