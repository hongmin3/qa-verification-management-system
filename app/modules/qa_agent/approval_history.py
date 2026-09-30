"""QA 결정 기록 (REQ-QAAGENT-012, OPEN_QUESTIONS 8-6).

`qa_agent_approvals` 표(`app/core/storage.py`)는 판정마다 **최신 결정 한 줄**만 둔다. 결정을 다시
적으면 그 줄이 바뀐다. 그래서 결정을 적을 때마다 이 모듈의 표(`qa_agent_approval_events`)에 새 줄을
더한다. 기존 줄은 고치거나 지우지 않는다.

이유: QA 규칙 20절은 AI 결과와 QA 수정 결과를 따로 저장해 규칙 개선에 쓰라고 정한다. 결정을 바꾼
기록이 사라지면 어떤 판정을 QA 가 다시 뒤집었는지 알 수 없다.

화면은 최신 결정을 보이고, 이전 결정은 "결정 기록 N건" 아래에 펼쳐 보인다.
"""

from __future__ import annotations

from datetime import datetime, timezone

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS qa_agent_approval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id TEXT NOT NULL,
    claim_kind TEXT NOT NULL,
    claim_label TEXT NOT NULL,
    qa_decision TEXT NOT NULL,
    qa_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""
_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_qa_agent_approval_events_analysis ON qa_agent_approval_events(analysis_id)"


def _ensure_table(db) -> None:
    db.execute(_TABLE_SQL)
    db.execute(_INDEX_SQL)


def record(storage, analysis_id: str, claim_kind: str, claim_label: str, qa_decision: str, qa_note: str = "") -> None:
    """결정 한 건을 새 줄로 더한다."""
    now = datetime.now(timezone.utc).isoformat()
    with storage.connect() as db:
        _ensure_table(db)
        db.execute(
            "INSERT INTO qa_agent_approval_events(analysis_id,claim_kind,claim_label,qa_decision,qa_note,created_at) VALUES(?,?,?,?,?,?)",
            (analysis_id, claim_kind, claim_label, qa_decision, qa_note, now),
        )


def history_map(storage, analysis_id: str) -> dict[str, list[dict]]:
    """`"<종류>|<대상>"` → 그 판정의 결정 기록 목록. 최신 결정이 먼저 온다."""
    with storage.connect() as db:
        _ensure_table(db)
        rows = db.execute(
            "SELECT * FROM qa_agent_approval_events WHERE analysis_id=? ORDER BY id DESC", (analysis_id,)
        ).fetchall()
    history: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        history.setdefault(f"{item['claim_kind']}|{item['claim_label']}", []).append(item)
    return history
