"""일일 QA 점검·QA Intelligence 전용 테이블 (`daily_qa_*`, `qa_change_events`).

SPEC REQ-DAILY-001 · REQ-DAILY-009 · REQ-QAINTEL-006 · REQ-QAINTEL-023.

`Storage` 가 이 믹스인을 상속한다. DB 접근은 `Storage` 한 곳으로만 한다는 규칙
(`knowledge/` core-architecture#storage-single-owner)을 지키면서, 기능 전용 SQL 을
`storage.py` 한 파일에 몰아넣지 않기 위해 나눴다. 메서드 이름은 모두 `daily_qa_` 로 시작한다.

**분석 결과를 새 표가 아니라 `daily_qa_findings` 에 넣는 이유** (REQ-QAINTEL-006): 분석 한 건은
대상·판정·요약·근거·초안을 가진 Finding 과 모양이 같다. 새 표를 만들면 초안 Excel·중복 방지·실행
상세가 두 표를 따로 읽어야 한다. 그래서 열만 더한다(`analysis_type`, `event_ids_json`,
`sections_json`, `related_ids_json`). 이벤트는 분석 여부와 무관하게 쌓이는 기록이라 따로 둔다.

**제품 열** (REQ-QAINTEL-023): 개편 전 기록은 `product` 가 비어 있다. 조회 함수의
`include_legacy=True` 가 그 기록을 옛 제품(`daily_qa.product`)의 것으로 함께 읽는다. 열을 채우는
옮기기 작업은 하지 않는다.
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
    "NO_CHANGE": "변경사항 없음",
    "BASELINE": "기준 스냅샷 생성",
}

EVENT_STATUSES = {
    "not_required": "분석 필요 없음",
    "pending": "분석 대기",
    "done": "분석 완료",
    "failed": "분석 실패(다시 시도)",
    "abandoned": "분석 포기",
}

_ADDED_COLUMNS = {
    "daily_qa_runs": {"product": "TEXT NOT NULL DEFAULT ''"},
    "daily_qa_findings": {
        "product": "TEXT NOT NULL DEFAULT ''",
        "analysis_type": "TEXT NOT NULL DEFAULT ''",
        "event_ids_json": "TEXT NOT NULL DEFAULT '[]'",
        "sections_json": "TEXT NOT NULL DEFAULT '{}'",
        "related_ids_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "daily_qa_questions": {"product": "TEXT NOT NULL DEFAULT ''"},
    # remaining_analyses_json: 아직 끝나지 않은 분석 종류. 빈 값('')은 개편 초기 행이라 analyses 로 읽는다.
    "qa_change_events": {"after_fingerprint": "TEXT NOT NULL DEFAULT ''", "remaining_analyses_json": "TEXT NOT NULL DEFAULT ''"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _product_clause(product: str | None, include_legacy: bool, column: str = "product") -> tuple[str, list]:
    if not product:
        return "", []
    if include_legacy:
        return f"({column}=? OR {column}='')", [product]
    return f"{column}=?", [product]


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
                CREATE TABLE IF NOT EXISTS qa_change_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL, run_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
                    changed_fields_json TEXT NOT NULL DEFAULT '[]', analysis_required INTEGER NOT NULL DEFAULT 0,
                    analyses_json TEXT NOT NULL DEFAULT '[]', reason TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL, analysis_status TEXT NOT NULL DEFAULT 'not_required',
                    attempts INTEGER NOT NULL DEFAULT 0, finding_ids_json TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT NOT NULL DEFAULT '', analyzed_run_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_change_events_fp ON qa_change_events(product, fingerprint);
                CREATE INDEX IF NOT EXISTS idx_qa_change_events_run ON qa_change_events(run_id);
                CREATE INDEX IF NOT EXISTS idx_qa_change_events_status ON qa_change_events(product, analysis_status);
                CREATE INDEX IF NOT EXISTS idx_qa_change_events_entity ON qa_change_events(product, entity_id);
                """
            )
            for table, columns in _ADDED_COLUMNS.items():
                existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for column, ddl in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_daily_qa_findings_product ON daily_qa_findings(product, created_at)")

    # -- 실행 --------------------------------------------------------------
    def daily_qa_create_run(self, run_id: str, dry_run: bool, product: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO daily_qa_runs (id, started_at, status, dry_run, product) VALUES (?, ?, 'RUNNING', ?, ?)",
                (run_id, now_iso(), int(dry_run), product),
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

    def daily_qa_list_runs(self, limit: int = 30, product: str | None = None, include_legacy: bool = False,
                           since: str | None = None, until: str | None = None) -> list[dict]:
        clause, params = _product_clause(product, include_legacy)
        clauses = [clause] if clause else []
        if since:
            clauses.append("started_at>=?")
            params.append(since)
        if until:
            clauses.append("started_at<?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM daily_qa_runs {where} ORDER BY started_at DESC, id DESC LIMIT ?", (*params, limit)).fetchall()
        return [self._daily_qa_run_dict(row) for row in rows]

    @staticmethod
    def _daily_qa_run_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["stages"] = json.loads(data.pop("stages_json") or "{}")
        data["summary"] = json.loads(data.pop("summary_json") or "{}")
        data["status_label"] = RUN_STATUSES.get(data["status"], data["status"])
        return data

    # -- Finding -----------------------------------------------------------
    def daily_qa_add_finding(self, run_id: str, skill: str, task_id: str, finding: dict, product: str = "") -> int:
        # 표에 `action` 칸이 없다. QA 할 일은 화면 카드에 보여야 하므로 구획에 함께 남긴다 (REQ-QAINTEL-033).
        sections = dict(finding.get("sections") or {})
        if finding.get("action") and not sections.get("action"):
            sections["action"] = finding["action"]
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO daily_qa_findings (run_id, skill, task_id, subject, subject_title, verdict,
                   summary, detail, confidence, issue_type, evidence_json, tc_ref_json, draft_tcs_json, created_at,
                   product, analysis_type, event_ids_json, sections_json, related_ids_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, skill, task_id, finding["subject"], finding.get("subject_title", ""),
                    finding["verdict"], finding["summary"], finding.get("detail", ""),
                    finding.get("confidence", ""), finding.get("issue_type", ""),
                    json.dumps(finding.get("evidence") or [], ensure_ascii=False),
                    json.dumps(finding.get("tc_ref"), ensure_ascii=False),
                    json.dumps(finding.get("draft_tcs") or [], ensure_ascii=False),
                    now_iso(), product, finding.get("analysis_type", ""),
                    json.dumps(finding.get("event_ids") or [], ensure_ascii=False),
                    json.dumps(sections, ensure_ascii=False),
                    json.dumps(finding.get("related_ids") or {}, ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def daily_qa_list_findings(
        self, run_id: str | None = None, skill: str | None = None, verdict: str | None = None,
        review_status: str | None = None, limit: int = 500, product: str | None = None, include_legacy: bool = False,
        analysis_type: str | None = None, only_analyses: bool = False, since: str | None = None, until: str | None = None,
        skills: tuple[str, ...] | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        for column, value in (("run_id", run_id), ("skill", skill), ("verdict", verdict), ("review_status", review_status),
                              ("analysis_type", analysis_type)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if skills:
            clauses.append(f"skill IN ({','.join('?' for _ in skills)})")
            params.extend(skills)
        clause, product_params = _product_clause(product, include_legacy)
        if clause:
            clauses.append(clause)
            params.extend(product_params)
        if only_analyses:
            clauses.append("analysis_type!=''")
        if since:
            clauses.append("created_at>=?")
            params.append(since)
        if until:
            clauses.append("created_at<?")
            params.append(until)
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
        """옛 검토 결정 저장. 1차 개편 화면에서는 부르지 않는다 (REQ-QAINTEL-019). 기록 호환을 위해 둔다."""
        if status not in REVIEW_STATUSES or status == "PENDING":
            raise ValueError(f"알 수 없는 검토 결정: {status}")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE daily_qa_findings SET review_status=?, reviewer=?, review_note=?, reviewed_at=? WHERE id=?",
                (status, reviewer.strip(), note.strip(), now_iso(), finding_id),
            )
            return cursor.rowcount == 1

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
        data["event_ids"] = json.loads(data.pop("event_ids_json", "[]") or "[]")
        data["sections"] = json.loads(data.pop("sections_json", "{}") or "{}")
        data["related_ids"] = json.loads(data.pop("related_ids_json", "{}") or "{}")
        data["review_label"] = REVIEW_STATUSES.get(data["review_status"], data["review_status"])
        return data

    # -- 질문 ----------------------------------------------------------------
    def daily_qa_add_question(self, run_id: str, skill: str, subject: str, question: str, why: str, product: str = "") -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO daily_qa_questions (run_id, skill, subject, question, why, created_at, product) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, skill, subject, question, why, now_iso(), product),
            )
            return int(cursor.lastrowid)

    def daily_qa_list_questions(self, unanswered_only: bool = False, run_id: str | None = None, limit: int = 300,
                                subject: str | None = None) -> list[dict]:
        clauses, params = [], []
        if unanswered_only:
            clauses.append("answer=''")
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if subject:
            clauses.append("subject=?")
            params.append(subject)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM daily_qa_questions {where} ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(row) for row in rows]

    def daily_qa_answer_question(self, question_id: int, answer: str, answered_by: str) -> bool:
        """옛 질문 답변 저장. 1차 개편 화면에서는 부르지 않는다. 기록 호환을 위해 둔다."""
        if not answer.strip():
            raise ValueError("답변이 비어 있습니다.")
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE daily_qa_questions SET answer=?, answered_by=?, answered_at=? WHERE id=?",
                (answer.strip(), answered_by.strip(), now_iso(), question_id),
            )
            return cursor.rowcount == 1

    def daily_qa_answered_questions(self, skill: str, product: str | None = None, include_legacy: bool = False,
                                    aliases: tuple[str, ...] | None = None) -> list[dict]:
        """다음 실행의 입력에 넣을 답변 (REQ-DAILY-009 4번). 옛 이름 Skill(`aliases`)의 답변도 계속 넣는다.

        제품을 주면 그 제품 질문의 답만 넣는다(REQ-QAINTEL-023). 개편 전 질문(product='')은 옛 제품만 읽는다.
        """
        if aliases is None:
            from app.modules.daily_qa.schema import SKILL_ALIASES

            aliases = SKILL_ALIASES.get(skill, ())
        names = (skill, *aliases)
        clauses = [f"skill IN ({','.join('?' for _ in names)})", "answer!=''"]
        params: list = list(names)
        clause, values = _product_clause(product, include_legacy)
        if clause:
            clauses.append(clause)
            params.extend(values)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT subject, question, answer, answered_at FROM daily_qa_questions WHERE {' AND '.join(clauses)} "
                "ORDER BY id DESC LIMIT 100",
                params,
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

    # -- 변경 이벤트 (REQ-QAINTEL-006) ------------------------------------------
    def daily_qa_add_events(self, product: str, run_id: str, events: list[dict], skip_known_after: bool = False) -> list[int]:
        """이벤트를 한 트랜잭션으로 저장한다. 같은 제품·같은 지문의 이벤트는 다시 넣지 않는다.

        `skip_known_after` 면 바뀐 뒤 값을 이미 아는 이벤트도 넣지 않는다. 기간 분석이 매일 실행에서 이미
        분석한 끝 상태를 다시 분석하지 않게 한다 (REQ-QAINTEL-027 순서 4). 필드는 그 대상의 가장 최근 값과,
        댓글은 번호로 비교한다(`change_events.already_known`).
        새로 넣은 이벤트 번호만 돌려주고, 넣은 이벤트 dict 에는 `id` 를 적는다. 하나라도 실패하면 모두
        되돌린다(호출자가 스냅샷을 되돌린다).
        """
        from app.modules.daily_qa.change_events import already_known

        created = now_iso()
        inserted: list[int] = []
        with self.connect() as db:
            for event in events:
                if skip_known_after:
                    prior = [self._daily_qa_event_dict(row) for row in db.execute(
                        "SELECT * FROM qa_change_events WHERE product=? AND entity_id=? ORDER BY id", (product, event["entity_id"]))]
                    if already_known(event, prior):
                        continue
                status = "pending" if event.get("analysis_required") else "not_required"
                remaining = list(event.get("analyses") or []) if event.get("analysis_required") else []
                cursor = db.execute(
                    """INSERT OR IGNORE INTO qa_change_events (product, run_id, entity_type, entity_id, event_type,
                       before_json, after_json, changed_fields_json, analysis_required, analyses_json, reason,
                       fingerprint, analysis_status, created_at, updated_at, after_fingerprint, remaining_analyses_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        product, run_id, event["entity_type"], event["entity_id"], event["event_type"],
                        json.dumps(event.get("before") or {}, ensure_ascii=False, default=str),
                        json.dumps(event.get("after") or {}, ensure_ascii=False, default=str),
                        json.dumps(event.get("changed_fields") or [], ensure_ascii=False),
                        int(bool(event.get("analysis_required"))),
                        json.dumps(event.get("analyses") or [], ensure_ascii=False),
                        event.get("reason", ""), event["fingerprint"], status, created, created,
                        event.get("after_fingerprint", ""), json.dumps(remaining, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount:
                    inserted.append(int(cursor.lastrowid))
                    event["id"] = int(cursor.lastrowid)
        return inserted

    def daily_qa_list_events(
        self, product: str | None = None, run_id: str | None = None, statuses: tuple[str, ...] | None = None,
        since: str | None = None, until: str | None = None, entity_id: str | None = None,
        event_types: tuple[str, ...] | None = None, ids: list[int] | None = None, limit: int = 5000,
    ) -> list[dict]:
        clauses, params = [], []
        for column, value in (("product", product), ("run_id", run_id), ("entity_id", entity_id)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        for column, values in (("analysis_status", statuses), ("event_type", event_types), ("id", ids)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if since:
            clauses.append("created_at>=?")
            params.append(since)
        if until:
            clauses.append("created_at<?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM qa_change_events {where} ORDER BY id LIMIT ?", (*params, limit)).fetchall()
        return [self._daily_qa_event_dict(row) for row in rows]

    def daily_qa_update_events(self, ids: list[int], status: str, run_id: str = "", finding_ids: list[int] | None = None,
                               error: str = "", count_attempt: bool = False) -> None:
        if not ids:
            return
        if status not in EVENT_STATUSES:
            raise ValueError(f"알 수 없는 이벤트 상태: {status}")
        marks = ",".join("?" for _ in ids)
        with self.connect() as db:
            db.execute(
                f"""UPDATE qa_change_events SET analysis_status=?, analyzed_run_id=CASE WHEN ?='' THEN analyzed_run_id ELSE ? END,
                    finding_ids_json=CASE WHEN ? IS NULL THEN finding_ids_json ELSE ? END, last_error=?,
                    attempts=attempts+?, updated_at=? WHERE id IN ({marks})""",
                (status, run_id, run_id, None if finding_ids is None else 1,
                 json.dumps(finding_ids or [], ensure_ascii=False), error[:500], int(count_attempt), now_iso(), *ids),
            )

    def daily_qa_mark_analysis(self, ids: list[int], analysis: str, result: str, run_id: str = "",
                               finding_ids: list[int] | None = None, error: str = "", max_attempts: int = 3) -> None:
        """이벤트에 딸린 분석 하나의 결과를 적는다 (REQ-QAINTEL-006).

        한 이벤트가 두 분석을 부르면(수정 완료 이슈의 새 댓글 → 새 댓글 분석과 수정 완료 이슈 분석) 분석마다
        끝났는지 따로 본다. 남은 분석이 없어야 `done` 이다. 실패 횟수는 실행 하나에 한 번만 센다.

        result: `done`(성공), `failed`(작업 실패), `limit`(사용량 한도. 횟수를 세지 않고 대기로 남긴다).
        """
        if not ids:
            return
        if result not in ("done", "failed", "limit"):
            raise ValueError(f"알 수 없는 분석 결과: {result}")
        marks = ",".join("?" for _ in ids)
        with self.connect() as db:
            rows = [self._daily_qa_event_dict(row) for row in db.execute(f"SELECT * FROM qa_change_events WHERE id IN ({marks})", ids)]
            for event in rows:
                remaining = list(event["remaining_analyses"])
                attempts, status, last_error = event["attempts"], event["analysis_status"], event["last_error"]
                found = list(event["finding_ids"])
                failed_this_run = status == "failed" and event["analyzed_run_id"] == run_id
                if result == "done":
                    remaining = [kind for kind in remaining if kind != analysis]
                    found += [value for value in finding_ids or [] if value not in found]
                    status = "done" if not remaining else ("failed" if status == "failed" else "pending")
                elif result == "limit":
                    status = "failed" if failed_this_run else "pending"
                    last_error = error
                else:
                    attempts += 0 if failed_this_run else 1
                    status = "abandoned" if attempts >= max_attempts else "failed"
                    last_error = error or "작업 실패"
                db.execute(
                    """UPDATE qa_change_events SET analysis_status=?, remaining_analyses_json=?, finding_ids_json=?, attempts=?,
                       last_error=?, analyzed_run_id=CASE WHEN ?='' THEN analyzed_run_id ELSE ? END, updated_at=? WHERE id=?""",
                    (status, json.dumps(remaining, ensure_ascii=False), json.dumps(found, ensure_ascii=False), attempts,
                     (last_error or "")[:500], run_id, run_id, now_iso(), event["id"]),
                )

    def daily_qa_event_counts(self, product: str | None = None, run_id: str | None = None,
                              since: str | None = None, until: str | None = None) -> dict[str, int]:
        clauses, params = [], []
        for column, value in (("product", product), ("run_id", run_id)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if since:
            clauses.append("created_at>=?")
            params.append(since)
        if until:
            clauses.append("created_at<?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as db:
            rows = db.execute(f"SELECT event_type, COUNT(*) AS n FROM qa_change_events {where} GROUP BY event_type", params).fetchall()
        return {row["event_type"]: row["n"] for row in rows}

    @staticmethod
    def _daily_qa_event_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        for name, default in (("before", "{}"), ("after", "{}"), ("changed_fields", "[]"), ("analyses", "[]"), ("finding_ids", "[]")):
            data[name] = json.loads(data.pop(f"{name}_json") or default)
        data["analysis_required"] = bool(data["analysis_required"])
        raw_remaining = data.pop("remaining_analyses_json", "")
        if raw_remaining:
            data["remaining_analyses"] = json.loads(raw_remaining)
        else:   # 개편 초기 행: 끝나지 않은 이벤트는 모든 분석이 남은 것으로 본다
            data["remaining_analyses"] = list(data["analyses"]) if data["analysis_status"] in ("pending", "failed") else []
        data["status_label"] = EVENT_STATUSES.get(data["analysis_status"], data["analysis_status"])
        return data
