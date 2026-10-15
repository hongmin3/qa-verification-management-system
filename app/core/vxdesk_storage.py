"""VXvue desktop enrollment persistence, owned by Storage. Only hashes of tickets and keys are stored."""
from contextlib import closing
from datetime import datetime, timezone
import uuid


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class VxDeskStorageMixin:
    def _initialize_vxdesk(self):
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vxdesk_tickets (
                    ticket_hash TEXT PRIMARY KEY, purpose TEXT NOT NULL, address TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, device_id TEXT);
                CREATE TABLE IF NOT EXISTS vxdesk_devices (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, client TEXT NOT NULL,
                    hostname TEXT NOT NULL, username TEXT NOT NULL, address TEXT NOT NULL,
                    bridge_version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL, last_used_at TEXT NOT NULL, revoked_at TEXT);
            """)

    def vxdesk_add_ticket(self, ticket_hash, purpose, address, expires_at):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM vxdesk_tickets WHERE used_at IS NULL AND expires_at < ?", (now_iso(),))
            db.execute("INSERT INTO vxdesk_tickets(ticket_hash,purpose,address,created_at,expires_at) VALUES(?,?,?,?,?)",
                       (ticket_hash, purpose, address, now_iso(), expires_at))

    def vxdesk_redeem(self, ticket_hash, token_hash, device):
        """Atomically consume a ticket and create its device. Returns (device_id, None) or (None, reason)."""
        now = now_iso()
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT used_at, expires_at FROM vxdesk_tickets WHERE ticket_hash=?", (ticket_hash,)).fetchone()
            if not row:
                return None, "ticket_unknown"
            if row["used_at"]:
                return None, "ticket_used"
            if row["expires_at"] <= now:
                return None, "ticket_expired"
            device_id = uuid.uuid4().hex[:12]
            claimed = db.execute("UPDATE vxdesk_tickets SET used_at=?, device_id=? WHERE ticket_hash=? AND used_at IS NULL",
                                 (now, device_id, ticket_hash)).rowcount
            if claimed != 1:
                return None, "ticket_used"
            db.execute("INSERT INTO vxdesk_devices(id,token_hash,client,hostname,username,address,bridge_version,created_at,last_used_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (device_id, token_hash, device["client"], device["hostname"], device["username"],
                        device["address"], device["bridge_version"], now, now))
        return device_id, None

    def vxdesk_device_by_token(self, token_hash):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT id,status,last_used_at,bridge_version FROM vxdesk_devices WHERE token_hash=?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def vxdesk_touch(self, device_id, address, bridge_version):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE vxdesk_devices SET last_used_at=?, address=?, bridge_version=CASE WHEN ?='' THEN bridge_version ELSE ? END WHERE id=?",
                       (now_iso(), address, bridge_version, bridge_version, device_id))

    def vxdesk_devices(self):
        with closing(self.connect()) as db, db:
            return [dict(r) for r in db.execute(
                "SELECT id,client,hostname,username,address,bridge_version,status,created_at,last_used_at,revoked_at"
                " FROM vxdesk_devices ORDER BY created_at")]

    def vxdesk_revoke(self, device_id):
        with closing(self.connect()) as db, db:
            return db.execute("UPDATE vxdesk_devices SET status='revoked', revoked_at=? WHERE id=? AND status!='revoked'",
                              (now_iso(), device_id)).rowcount == 1
