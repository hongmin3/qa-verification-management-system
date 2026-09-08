from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings


class Storage:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (get_settings().root / "data" / "app.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    DEFAULT_PRODUCTS = ("VXvue", "Bellalun Viewer")

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    product TEXT NOT NULL, version TEXT, revision TEXT,
                    name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, result_json TEXT,
                    request_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_evaluations (
                    analysis_id TEXT PRIMARY KEY REFERENCES analyses(id),
                    expected_tc_ids_json TEXT NOT NULL, qa_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_cache (
                    cache_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL, version TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(product, version)
                );
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, synced_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS manual_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL,
                    manual_name TEXT NOT NULL, revision_label TEXT NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 0,
                    parent_revision_id INTEGER REFERENCES manual_revisions(id),
                    baseline_revision_id INTEGER REFERENCES manual_revisions(id),
                    target_version TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'REGISTERED',
                    analysis_id TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, revision_id INTEGER NOT NULL REFERENCES manual_revisions(id),
                    kind TEXT NOT NULL, author TEXT, change_date TEXT, paragraph_index INTEGER,
                    text TEXT NOT NULL, functional INTEGER NOT NULL DEFAULT 1,
                    decision TEXT, confidence REAL, qa_decision TEXT, qa_note TEXT,
                    ai_judgment_json TEXT, source_page INTEGER, review_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, change_id INTEGER NOT NULL REFERENCES manual_changes(id),
                    round_number INTEGER NOT NULL DEFAULT 1, comment_text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN', resolved_in_revision_id INTEGER REFERENCES manual_revisions(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_release_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, revision_id INTEGER NOT NULL REFERENCES manual_revisions(id),
                    source TEXT NOT NULL, category TEXT, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'MISSING_SUSPECTED',
                    matched_change_id INTEGER REFERENCES manual_changes(id),
                    description TEXT NOT NULL DEFAULT '', result_status TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    kind TEXT PRIMARY KEY, last_sent_at TEXT, last_claimed_at TEXT NOT NULL,
                    sent_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS qa_agent_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL REFERENCES analyses(id),
                    claim_kind TEXT NOT NULL, claim_label TEXT NOT NULL,
                    qa_decision TEXT NOT NULL, qa_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(analysis_id, claim_kind, claim_label)
                );
                CREATE TABLE IF NOT EXISTS manual_cross_impacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revision_id INTEGER NOT NULL REFERENCES manual_revisions(id),
                    target_manual TEXT NOT NULL, source_document TEXT NOT NULL,
                    release_source TEXT NOT NULL, category TEXT, title TEXT NOT NULL,
                    evidence_text TEXT NOT NULL DEFAULT '', relevance_score REAL NOT NULL DEFAULT 0,
                    qa_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', created_at TEXT NOT NULL
                );
            """)
            # analyses는 기존 배포에 이미 존재할 수 있어 ADD COLUMN으로 안전하게 확장한다 (SQLite는 컬럼 추가만 지원).
            existing = {row["name"] for row in db.execute("PRAGMA table_info(analyses)")}
            for column, ddl in (
                ("stage", "TEXT"),
                ("stage_index", "INTEGER"),
                ("stage_total", "INTEGER"),
                ("started_at", "TEXT"),
                ("stage_updated_at", "TEXT"),
                ("request_json", "TEXT"),
                ("module", "TEXT"),
            ):
                if column not in existing:
                    db.execute(f"ALTER TABLE analyses ADD COLUMN {column} {ddl}")
            manual_change_columns = {row["name"] for row in db.execute("PRAGMA table_info(manual_changes)")}
            for column, ddl in (
                ("source_page", "INTEGER"),
                ("review_required", "INTEGER NOT NULL DEFAULT 0"),
                ("change_index_in_paragraph", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in manual_change_columns:
                    db.execute(f"ALTER TABLE manual_changes ADD COLUMN {column} {ddl}")
            release_finding_columns = {row["name"] for row in db.execute("PRAGMA table_info(manual_release_findings)")}
            for column, ddl in (("description", "TEXT NOT NULL DEFAULT ''"), ("result_status", "TEXT NOT NULL DEFAULT ''")):
                if column not in release_finding_columns:
                    db.execute(f"ALTER TABLE manual_release_findings ADD COLUMN {column} {ddl}")
            revision_columns = {row["name"] for row in db.execute("PRAGMA table_info(manual_revisions)")}
            if "target_version" not in revision_columns:
                db.execute("ALTER TABLE manual_revisions ADD COLUMN target_version TEXT NOT NULL DEFAULT ''")
            now = datetime.now(timezone.utc).isoformat()
            for name in self.DEFAULT_PRODUCTS:
                db.execute("INSERT OR IGNORE INTO products(name,created_at) VALUES(?,?)", (name, now))

    def list_products(self) -> list[str]:
        with self.connect() as db:
            return [row["name"] for row in db.execute("SELECT name FROM products ORDER BY name")]

    def ensure_product(self, name: str) -> None:
        name = name.strip()
        if not name:
            return
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO products(name,created_at) VALUES(?,?)", (name, datetime.now(timezone.utc).isoformat()))

    def list_versions(self, product: str) -> list[str]:
        with self.connect() as db:
            return [row["version"] for row in db.execute("SELECT version FROM product_versions WHERE product=? ORDER BY version", (product,))]

    def ensure_version(self, product: str, version: str) -> None:
        version = version.strip()
        if not version:
            return
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO product_versions(product,version,created_at) VALUES(?,?,?)",
                (product, version, datetime.now(timezone.utc).isoformat()),
            )

    def active_documents(self, kind: str, product: str) -> list[dict]:
        """제품에 등록된 모든 문서를 반환한다.

        사양서1~5처럼 서로 다른 문서가 같은 제품·버전 아래 여러 개 등록될 수 있으므로,
        새 문서가 추가돼도 이전 문서를 검색 대상에서 제외(레거시 처리)하지 않고 전부 포함한다.
        """
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM documents WHERE kind=? AND product=? ORDER BY id", (kind, product)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_document(self, kind: str, product: str, version: str, revision: str, name: str, path: Path, metadata: dict | None = None) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO documents(kind,product,version,revision,name,path,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (kind, product, version, revision, name, str(path), json.dumps(metadata or {}, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def list_documents(self, kind: str) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM documents WHERE kind=? ORDER BY id DESC", (kind,))]

    def get_document(self, document_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return dict(row) if row else None

    def delete_document(self, document_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM documents WHERE id=?", (document_id,))

    def update_document_metadata(self, document_id: int, metadata: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE documents SET metadata_json=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), document_id))

    def create_analysis(self, analysis_id: str, status: str = "QUEUED", stage_total: int = 0, request: dict | None = None, module: str = "impact_analyzer") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO analyses(id,status,result_json,request_json,error,created_at,updated_at,stage,stage_index,stage_total,started_at,stage_updated_at,module) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (analysis_id, status, None, json.dumps(request, ensure_ascii=False) if request else None, None, now, now, "대기 중", 0, stage_total, now, now, module),
            )

    def update_stage(self, analysis_id: str, index: int, name: str, stage_total: int | None = None) -> None:
        with self.connect() as db:
            if stage_total is None:
                db.execute(
                    "UPDATE analyses SET stage=?, stage_index=?, stage_updated_at=? WHERE id=?",
                    (name, index, datetime.now(timezone.utc).isoformat(), analysis_id),
                )
            else:
                db.execute(
                    "UPDATE analyses SET stage=?, stage_index=?, stage_total=?, stage_updated_at=? WHERE id=?",
                    (name, index, stage_total, datetime.now(timezone.utc).isoformat(), analysis_id),
                )

    def update_analysis(self, analysis_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE analyses SET status=?,result_json=?,error=?,updated_at=? WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, datetime.now(timezone.utc).isoformat(), analysis_id),
            )

    def active_analysis_count(self, module: str | None = None) -> int:
        with self.connect() as db:
            if module:
                return int(db.execute(
                    "SELECT COUNT(*) FROM analyses WHERE status IN ('QUEUED','RUNNING') AND module=?", (module,)
                ).fetchone()[0])
            return int(db.execute(
                "SELECT COUNT(*) FROM analyses WHERE status IN ('QUEUED','RUNNING')"
            ).fetchone()[0])

    def operations_status(self, stale_before_iso: str) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self.connect() as db:
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            active = int(db.execute("SELECT COUNT(*) FROM analyses WHERE status IN ('QUEUED','RUNNING')").fetchone()[0])
            stale = int(db.execute(
                "SELECT COUNT(*) FROM analyses WHERE status='RUNNING' AND COALESCE(stage_updated_at,updated_at)<?",
                (stale_before_iso,),
            ).fetchone()[0])
            failed_24h = int(db.execute(
                "SELECT COUNT(*) FROM analyses WHERE status='FAILED' AND updated_at>=?", (cutoff,)
            ).fetchone()[0])
            last_sync = db.execute("SELECT status,detail,synced_at FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
        return {"database_integrity": integrity, "active_jobs": active, "stale_jobs": stale,
                "failed_jobs_24h": failed_24h, "last_sync": dict(last_sync) if last_sync else None}

    def get_analysis(self, analysis_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        raw_request = value.pop("request_json", None)
        value["request"] = json.loads(raw_request) if raw_request else None
        if value["result_json"]:
            value["result"] = json.loads(value.pop("result_json"))
        else:
            value.pop("result_json")
        return value

    def save_analysis_evaluation(self, analysis_id: str, expected_tc_ids: list[str], qa_note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO analysis_evaluations(analysis_id,expected_tc_ids_json,qa_note,created_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET "
                "expected_tc_ids_json=excluded.expected_tc_ids_json,qa_note=excluded.qa_note,updated_at=excluded.updated_at",
                (analysis_id, json.dumps(expected_tc_ids, ensure_ascii=False), qa_note, now, now),
            )

    def get_analysis_evaluation(self, analysis_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM analysis_evaluations WHERE analysis_id=?", (analysis_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["expected_tc_ids"] = json.loads(value.pop("expected_tc_ids_json"))
        return value

    def list_evaluated_analyses(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT a.id,a.result_json,e.expected_tc_ids_json,e.qa_note,e.updated_at "
                "FROM analysis_evaluations e JOIN analyses a ON a.id=e.analysis_id "
                "WHERE a.status='DONE' AND a.result_json IS NOT NULL ORDER BY e.updated_at DESC"
            ).fetchall()
        return [
            {"analysis_id": row["id"], "result": json.loads(row["result_json"]),
             "expected_tc_ids": json.loads(row["expected_tc_ids_json"]),
             "qa_note": row["qa_note"], "updated_at": row["updated_at"]}
            for row in rows
        ]

    def list_analyses(
        self, limit: int = 100, offset: int = 0,
        status: str | None = None, product: str | None = None, search: str | None = None,
        module: str | None = None,
    ) -> tuple[list[dict], int]:
        """분석 이력을 최신순으로 페이지네이션해 반환한다. 반환값은 (이 페이지의 행,
        필터 적용 후 전체 건수)다. `product`는 등록 당시 `request_json`에 저장된 값을
        `json_extract`로 대조한다(별도 컬럼 없음). `search`는 작업 ID와 변경 문서명에서
        부분일치한다.

        `module`은 기능별 이력을 분리한다. 여러 기능이 같은 `analyses` 테이블을 쓰므로
        필터가 없으면 한 기능의 이력 화면에 다른 기능의 분석이 섞인다. `module` 컬럼이
        없던 시절의 행(NULL)은 그때 유일했던 기능인 `impact_analyzer`로 취급한다."""
        conditions: list[str] = []
        params: list[str] = []
        if module:
            if module == "impact_analyzer":
                conditions.append("(module=? OR module IS NULL)")
            else:
                conditions.append("module=?")
            params.append(module)
        if status:
            conditions.append("status=?")
            params.append(status)
        if product:
            conditions.append("json_extract(request_json, '$.product')=?")
            params.append(product)
        if search:
            conditions.append("(id LIKE ? OR json_extract(result_json, '$.change_file') LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM analyses {where}", params).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM analyses {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            raw = value.pop("result_json")
            raw_request = value.pop("request_json", None)
            value["result"] = json.loads(raw) if raw else None
            value["request"] = json.loads(raw_request) if raw_request else None
            values.append(value)
        return values, total

    def tokens_used_since(self, since_iso: str) -> int:
        """지정 시각 이후 완료된 분석의 total_tokens 합계. 한도 체크용이라 대략치면 충분하다."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT result_json FROM analyses WHERE status='DONE' AND created_at>=? AND result_json IS NOT NULL",
                (since_iso,),
            ).fetchall()
        total = 0
        for row in rows:
            try:
                total += int(json.loads(row["result_json"]).get("token_usage", {}).get("total_tokens", 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return total

    @staticmethod
    def _cache_calls_from_audit(ai_audit: dict) -> tuple[int, int] | None:
        """(hit 수, 전체 호출 수)를 ai_audit 형태에 상관없이 계산한다.

        impact_analyzer는 분석당 Gemini 호출이 1회뿐이라 `cache_hit`(bool) 하나만 기록한다.
        manual_review는 변경 건마다 여러 번 호출하므로 `cache_hit_count`/`request_count`
        누적치를 기록한다(request_count는 캐시 미스=실제 호출 수만 센다, gemini_client.py 참고).
        둘 다 없으면 이 분석은 캐시 통계에서 제외한다(None)."""
        if "cache_hit" in ai_audit:
            return (1, 1) if ai_audit["cache_hit"] else (0, 1)
        if "cache_hit_count" in ai_audit:
            hits = int(ai_audit.get("cache_hit_count", 0) or 0)
            total = hits + int(ai_audit.get("request_count", 0) or 0)
            return (hits, total) if total else None
        return None

    def cost_dashboard_stats(self, days: int = 30) -> dict:
        """비용/캐시 대시보드 집계. analyses에 토큰/캐시 전용 컬럼이 없어 result_json을 그때그때 파싱한다.

        module 컬럼이 없는 과거 행(이 기능 도입 이전 분석)은 result_json에 manual_review 전용
        키(revision_id)가 있는지로 모듈을 추정한다.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, module, request_json, result_json, created_at FROM analyses "
                "WHERE status='DONE' AND created_at>=? AND result_json IS NOT NULL ORDER BY created_at",
                (cutoff,),
            ).fetchall()
        daily: dict[str, dict] = {}
        modules: dict[str, dict] = {}
        cache_hits = 0
        cache_checked = 0
        recent: list[dict] = []
        for row in rows:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            module = row["module"] or ("manual_review" if "revision_id" in result else "impact_analyzer")
            tokens = int((result.get("token_usage") or {}).get("total_tokens", 0) or 0)
            day = row["created_at"][:10]
            day_bucket = daily.setdefault(day, {"date": day, "tokens": 0, "count": 0})
            day_bucket["tokens"] += tokens
            day_bucket["count"] += 1
            module_bucket = modules.setdefault(module, {"tokens": 0, "count": 0})
            module_bucket["tokens"] += tokens
            module_bucket["count"] += 1
            cache_calls = self._cache_calls_from_audit(result.get("ai_audit") or {})
            if cache_calls is not None:
                hits, total = cache_calls
                cache_checked += total
                cache_hits += hits
            request = json.loads(row["request_json"]) if row["request_json"] else {}
            product = request.get("product")
            if not product and module == "manual_review" and result.get("revision_id"):
                revision = self.get_manual_revision(int(result["revision_id"]))
                product = revision["product"] if revision else None
            recent.append({
                "id": row["id"], "module": module, "product": product, "created_at": row["created_at"],
                "tokens": tokens, "cache_hits": cache_calls[0] if cache_calls else None,
                "cache_calls": cache_calls[1] if cache_calls else None,
            })
        return {
            "days": days,
            "daily": sorted(daily.values(), key=lambda item: item["date"]),
            "modules": modules,
            "cache_hit_rate": (cache_hits / cache_checked) if cache_checked else None,
            "cache_sample_size": cache_checked,
            "recent": list(reversed(recent))[:50],
        }

    def fail_incomplete_analyses(self, error: str = "서버 재시작으로 분석이 중단되었습니다.") -> int:
        """A process restart cannot resume in-memory BackgroundTasks safely."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE analyses SET status='FAILED',error=?,updated_at=? WHERE status IN ('QUEUED','RUNNING')",
                (error, now),
            )
            return cursor.rowcount

    def fail_running_analyses(self, error: str = "서버 재시작으로 실행 중 분석이 중단되었습니다. 재실행할 수 있습니다.") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE analyses SET status='FAILED',error=?,updated_at=? WHERE status='RUNNING'", (error, now)
            )
            return cursor.rowcount

    def queued_analyses(self, module: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,request_json FROM analyses WHERE status='QUEUED' AND module=? ORDER BY created_at", (module,)
            ).fetchall()
        return [{"id": row["id"], "request": json.loads(row["request_json"] or "{}") } for row in rows]

    def cache_get(self, key: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT response_json FROM ai_cache WHERE cache_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_set(self, key: str, value: dict) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO ai_cache VALUES(?,?,?)", (key, json.dumps(value, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def sync_start(self, product: str, kind: str, source: str) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO sync_log(product,kind,source,synced_at,status,detail) VALUES(?,?,?,?,?,?)",
                (product, kind, source, datetime.now(timezone.utc).isoformat(), "RUNNING", ""),
            )
            return int(cursor.lastrowid)

    def sync_finish(self, sync_id: int, status: str, detail: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sync_log SET status=?, detail=?, synced_at=? WHERE id=?",
                (status, detail, datetime.now(timezone.utc).isoformat(), sync_id),
            )

    def is_sync_running(self, product: str, kind: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT status FROM sync_log WHERE product=? AND kind=? ORDER BY id DESC LIMIT 1", (product, kind)
            ).fetchone()
        return bool(row) and row["status"] == "RUNNING"

    def latest_sync(self, product: str, kind: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sync_log WHERE product=? AND kind=? ORDER BY id DESC LIMIT 1", (product, kind)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # manual_review: 매뉴얼 개정 검증 (Revision Lineage / Track Changes / QA Comment)
    # ------------------------------------------------------------------

    def add_manual_revision(
        self,
        product: str,
        manual_name: str,
        revision_label: str,
        source_path: Path,
        round_number: int = 0,
        parent_revision_id: int | None = None,
        baseline_revision_id: int | None = None,
        analysis_id: str | None = None,
        status: str = "REGISTERED",
        target_version: str = "",
    ) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO manual_revisions(product,manual_name,revision_label,round_number,parent_revision_id,baseline_revision_id,target_version,source_path,status,analysis_id,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (product, manual_name, revision_label, round_number, parent_revision_id, baseline_revision_id, target_version, str(source_path), status, analysis_id, datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def get_manual_revision(self, revision_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM manual_revisions WHERE id=?", (revision_id,)).fetchone()
            return dict(row) if row else None

    def list_manual_revisions(self, product: str | None = None) -> list[dict]:
        with self.connect() as db:
            if product:
                rows = db.execute("SELECT * FROM manual_revisions WHERE product=? ORDER BY id DESC", (product,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM manual_revisions ORDER BY id DESC").fetchall()
            return [dict(row) for row in rows]

    def update_manual_revision_status(self, revision_id: int, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE manual_revisions SET status=? WHERE id=?", (status, revision_id))

    def add_manual_change(
        self,
        revision_id: int,
        kind: str,
        author: str,
        change_date: str,
        paragraph_index: int,
        text: str,
        functional: bool = True,
        source_page: int | None = None,
        review_required: bool = False,
        change_index_in_paragraph: int = 0,
    ) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO manual_changes(revision_id,kind,author,change_date,paragraph_index,text,functional,source_page,review_required,change_index_in_paragraph,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (revision_id, kind, author, change_date, paragraph_index, text, int(functional), source_page, int(review_required), change_index_in_paragraph, datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def update_manual_change_judgment(self, change_id: int, decision: str, confidence: float, ai_judgment: dict) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE manual_changes SET decision=?, confidence=?, ai_judgment_json=? WHERE id=?",
                (decision, confidence, json.dumps(ai_judgment, ensure_ascii=False), change_id),
            )

    def update_manual_change_qa_decision(self, change_id: int, qa_decision: str, qa_note: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE manual_changes SET qa_decision=?, qa_note=? WHERE id=?", (qa_decision, qa_note, change_id))

    def get_manual_change(self, change_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM manual_changes WHERE id=?", (change_id,)).fetchone()
        if not row:
            return None
        value = dict(row)
        if value.get("ai_judgment_json"):
            value["ai_judgment"] = json.loads(value.pop("ai_judgment_json"))
        else:
            value["ai_judgment"] = None
            value.pop("ai_judgment_json", None)
        return value

    def list_manual_changes(self, revision_id: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manual_changes WHERE revision_id=? ORDER BY paragraph_index, id", (revision_id,)).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            raw = value.pop("ai_judgment_json")
            value["ai_judgment"] = json.loads(raw) if raw else None
            values.append(value)
        return values

    def add_manual_comment(self, change_id: int, round_number: int, comment_text: str, status: str = "OPEN") -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO manual_comments(change_id,round_number,comment_text,status,created_at) VALUES(?,?,?,?,?)",
                (change_id, round_number, comment_text, status, datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def update_manual_comment_status(self, comment_id: int, status: str, resolved_in_revision_id: int | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE manual_comments SET status=?, resolved_in_revision_id=? WHERE id=?",
                (status, resolved_in_revision_id, comment_id),
            )

    def get_manual_comment(self, comment_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT manual_comments.*, manual_changes.revision_id AS source_revision_id, "
                "manual_changes.text AS change_text FROM manual_comments "
                "JOIN manual_changes ON manual_changes.id=manual_comments.change_id "
                "WHERE manual_comments.id=?",
                (comment_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_open_comments_for_revision(self, revision_id: int) -> list[dict]:
        """revision_id와 그 조상 Round에서 아직 해결되지 않은 QA Comment 목록."""
        with self.connect() as db:
            lineage_ids: list[int] = []
            current_id: int | None = revision_id
            while current_id:
                lineage_ids.append(current_id)
                row = db.execute("SELECT parent_revision_id FROM manual_revisions WHERE id=?", (current_id,)).fetchone()
                current_id = row["parent_revision_id"] if row else None
            if not lineage_ids:
                return []
            placeholders = ",".join("?" for _ in lineage_ids)
            rows = db.execute(
                "SELECT manual_comments.*, manual_changes.text AS change_text FROM manual_comments "
                "JOIN manual_changes ON manual_changes.id = manual_comments.change_id "
                f"WHERE manual_changes.revision_id IN ({placeholders}) "
                "AND manual_comments.status IN ('OPEN','NOT_RESOLVED','REOPENED') ORDER BY manual_comments.id",
                lineage_ids,
            ).fetchall()
            return [dict(row) for row in rows]

    def add_release_finding(self, revision_id: int, source: str, category: str, title: str, status: str = "MISSING_SUSPECTED", matched_change_id: int | None = None, description: str = "", result_status: str = "") -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO manual_release_findings(revision_id,source,category,title,status,matched_change_id,description,result_status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (revision_id, source, category, title, status, matched_change_id, description, result_status, datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def list_release_findings(self, revision_id: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM manual_release_findings WHERE revision_id=? ORDER BY id", (revision_id,)).fetchall()
            return [dict(row) for row in rows]

    def add_cross_manual_impact(
        self, revision_id: int, target_manual: str, source_document: str, release_source: str,
        category: str, title: str, evidence_text: str, relevance_score: float,
    ) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO manual_cross_impacts(revision_id,target_manual,source_document,release_source,category,title,evidence_text,relevance_score,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (revision_id, target_manual, source_document, release_source, category, title, evidence_text, relevance_score, datetime.now(timezone.utc).isoformat()),
            )
            return int(cursor.lastrowid)

    def list_cross_manual_impacts(self, revision_id: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM manual_cross_impacts WHERE revision_id=? ORDER BY relevance_score DESC,id",
                (revision_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_cross_manual_impact_status(self, impact_id: int, qa_status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE manual_cross_impacts SET qa_status=? WHERE id=?", (qa_status, impact_id))

    def latest_manual_revisions_by_name(self, product: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT r.* FROM manual_revisions r JOIN (SELECT manual_name,MAX(id) id FROM manual_revisions WHERE product=? AND status IN ('REVIEWED','BASELINE') GROUP BY manual_name) latest ON latest.id=r.id ORDER BY r.manual_name",
                (product,),
            ).fetchall()
            return [dict(row) for row in rows]

    # --- QA Agent 승인 (규칙 §19 QA 승인 원칙) --------------------------------
    # AI 판정은 결과에 그대로 남고, QA 의 승인/거절/수정은 별도 행으로 쌓인다. AI 결과를
    # 덮어쓰지 않는 이유는 규칙 §20 이 "AI 결과 / QA 수정 결과 / QA 승인·거절"을 각각
    # 저장해 Rule·Prompt 개선에 쓰라고 정하고 있기 때문이다.

    def save_qa_agent_approval(self, analysis_id: str, claim_kind: str, claim_label: str, qa_decision: str, qa_note: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO qa_agent_approvals(analysis_id,claim_kind,claim_label,qa_decision,qa_note,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(analysis_id,claim_kind,claim_label) DO UPDATE SET qa_decision=excluded.qa_decision, qa_note=excluded.qa_note, updated_at=excluded.updated_at",
                (analysis_id, claim_kind, claim_label, qa_decision, qa_note, now, now),
            )

    def list_qa_agent_approvals(self, analysis_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM qa_agent_approvals WHERE analysis_id=? ORDER BY claim_kind,claim_label", (analysis_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def qa_agent_approval_map(self, analysis_id: str) -> dict[tuple[str, str], dict]:
        """`(claim_kind, claim_label)` → 승인 행. 화면이 판정 옆에 QA 결정을 붙일 때 쓴다."""
        return {(row["claim_kind"], row["claim_label"]): row for row in self.list_qa_agent_approvals(analysis_id)}

    def qa_agent_approval_stats(self, days: int = 90) -> dict:
        """AI 판정 대비 QA 결정 분포. 규칙 §21 품질 지표(AI 결과 QA 수정률)의 원천이다."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as db:
            rows = db.execute(
                "SELECT qa_decision, COUNT(*) n FROM qa_agent_approvals WHERE created_at>=? GROUP BY qa_decision", (since,)
            ).fetchall()
        counts = {row["qa_decision"]: row["n"] for row in rows}
        total = sum(counts.values())
        changed = sum(count for decision, count in counts.items() if decision in ("REJECTED", "EDITED"))
        return {"days": days, "total": total, "by_decision": counts, "revision_rate": round(changed / total, 4) if total else 0.0}

    # --- 알림 쿨다운 (app/core/notifier.py) -----------------------------------
    # 크레딧이 소진되면 실행마다 같은 오류가 난다. 쿨다운을 두지 않으면 메일이 폭주한다.

    def claim_notification(self, kind: str, cooldown_before_iso: str) -> bool:
        """이 종류의 알림을 보낼 차례인지 확인하고 **동시에 선점**한다.

        확인과 기록을 한 트랜잭션에서 하는 이유는 분석이 동시에 여러 건 실패할 수 있어서다.
        읽고 나서 따로 쓰면 두 건이 같은 틈에 들어와 메일이 두 번 나간다.

        `last_claimed_at` 을 갱신하므로 발송이 실패해도 쿨다운이 걸린다 — 메일 서버가 죽어
        있을 때 매 실행마다 SMTP 접속을 시도하지 않게 하기 위함이다.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            row = db.execute("SELECT last_claimed_at FROM notifications WHERE kind=?", (kind,)).fetchone()
            if row is not None and row["last_claimed_at"] > cooldown_before_iso:
                return False
            db.execute(
                "INSERT INTO notifications(kind,last_sent_at,last_claimed_at,sent_count) VALUES(?,NULL,?,0) "
                "ON CONFLICT(kind) DO UPDATE SET last_claimed_at=excluded.last_claimed_at",
                (kind, now),
            )
        return True

    def mark_notification_sent(self, kind: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE notifications SET last_sent_at=?, sent_count=sent_count+1 WHERE kind=?", (now, kind)
            )

    def notification_log(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM notifications ORDER BY kind").fetchall()]
