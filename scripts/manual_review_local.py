"""Claude 대화로 돌리는 매뉴얼 개정 검증 도구 (REQ-MANUAL-018).

QA 가 이 저장소에서 Claude Code 에게 "이 경로의 매뉴얼 분석해 줘"라고 말하면, Claude 가
작업 설명서 `.claude/skills/vxvue-manual-revision-review/SKILL.md` 를 따라 이 도구를 두 번 부른다.

    python scripts/manual_review_local.py extract --manual "D:/manual/VXvue_UserManual_Rev1.8.docx" \\
        --release-note "D:/manual/RN_1.1.docx"
    #  → output/manual_review_local/<매뉴얼>_<실행 시각>/changes.json · decisions.json
    #  (Claude 가 decisions.json 을 채우고 QA 와 고친다)
    python scripts/manual_review_local.py finish --run-dir "output/manual_review_local/<폴더>"
    #  → manual_review.xlsx · <매뉴얼>_QA_Comment.docx · comments.json

이 도구는 외부 AI 를 부르지 않고 핵심 앱 DB 에도 쓰지 않는다. 로직은
`app/modules/manual_review/local_review.py` 에 있다.

종료 코드: 0 정상, 1 판정 기록 오류(finish), 2 입력 파일 문제.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.modules.manual_review.local_review import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
