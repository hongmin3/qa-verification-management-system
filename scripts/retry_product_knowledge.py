"""Daily 08:00 KST knowledge sync, persistent 15-minute catch-up."""
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

bootstrap = argparse.ArgumentParser(add_help=False)
bootstrap.add_argument("--project-root", type=Path)
bootstrap_args, _ = bootstrap.parse_known_args()
sys.path.insert(0, str(bootstrap_args.project_root or Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from app.core.console import configure_stdout


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default="VXvue")
    parser.add_argument("--upload-to", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--wait-retry", action="store_true", help="예약이 재시도 시각보다 먼저 실행되면 남은 시간만 기다린다")
    args = parser.parse_args()
    settings = get_settings()
    if args.check:
        from app.core.product_knowledge import resolve_config, source_available
        config = resolve_config(args.product)
        ready = bool(config and source_available(config))
        print(f"지식 폴더 접근: {'정상' if ready else '확인 필요'} / 매일 08:00 KST · 미완료 시 15분마다 재시도 · 1분마다 화면 수집 요청 확인")
        return 0 if ready else 1
    log = settings.root / "output" / "logs" / "product_knowledge_retry.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log, encoding="utf-8", level=logging.INFO, format="%(asctime)s %(message)s")
    if (Path(__file__).parent / "knowledge_retry.py").is_file():
        from knowledge_retry import run_due
        from knowledge_push import fetch_collect_request, push_product
    else:
        from app.core.knowledge_retry import run_due
        from app.core.knowledge_push import fetch_collect_request, push_product
    # 화면 `지금 수집` 요청(REQ-KNOW-021). 예약이 1분마다 돌며 이것을 묻는다.
    check_request = lambda: fetch_collect_request(args.product, args.upload_to)
    result = run_due(args.product, args.upload_to, state_dir=settings.root / "data" / "knowledge_retry", push=push_product,
                     check_request=check_request)
    if args.wait_retry and result["status"] == "NOT_DUE" and result.get("next_retry_at"):
        due = datetime.fromisoformat(result["next_retry_at"])
        logging.info("재시도 시각까지 대기: %s", due.isoformat())
        while True:
            remaining = (due - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 30))
        result = run_due(args.product, args.upload_to, state_dir=settings.root / "data" / "knowledge_retry", push=push_product,
                         check_request=check_request)
    # 예약이 1분마다 돌므로 할 일 없는 실행은 로그에 남기지 않는다(하루 1,440줄이 쌓인다).
    if result["status"] != "NOT_DUE":
        logging.info("%s %s next_retry=%s", result["status"], result["detail"], result.get("next_retry_at"))
    print(f"[{args.product}] {result['status']} · {result['detail']}")
    if result["status"] not in ("NOT_DUE", "BUSY"):
        from app.modules.knowledge.vxvue_spec_sync import report_sync_log
        detail = result["detail"] + (" / 다음 재시도 " + result["next_retry_at"] if result.get("next_retry_at") else "")
        report_sync_log(args.upload_to, args.product, "product_knowledge", "knowledge_folder", result["status"], detail)
    return 1 if result["status"] in ("FAILED", "PARTIAL", "NEEDS_CONFIG") else 0


if __name__ == "__main__":
    raise SystemExit(main())
