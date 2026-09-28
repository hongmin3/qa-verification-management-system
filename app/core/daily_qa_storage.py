"""일일 QA 점검 전용 테이블 (`daily_qa_*`, SPEC REQ-DAILY-001 · REQ-DAILY-009).

`Storage` 가 이 믹스인을 상속한다. DB 접근은 `Storage` 한 곳으로만 한다는 규칙
(`knowledge/` core-architecture#storage-single-owner)을 지키면서, 기능 전용 SQL 을
`storage.py` 한 파일에 몰아넣지 않기 위해 나눴다. 메서드 이름은 모두 `daily_qa_` 로 시작한다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

REVIEW_STATUSES = {
    "PENDING": "검토 대기",
    "APPROVED": "승인",
    "REJECTED": "거절",
    "NEED_EVIDENCE": "근거 추가 필요",
}

RUN_STATUSES = {
    "RUNNING": "실행 중",
    "SUCCESS": "성공",
    "PARTIAL": "일부 실패",
    "FAILED": "실패",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DailyQaStorageMixin:
    def _initialize_daily_qa(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_qa_runs (
                    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, dry_run INTEGER NOT NULL DEFAULT 0,
                    stages_json TEXT NOT NULL DEFAULT '{}', summary_json TEXT NOT NULL DEFAULT '{}',
                    email_status TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS daily_qa_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES daily_qa_runs(id),
                    skill TEXT NOT NULL, task_id TEXT NOT NULL, subject TEXT NOT NULL,
                    subject_title TEXT NOT NULL DEFAULT '', verdict TEXT NOT NULL, summary TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', confidence TEXT NOT NULL DEFAULT '',
                    issue_type TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '[]',
                    tc_ref_json TEXT NOT NULL DEFAULT 'null', draft_tcs_json TEXT NOT NULL DEFAULT '[]',
                    review_status TEXT NOT NULL DEFAULT 'PENDING', reviewer TEXT NOT NULL DEFAULT '',
                    review_note TEXT NOT NULL DEFAULT '', reviewed_at TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_daily_qa_findings_run ON daily_qa_findings(run_id);
                CREATE INDEX IF NOT EXISTS idx_daily_qa_findings_status ON daily_qa_findings(review_status);
                CREATE TABLE IF NOT EXISTS daily_qa_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES daily_qa_runs(id),
                    skill TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', question TEXT NOT NULL,
                    why TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '',
                    answered_by TEXT NOT NULL DEFAULT '', answered_at TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_qa_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )

    # -- 실행 --------------------------------------------------------------
    def daily_qa_create_run(self, run_id: str, dry_run: bool) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO daily_qa_runs (id, started_at, status, dry_run) VALUES (?, ?, 'RUNNING', ?)",
                (run_id, now_iso(), int(dry_run)),
            )

    def daily_qa_finish_run(self, run_id: str, status: str, stages: dict, summary: dict) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE daily_qa_runs SET finished_at=?, status=?, stages_json=?, summary_json=? WHERE id=?",
                (now_iso(), status, json.dumps(stages, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), run_id),
            )

    def daily_qa_set_email_status(self, run_id: str, email_status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE daily_qa_runs SET email_status=? WHERE id=?", (email_status, run_id))

    def daily_qa_get_run(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM daily_qa_runs WHERE id=?", (run_id,)).fetchone()
        return self._daily_qa_run_dict(row) if row else None

    def daily_qa_list_runs(self, limit: int = 30) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM daily_qa_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._daily_qa_run_dict(row) for row in rows]

    @staticmethod
    def _daily_qa_run_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["stages"] = json.loads(data.pop("stages_json") or "{}")
        data["summary"] = json.loads(data.pop("summary_json") or "{}")
        data["status_label"] = RUN_STATUSES.get(data["status"], data["status"])
        return data

    # -- Finding -----------------------------------------------------------
    def daily_qa_add_finding(self, run_id: str, skill: str, task_id: str, finding: dict) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO daily_qa_findings (run_id, skill, task_id, subject, subject_title, verdict,
                   summary, detail, confidence, issue_type, evidence_json, tc_ref_json, draft_tcs_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, skill, task_id, finding["subject"], finding.get("subject_title", ""),
                    finding["verdict"], finding["summary"], finding.get("detail", ""),
                    finding.get("confidence", ""), finding.get("issue_type", ""),
                    json.dumps(finding.get("evidence") or [], ensure_ascii=False),
                    json.dumps(finding.get("tc_ref"), ensure_ascii=False),
                    json.dumps(finding.get("draft_tcs") or [], ensure_ascii=False),
                    now_iso(),
                ),
            )
            return int(cursor.lastrowid)

    def daily_qa_list_findings(
        self, run_id: str | None = None, skill: str | None = None, verdict: str | None = None,
        review_status: str | None = None, limit: int = 500,
    ) -> list[dict]:
        clauses, params = [], []
        for column, value in (("run_id", run_id), ("skill", skill), ("verdict", verdict), ("review_status", review_status)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM daily_qa_findings {where} ORDER BY id DESC LIMIT ?", (*params, limit)
            ).fetchall()
        return [self._daily_qa_finding_dict(row) for row in rows]

    def daily_qa_get_finding(self, finding_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM daily_qa_findings WHERE id=?", (finding_id,)).fetchone()
        return self._daily_qa_finding_dict(row) if row else None

    def daily_qa_review_finding(self, finding_id: int, status: str, reviewer: str, note: str) -> bool:
        if status not in REVIEW_STATUSES or status == "PENDING":
            raise ValueError(f"알 수 없는 검토 결정: {status}")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE daily_qa_findings SET review_status=?, reviewer=?, review_note=?, reviewed_at=? WHERE id=?",
                (status, reviewer.strip(), note.strip(), now_iso(), finding_id),
            )
            return cursor.rowcount == 1

    def daily_qa_has_open_finding(self, skill: str, subject: str, verdict: str) -> bool:
        """같은 대상·같은 판정이 아직 거절되지 않은 채 남아 있는가 (중복 Finding 방지)."""
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM daily_qa_findings WHERE skill=? AND subject=? AND verdict=? "
                "AND review_status IN ('PENDING','APPROVED','NEED_EVIDENCE') LIMIT 1",
                (skill, subject, verdict),
            ).fetchone()
        return row is not None

    def daily_qa_review_counts(self, run_id: str | None = None) -> dict[str, int]:
        where, params = ("WHERE run_id=?", (run_id,)) if run_id else ("", ())
        with self.connect() as db:
            rows = db.execute(
                f"SELECT review_status, COUNT(*) AS n FROM daily_qa_findings {where} GROUP BY review_status", params
            ).fetchall()
        return {row["review_status"]: row["n"] for row in rows}

    @staticmethod
    def _daily_qa_finding_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["evidence"] = json.loads(data.pop("evidence_json") or "[]")
        data["tc_ref"] = json.loads(data.pop("tc_ref_json") or "null")
        data["draft_tcs"] = json.loads(data.pop("draft_tcs_json") or "[]")
        data["review_label"] = REVIEW_STATUSES.get(data["review_status"], data["review_status"])
        return data

    # -- 질문 ----------------------------------------------------------------
    def daily_qa_add_question(self, run_id: str, skill: str, subject: str, question: str, why: str) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO daily_qa_questions (run_id, skill, subject, question, why, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, skill, subject, question, why, now_iso()),
            )
            return int(cursor.lastrowid)

    def daily_qa_list_questions(self, unanswered_only: bool = False, run_id: str | None = None, limit: int = 300) -> list[dict]:
        clauses, params = [], []
        if unanswered_only:
            clauses.append("answer=''")
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM daily_qa_questions {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(row) for row in rows]

    def daily_qa_answer_question(self, question_id: int, answer: str, answered_by: str) -> bool:
        if not answer.strip():
            raise ValueError("답변이 비어 있습니다.")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE daily_qa_questions SET answer=?, answered_by=?, answered_at=? WHERE id=?",
                (answer.strip(), answered_by.strip(), now_iso(), question_id),
            )
            return cursor.rowcount == 1

    def daily_qa_answered_questions(self, skill: str) -> list[dict]:
        """다음 실행의 입력에 넣을 답변 (REQ-DAILY-009 4번)."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT subject, question, answer, answered_at FROM daily_qa_questions WHERE skill=? AND answer!='' ORDER BY id DESC LIMIT 100",
                (skill,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- 상태 ----------------------------------------------------------------
    def daily_qa_get_state(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM daily_qa_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def daily_qa_set_state(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO daily_qa_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now_iso()),
            )
