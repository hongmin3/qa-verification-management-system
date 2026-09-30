"""VXvue 일일 QA 점검 한 번 실행 (CLI, SPEC REQ-DAILY-001).

운영 서버에서는 앱 내장 스케줄러(`app/modules/daily_qa/scheduled_jobs.py`)가 평일 아침에 분리된
프로세스로 부른다.
자세한 운영 방법은 `docs/modules/daily-qa.md`.

    python scripts/run_daily_qa.py                 # 정식 실행
    python scripts/run_daily_qa.py --dry-run       # Claude 를 부르지 않고 입력 묶음과 결정적 계산만 (스냅샷·Finding 은 저장하지 않는다)
    python scripts/run_daily_qa.py --weekly        # 오늘이 지정 요일이 아니어도 주 1회 단계(사양–TC 연결 점검, 매뉴얼 누락 후보 점검)까지 돌린다
    python scripts/run_daily_qa.py --no-email      # 메일을 보내지 않는다
    python scripts/run_daily_qa.py --check         # 설정·자격증명·작업 폴더만 확인하고 끝낸다

종료 코드: 0 성공 / 1 일부 또는 전체 실패 / 2 설정 오류 / 3 다른 실행이 진행 중.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.modules.daily_qa import rules  # noqa: E402
from app.modules.daily_qa.pipeline import RunLocked, run_daily  # noqa: E402
from app.modules.daily_qa.settings import load  # noqa: E402
from app.modules.daily_qa.workspace import WorkspaceError, validate_location  # noqa: E402


def check(cfg) -> int:
    problems = 0

    def line(ok: bool, label: str, detail: str = "") -> None:
        nonlocal problems
        problems += 0 if ok else 1
        print(f"[{'OK' if ok else '확인 필요'}] {label}" + (f" — {detail}" if detail else ""))

    line(cfg.enabled, "daily_qa.enabled")
    line(cfg.polarion.configured, "Polarion 설정 (POLARION_HOST / POLARION_TOKEN / project_id)")
    line(bool(cfg.claude_token), "CLAUDE_CODE_OAUTH_TOKEN")
    line(shutil.which(cfg.claude_command) is not None, f"Claude CLI ({cfg.claude_command})")
    try:
        validate_location(cfg.workspace_dir, cfg.root)
        line(True, "작업 폴더 위치", str(cfg.workspace_dir))
    except WorkspaceError as exc:
        line(False, "작업 폴더 위치", str(exc))
    state = rules.check(cfg.product, cfg.root)
    line(state.ok, "QA 규칙 판", state.reason or f"Rev{state.found_rev}")
    line(bool(cfg.email_to), "요약 메일 수신자 (DAILY_QA_EMAIL_TO / NOTIFY_EMAIL_TO)", f"{len(cfg.email_to)}명")
    return 0 if problems == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="VXvue 일일 QA 점검")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--weekly", action="store_true", help="E·F 를 오늘 강제로 돌린다")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    try:
        cfg = load()
    except ValueError as exc:
        print(f"설정 오류: {exc}")
        return 2
    if args.check:
        return check(cfg)
    if not cfg.enabled:
        print("daily_qa.enabled 가 false 라 실행하지 않습니다.")
        return 0

    sender = (lambda *_: {"status": "disabled"}) if args.no_email else None
    try:
        outcome = run_daily(cfg, dry_run=args.dry_run, force_weekly=args.weekly, send_email=sender)
    except RunLocked as exc:
        print(str(exc))
        return 3
    print(f"실행 {outcome['run_id']}: {outcome['status']} · 메일 {outcome['email'].get('status')}")
    for name, stage in outcome["stages"].items():
        print(f"  - {name}: {stage['status']} {stage.get('note', '')}")
    return 0 if outcome["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
