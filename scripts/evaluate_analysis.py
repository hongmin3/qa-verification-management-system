from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core.evaluation import evaluate_analysis


def main() -> int:
    parser = argparse.ArgumentParser(description="저장된 분석 결과를 QA 정답 JSON과 비교합니다.")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_analysis(
        json.loads(args.result.read_text(encoding="utf-8")),
        json.loads(args.gold.read_text(encoding="utf-8")),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if not report["missing_tc_ids"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
