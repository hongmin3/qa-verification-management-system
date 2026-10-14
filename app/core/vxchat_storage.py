"""VXvue chat persistence, owned by Storage; no module opens SQLite directly."""
from contextlib import closing
from datetime import datetime, timezone
import json
import uuid


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class VxChatStorageMixin:
    def vxchat_last_complete_sync(self):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT synced_at FROM sync_log WHERE product='VXvue' AND kind='product_knowledge' AND status='SUCCESS' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def _initialize_vxchat(self):
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vxchat_conversations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    title_source TEXT NOT NULL DEFAULT 'auto');
                CREATE INDEX IF NOT EXISTS vxchat_owner ON vxchat_conversations(owner,updated_at);
                CREATE TABLE IF NOT EXISTS vxchat_messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, citations_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS vxchat_messages_order ON vxchat_messages(conversation_id,created_at);
                CREATE TABLE IF NOT EXISTS vxchat_attachments (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
                    name TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS vxchat_jobs (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_message_id TEXT NOT NULL,
                    assistant_message_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', knowledge_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS vxchat_one_active ON vxchat_jobs(conversation_id)
                    WHERE status IN ('QUEUED','RUNNING');
            """)
            if "title_source" not in {r[1] for r in db.execute("PRAGMA table_info(vxchat_conversations)")}:
                db.execute("ALTER TABLE vxchat_conversations ADD COLUMN title_source TEXT NOT NULL DEFAULT 'auto'")

    def vxchat_create(self, owner, title="새 대화"):
        value = {"id": uuid.uuid4().hex, "owner": owner, "title": title[:100], "created_at": now_iso()}
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO vxchat_conversations(id,owner,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                       (value["id"], owner, value["title"], value["created_at"], value["created_at"]))
        return value

    def vxchat_list(self, owner):
        with closing(self.connect()) as db, db:
            return [dict(r) for r in db.execute("SELECT id,title,created_at,updated_at FROM vxchat_conversations WHERE owner=? ORDER BY updated_at DESC", (owner,))]

    def vxchat_get(self, owner, conversation_id):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT * FROM vxchat_conversations WHERE id=? AND owner=?", (conversation_id, owner)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["messages"] = []
            for message in db.execute("SELECT * FROM vxchat_messages WHERE conversation_id=? ORDER BY created_at,rowid", (conversation_id,)):
                value = dict(message)
                value["citations"] = json.loads(value.pop("citations_json"))
                value["attachments"] = [dict(a) for a in db.execute("SELECT * FROM vxchat_attachments WHERE message_id=?", (value["id"],))]
                result["messages"].append(value)
            result["jobs"] = [dict(r) for r in db.execute("SELECT * FROM vxchat_jobs WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]
            return result

    def vxchat_rename(self, owner, conversation_id, title):
        with closing(self.connect()) as db, db:
            return db.execute("UPDATE vxchat_conversations SET title=?,title_source='user',updated_at=? WHERE id=? AND owner=?",
                              (title[:100], now_iso(), conversation_id, owner)).rowcount > 0

    def vxchat_set_ai_title(self, conversation_id, title):
        with closing(self.connect()) as db, db:
            return db.execute("UPDATE vxchat_conversations SET title=?,title_source='ai' WHERE id=? AND title_source!='user'",
                              (title[:100], conversation_id)).rowcount > 0

    def vxchat_submit(self, owner, conversation_id, text, attachments, max_pending=20):
        if len(attachments) > 10:
            raise ValueError("첨부파일은 최대 10개입니다.")
        job_id, user_id, assistant_id = (uuid.uuid4().hex for _ in range(3))
        now = now_iso()
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            chat = db.execute("SELECT * FROM vxchat_conversations WHERE id=? AND owner=?", (conversation_id, owner)).fetchone()
            if not chat:
                raise LookupError("대화를 찾을 수 없습니다.")
            if db.execute("SELECT 1 FROM vxchat_jobs WHERE conversation_id=? AND status IN ('QUEUED','RUNNING')", (conversation_id,)).fetchone():
                raise ValueError("이 대화의 답변이 진행 중입니다.")
            if db.execute("SELECT count(*) FROM vxchat_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0] >= max_pending:
                raise ValueError("대기 요청이 많습니다. 잠시 후 다시 요청하세요.")
            for message_id, role, content in ((user_id, "user", text), (assistant_id, "assistant", "")):
                db.execute("INSERT INTO vxchat_messages(id,conversation_id,role,content,created_at) VALUES(?,?,?,?,?)", (message_id, conversation_id, role, content, now))
            for a in attachments:
                db.execute("INSERT INTO vxchat_attachments VALUES(?,?,?,?,?,?)", (a["id"], conversation_id, user_id, a["name"], a["path"], a["bytes"]))
            db.execute("INSERT INTO vxchat_jobs(id,conversation_id,user_message_id,assistant_message_id,status,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, conversation_id, user_id, assistant_id, "QUEUED", "대기 중", now, now))
            db.execute("INSERT INTO analyses(id,status,request_json,created_at,updated_at,module) VALUES(?,?,?,?,?,?)",
                       (job_id, "QUEUED", json.dumps({"product": "VXvue"}), now, now, "vxvue_chatbot"))
            title = (text.strip() or (attachments[0]["name"] if attachments else "새 대화"))[:60]
            db.execute("UPDATE vxchat_conversations SET updated_at=?,title=CASE WHEN title='새 대화' THEN ? ELSE title END WHERE id=?", (now, title, conversation_id))
        return self.vxchat_job(owner, job_id)

    def vxchat_job(self, owner, job_id):
        with closing(self.connect()) as db, db:
            r = db.execute("SELECT j.* FROM vxchat_jobs j JOIN vxchat_conversations c ON c.id=j.conversation_id WHERE j.id=? AND c.owner=?", (job_id, owner)).fetchone()
            return dict(r) if r else None

    def vxchat_pending(self):
        with closing(self.connect()) as db, db:
            return [dict(r) for r in db.execute("SELECT j.*,c.owner FROM vxchat_jobs j JOIN vxchat_conversations c ON c.id=j.conversation_id WHERE j.status='QUEUED' ORDER BY j.created_at")]

    def vxchat_start(self, job_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE vxchat_jobs SET status='RUNNING',stage='자료 검색 준비',updated_at=? WHERE id=? AND status='QUEUED'", (now_iso(), job_id)).rowcount
            if changed:
                db.execute("UPDATE analyses SET status='RUNNING',updated_at=? WHERE id=?", (now_iso(), job_id))
            return bool(changed)

    def vxchat_global_busy(self, max_running=2):
        with closing(self.connect()) as db, db:
            total = db.execute("SELECT count(*) FROM analyses WHERE status='RUNNING'").fetchone()[0]
            total += db.execute("SELECT count(*) FROM daily_qa_runs WHERE status='RUNNING'").fetchone()[0]
        return total >= max_running

    def vxchat_progress(self, job_id, stage, content=None, knowledge=None):
        with closing(self.connect()) as db, db:
            job = db.execute("SELECT * FROM vxchat_jobs WHERE id=? AND status='RUNNING'", (job_id,)).fetchone()
            if not job:
                return False
            db.execute("UPDATE vxchat_jobs SET stage=?,updated_at=? WHERE id=?", (stage, now_iso(), job_id))
            if content is not None:
                db.execute("UPDATE vxchat_messages SET content=? WHERE id=?", (content, job["assistant_message_id"]))
            if knowledge is not None:
                db.execute("UPDATE vxchat_jobs SET knowledge_json=? WHERE id=?", (json.dumps(knowledge, ensure_ascii=False), job_id))
            return True

    def vxchat_finish(self, job_id, status, content, *, usage=None, citations=None, error="", model="", masking=None, metrics=None):
        if status not in ("DONE", "FAILED", "CANCELLED"):
            raise ValueError("잘못된 작업 상태입니다.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM vxchat_jobs WHERE id=? AND status IN ('QUEUED','RUNNING')", (job_id,)).fetchone()
            if not job:
                return False
            db.execute("UPDATE vxchat_jobs SET status=?,stage=?,error=?,updated_at=? WHERE id=?", (status, {"DONE": "완료", "FAILED": "실패", "CANCELLED": "중지됨"}[status], error, now_iso(), job_id))
            db.execute("UPDATE vxchat_messages SET content=?,citations_json=? WHERE id=?", (content, json.dumps(citations or [], ensure_ascii=False), job["assistant_message_id"]))
            db.execute("UPDATE vxchat_conversations SET updated_at=? WHERE id=?", (now_iso(), job["conversation_id"]))
            result = {"token_usage": usage or {}, "model_routing": {"model": model}, "ai_audit": {"request_count": 1, "cache_hit_count": 0, "masking": masking or {}}}
            result["cli_usage"] = metrics or {}
            db.execute("UPDATE analyses SET status=?,result_json=?,error=?,updated_at=? WHERE id=?", ("DONE" if status == "DONE" else "FAILED", json.dumps(result), error, now_iso(), job_id))
            return True

    def vxchat_cancel(self, owner, job_id):
        if self.vxchat_job(owner, job_id) is None:
            return False
        return self.vxchat_finish(job_id, "CANCELLED", "답변 생성을 중지했습니다.")

    def vxchat_recover(self):
        with closing(self.connect()) as db, db:
            ids = [r[0] for r in db.execute("SELECT id FROM vxchat_jobs WHERE status='RUNNING'")]
        for job_id in ids:
            self.vxchat_finish(job_id, "FAILED", "서버 재시작으로 답변이 중단되었습니다. 다시 질문해주세요.", error="서버 재시작")
        return len(ids)

    def vxchat_attachment(self, owner, attachment_id):
        with closing(self.connect()) as db, db:
            r = db.execute("SELECT a.* FROM vxchat_attachments a JOIN vxchat_conversations c ON c.id=a.conversation_id WHERE a.id=? AND c.owner=?", (attachment_id, owner)).fetchone()
            return dict(r) if r else None

    def vxchat_delete(self, owner, conversation_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM vxchat_conversations WHERE id=? AND owner=?", (conversation_id, owner)).fetchone():
                return None
            if db.execute("SELECT 1 FROM vxchat_jobs WHERE conversation_id=? AND status IN ('QUEUED','RUNNING')", (conversation_id,)).fetchone():
                raise ValueError("진행 중인 답변을 먼저 중지하세요.")
            paths = [r[0] for r in db.execute("SELECT path FROM vxchat_attachments WHERE conversation_id=?", (conversation_id,))]
            db.execute("DELETE FROM vxchat_attachments WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM vxchat_messages WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM vxchat_jobs WHERE conversation_id=?", (conversation_id,))
            db.execute("DELETE FROM vxchat_conversations WHERE id=?", (conversation_id,))
            return {"paths": paths}
