"""배포 뒤 사용을 종료한 단일 이슈 분석 파일만 정리한다 (REQ-QAAGENT-016)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RETIRED_PATHS = (
    *(f"app/modules/qa_agent/{name}.py" for name in
      ("analyzer", "ai_client", "gates", "evidence", "validation", "standard_findings", "approval_history")),
    *(f"app/modules/qa_agent/templates/{name}.html" for name in ("index", "analysis", "history")),
    "app/prompts/qa_agent_issue_impact.yaml", "scripts/compare_models.py",
    *(f"tests/test_qa_agent_{name}.py" for name in ("analyzer", "fixes", "gates", "validation")),
)


def cleanup(root: Path, *, apply: bool = False) -> dict:
    root = root.resolve(strict=True)
    targets = []
    for name in RETIRED_PATHS:
        path = root / name
        # 어느 하나라도 프로젝트 밖으로 연결되면 삭제 전에 전체 작업을 멈춘다.
        path.resolve().relative_to(root)
        if path.is_file() or path.is_symlink():
            targets.append((name, path))
    removed = []
    if apply:
        for name, path in targets:
            path.unlink()
            removed.append(name)
    return {"would_remove": [name for name, _ in targets], "removed": removed}


def main() -> None:
    parser = argparse.ArgumentParser(description="사용 종료 파일만 정리합니다. 기본은 삭제 없이 목록만 확인합니다.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(cleanup(Path(__file__).resolve().parents[1], apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
