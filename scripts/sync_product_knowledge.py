"""제품 지식 폴더를 수집하고 분석 대상으로 등록한다 (CLI).

지식 폴더는 QA 담당자의 PC에 있고 운영 서버에는 없다. 그래서 이 스크립트를 그 PC의 작업
스케줄러에 걸어 매주 자동 실행한다 — 앱 안 스케줄러는 폴더에 접근할 수 없으면 조용히
건너뛴다 (`app/modules/qa_agent/scheduled_jobs.py`).

    python scripts/sync_product_knowledge.py                       # 설정된 모든 제품 (로컬 DB)
    python scripts/sync_product_knowledge.py --product VXvue
    python scripts/sync_product_knowledge.py --dry-run             # 무엇이 바뀌는지만 확인
    python scripts/sync_product_knowledge.py --upload-to http://10.13.0.222:12000

**운영 서버에 반영하려면 `--upload-to` 를 쓴다.** 서버는 이 폴더를 볼 수 없으므로(담당자 PC
는 Wi-Fi DHCP 이고 사내 DNS 에 이름이 없다), 폴더를 볼 수 있는 이 PC 가 수집한 뒤 바뀐
파일만 서버로 올리고 서버가 등록까지 마친다 — 자세한 이유는 `app/core/knowledge_upload.py`.

`--report-to` 는 로컬 DB 에 수집하면서 원격 sync 로그에만 결과를 남기는 옛 방식이다
(`--upload-to` 를 쓰면 서버가 스스로 남기므로 함께 쓰지 않는다).

Windows 작업 스케줄러 등록 예 (운영 서버 반영):

    schtasks /Create /TN "QA_ProductKnowledge_Sync" /SC WEEKLY /D MON /ST 07:45 ^
      /TR "C:\\path\\to\\.venv\\Scripts\\python.exe C:\\path\\to\\scripts\\sync_product_knowledge.py --upload-to http://10.13.0.222:12000"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core.product_config import list_product_configs  # noqa: E402
from app.core.product_knowledge import (  # noqa: E402
    resolve_config,
    scan_source,
    source_available,
    sync_and_register,
    sync_product,
)


def _report_remote(target_url: str, product: str, status: str, detail: str) -> None:
    import httpx

    try:
        with httpx.Client() as client:
            client.post(
                f"{target_url.rstrip('/')}/knowledge/sync-log",
                data={"product": product, "kind": "product_knowledge", "source": "knowledge_folder", "status": status, "detail": detail},
                timeout=15,
            )
    except Exception as exc:  # 보고 실패가 수집 자체를 실패로 만들지 않는다
        print(f"  [경고] 원격 sync 로그 보고 실패 (수집은 {status}로 완료): {exc}")


def _dry_run(product: str) -> int:
    config = resolve_config(product)
    if config is None:
        print(f"[{product}] 제품 설정이 없습니다.")
        return 1
    if not source_available(config):
        print(f"[{product}] 지식 폴더에 접근할 수 없습니다: {config.knowledge_source.dir or '(미설정)'}")
        return 1
    scan = scan_source(config)
    outcome = sync_product(config, dry_run=True)
    print(f"[{product}] {config.knowledge_source.dir}")
    print(f"  분류: {scan.counts()}")
    for asset in scan.selected:
        print(f"  [수집] {asset.kind:20} {asset.revision or '-':10} {asset.file_name}")
    for asset in scan.assets:
        if not asset.selected:
            print(f"  [제외] {asset.file_name} — {asset.exclude_reason}")
    print(f"  → {outcome.detail}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="제품 지식 폴더 수집 + 분석 대상 등록")
    parser.add_argument("--product", action="append", default=[], help="제품명 또는 slug (여러 번 지정 가능). 없으면 설정된 전 제품")
    parser.add_argument("--dry-run", action="store_true", help="수집하지 않고 무엇이 바뀌는지만 출력")
    parser.add_argument("--report-to", default="", help="원격 서버 base URL. sync 로그를 그 서버에도 보고한다")
    parser.add_argument(
        "--upload-to",
        default="",
        help="원격 서버 base URL. 수집한 자산을 그 서버로 올려 등록까지 시킨다 (서버는 이 폴더를 볼 수 없다)",
    )
    args = parser.parse_args()

    if args.upload_to and args.report_to:
        # 업로드 경로는 서버가 스스로 sync_log 를 남긴다. 둘 다 주면 같은 실행이 두 줄로
        # 보여 "언제 최신화됐나"를 읽기 어려워진다.
        print("--upload-to 를 쓰면 서버가 직접 sync 로그를 남깁니다. --report-to 는 필요 없습니다.")
        return 1

    products = args.product or [config.product for config in list_product_configs() if config.knowledge_source.dir]
    if not products:
        print("config/products/ 에 knowledge_source.dir 이 설정된 제품이 없습니다.")
        return 1

    exit_code = 0
    for product in products:
        if args.dry_run and not args.upload_to:
            exit_code |= _dry_run(product)
            continue
        if args.upload_to:
            from app.core.knowledge_push import push_product

            result = push_product(product, args.upload_to, dry_run=args.dry_run)
        else:
            result = sync_and_register(product)
        print(f"[{product}] {result['status']} — {result['detail']}")
        for note in result.get("duplicates", []):
            print(f"  중복 확인 필요: {note}")
        for note in result.get("excluded", []):
            print(f"  제외: {note}")
        for note in result.get("removed", []):
            print(f"  서버에서 정리: {note}")
        if args.report_to:
            _report_remote(args.report_to, product, result["status"], result["detail"])
        if result["status"] in ("FAILED", "NEEDS_CONFIG"):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
