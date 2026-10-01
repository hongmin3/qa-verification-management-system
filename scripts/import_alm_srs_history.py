"""ALM-QA-Automation 사양서 자동화의 과거 SRS 스냅샷을 QA Agent 스냅샷으로 가져온다 (SPEC REQ-QAINTEL-029).

    .venv/Scripts/python.exe scripts/import_alm_srs_history.py --product VXvue --dry-run
    .venv/Scripts/python.exe scripts/import_alm_srs_history.py --product VXvue

`--source` 를 비우면 `config.yaml` 의 `daily_qa.alm_history.srs_snapshot_dir` 를 쓴다. 상대 경로는 이 프로젝트
폴더 기준이다. 여러 번 돌려도 결과가 같고, 이미 있는 날짜는 덮어쓰지 않는다.

종료 코드: 0 끝남(건너뛴 날짜가 있어도), 2 설정·폴더 오류.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.console import configure_stdout  # noqa: E402
from app.modules.daily_qa import snapshots  # noqa: E402
from app.modules.daily_qa.alm_history import import_history  # noqa: E402
from app.modules.daily_qa.settings import load  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(description="ALM-QA-Automation 과거 SRS 스냅샷 가져오기")
    parser.add_argument("--product", default="", help="제품 이름 (비우면 daily_qa 첫 제품)")
    parser.add_argument("--source", default="", help="srs-spec 스냅샷 폴더")
    parser.add_argument("--dry-run", action="store_true", help="쓰지 않고 할 일만 보인다")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        cfg = load(product=args.product or None)
    except (ValueError, LookupError) as exc:
        print(f"설정 오류: {exc}")
        return 2
    raw = args.source or str(settings.get("daily_qa.alm_history.srs_snapshot_dir", "") or "")
    if not raw:
        print("srs-spec 스냅샷 폴더가 없습니다. --source 또는 daily_qa.alm_history.srs_snapshot_dir 를 적으세요.")
        return 2
    source = Path(raw)
    if not source.is_absolute():
        source = (settings.root / source).resolve()
    if not source.is_dir():
        print(f"srs-spec 스냅샷 폴더를 찾을 수 없습니다: {source}")
        return 2
    project_id = cfg.product_profile.project_id or cfg.product
    store = snapshots.for_settings(cfg)
    result = import_history(source, store, project_id, cfg.product_profile, dry_run=args.dry_run)
    print(f"제품 {cfg.product} · ALM 프로젝트 {project_id} · 원본 {source}")
    print(f"저장 위치 {store.directory(snapshots.KIND_SRS)}" + (" (시험 실행: 쓰지 않음)" if args.dry_run else ""))
    for day in result.days:
        print(f"  {day.line()}")
    counts = result.counts()
    print("합계 " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
