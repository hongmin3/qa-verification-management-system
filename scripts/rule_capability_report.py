"""규칙 절별 구현현황을 출력한다.

    python scripts/rule_capability_report.py             # 요약 + 전체 표
    python scripts/rule_capability_report.py --status PLANNED
    python scripts/rule_capability_report.py --markdown  # 문서에 붙일 표

숫자를 문서에 손으로 적어두지 않기 위한 스크립트다. 판단의 원천은
`app/modules/qa_agent/rule_capability.py` 표 하나이고, 문서와 화면은 이것을 인용한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.modules.qa_agent.rule_capability import (  # noqa: E402
    MODE_LABELS,
    RULE_CAPABILITY,
    STATUS_LABELS,
    missing_paths,
    summary,
)

ROOT = Path(__file__).resolve().parents[1]


def _sorted_entries():
    return sorted(RULE_CAPABILITY.values(), key=lambda capability: int(capability.section))


def print_summary() -> None:
    result = summary()
    total = result["total"]
    print(f"규칙 최상위 절 {total}개")
    print("\n구현 방식")
    for mode, label in MODE_LABELS.items():
        count = result["by_mode"].get(mode, 0)
        print(f"  {label:24} {count:3}개  {100 * count / total:5.1f}%")
    print("\n구현 상태")
    for status, label in STATUS_LABELS.items():
        count = result["by_status"].get(status, 0)
        print(f"  {label:24} {count:3}개  {100 * count / total:5.1f}%")
    automatable = sum(count for mode, count in result["by_mode"].items() if mode in ("CODE", "CODE+LLM", "DRAFT_ONLY"))
    print(f"\n자동화 범위 {automatable}개 / QA 입력 필수 {result['by_mode'].get('QA_INPUT', 0)}개 / 런타임 대상 아님 {result['by_mode'].get('OUT_OF_SCOPE', 0)}개")
    missing = missing_paths(ROOT)
    if missing:
        print(f"\n[경고] IMPLEMENTED 인데 파일이 없는 항목: {missing}")


def print_table(status_filter: str | None) -> None:
    print(f"\n{'절':>4}  {'제목':40} {'구현 방식':22} {'상태':8} 위치")
    print("-" * 118)
    for capability in _sorted_entries():
        if status_filter and capability.status != status_filter:
            continue
        print(
            f"{capability.section:>4}  {capability.title[:40]:40} "
            f"{MODE_LABELS[capability.mode]:22} {STATUS_LABELS[capability.status]:8} {capability.where}"
        )


def print_markdown(status_filter: str | None) -> None:
    print("| 절 | 제목 | 구현 방식 | 상태 | 근거·비고 |")
    print("|---|---|---|---|---|")
    for capability in _sorted_entries():
        if status_filter and capability.status != status_filter:
            continue
        note = capability.note.replace("|", "\\|")
        where = f"`{capability.where}` — " if capability.where else ""
        print(f"| §{capability.section} | {capability.title} | {MODE_LABELS[capability.mode]} | {STATUS_LABELS[capability.status]} | {where}{note} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="규칙 절별 구현현황 리포트")
    parser.add_argument("--status", choices=sorted(STATUS_LABELS), help="이 상태만 출력")
    parser.add_argument("--markdown", action="store_true", help="Markdown 표로 출력")
    args = parser.parse_args()

    if args.markdown:
        print_markdown(args.status)
        return 0
    print_summary()
    print_table(args.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
