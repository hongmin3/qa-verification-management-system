"""VXvue 사양서 동기화 CLI. 실제 로직은 app/sync/vxvue_spec.py에 있다 (앱의 수동 트리거
버튼과 동일한 코드를 재사용하기 위함). Windows 작업 스케줄러에 이 스크립트를 등록한다.

사용법:
    .venv\\Scripts\\python.exe scripts\\sync_vxvue_spec.py [--target-url URL] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.modules.impact_analyzer.vxvue_spec_sync import acquire_lock, release_lock, report_sync_log, run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-url", default="http://10.13.0.222:12000", help="Knowledge API를 등록할 서버 (기본: 운영 서버)")
    parser.add_argument("--dry-run", action="store_true", help="실제 등록 없이 변경분만 확인")
    args = parser.parse_args()

    if not acquire_lock():
        print("이미 실행 중인 것으로 보입니다 (lock 파일 존재). 중복 실행을 막기 위해 종료합니다.")
        sys.exit(1)
    try:
        result = run(args.target_url, args.dry_run)
        print(f"{result['status']}: {result['detail']}")
        if not args.dry_run:
            report_sync_log(args.target_url, "VXvue", "specification", "alm_crawler", result["status"], result["detail"])
        sys.exit(0 if result["status"] in ("SUCCESS", "DRY_RUN", "PARTIAL") else 1)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
