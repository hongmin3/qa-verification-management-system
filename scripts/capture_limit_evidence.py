"""Claude 사용량 한도 증거를 직접 찍는다 (REQ-QAINTEL-039). 운영 서버에서 쓴다.

    .venv/bin/python scripts/capture_limit_evidence.py --out /tmp/usage.png   # 시험: 지정한 곳에만 저장
    .venv/bin/python scripts/capture_limit_evidence.py                        # 증거 폴더에 저장 (100% 일 때만)

일일 점검은 한도를 감지하면 같은 일을 스스로 한다. 이 명령은 화면 모양을 확인하거나,
점검 밖에서 한도에 걸린 것을 남길 때 쓴다. `/usage` 만 입력하므로 토큰을 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core.claude_cli import resolve_command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.modules.daily_qa import claude_limits, limit_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="시험 캡처를 저장할 PNG 경로. 증거 폴더에는 넣지 않는다")
    args = parser.parse_args()
    settings = get_settings()
    command = resolve_command(str(settings.get("daily_qa.claude_command", "claude") or "claude"))
    missing = limit_evidence.tools_missing()
    if missing:
        print(f"[오류] 캡처 도구가 없습니다: {', '.join(missing)} (sudo apt install xvfb xterm xdotool imagemagick)")
        return 2
    timeout = int(settings.get("daily_qa.limit_evidence.timeout_seconds", 60) or 60)
    if args.out:
        with tempfile.TemporaryDirectory(prefix="limit_evidence_") as temp:
            shot = limit_evidence.run_xterm(Path(temp), command, timeout)
            if shot.png is None:
                print(f"[실패] {shot.reason}")
                return 1
            args.out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(shot.png, args.out)
        print(shot.screen)
        print(f"\n사용률: {limit_evidence.read_usage(shot.screen)}")
        print(f"저장: {args.out}")
        return 0
    limit = claude_limits.LimitInfo(claude_limits.KIND_UNKNOWN, "", "수동 캡처 (scripts/capture_limit_evidence.py)")
    result = limit_evidence.capture(limit, settings=settings, claude_command=command, force=True)
    if result.saved:
        print(f"저장: {', '.join(result.files)} · 사용률 {result.usage}")
        return 0
    print(f"[저장 안 함] {result.reason} · 사용률 {result.usage}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
