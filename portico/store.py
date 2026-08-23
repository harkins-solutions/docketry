"""Local SQLite store: the pipeline's single source of truth.

Everything lives on the firm's own disk. Ingest is idempotent (keyed on the
raw message hash), attachment bytes go to a content-addressed directory, and
every gate finding and human approval lands in an audit table.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .envelope import Envelope

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  message_id TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  envelope_json TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  filename TEXT NOT NULL,
  content_type TEXT,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  stage TEXT NOT NULL,
  gate_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  stage TEXT NOT NULL,
  gate_id TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  role TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imap_state(
  mailbox TEXT PRIMARY KEY,
  uidvalidity INTEGER,
  last_uid INTEGER NOT NULL DEFAULT 0
);
"""

# Statuses a message row can hold.
OK = "ok"
PENDING_REVIEW = "pending_review"
BLOCKED = "blocked"
DONE = "done"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.attachments_dir = self.root / "attachments"
        self.attachments_dir.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "portico.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- ingest ----------------------------------------------------------
    def ingest(self, env: Envelope, *, first_stage: str) -> int | None:
        """Persist an envelope; return row id, or None if already seen."""
        seen = self.db.execute(
            "SELECT id FROM messages WHERE raw_sha256=?", (env.raw_sha256,)
        ).fetchone()
        if seen:
            return None
        now = utcnow()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO messages(message_id, raw_sha256, source, fetched_at,"
                " envelope_json, stage, status, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    env.message_id,
                    env.raw_sha256,
                    env.source,
                    env.fetched_at,
                    json.dumps(env.to_record()),
                    first_stage,
                    OK,
                    now,
                ),
            )
            msg_id = cur.lastrowid
            for a in env.attachments:
                sub = self.attachments_dir / a.sha256[:2]
                sub.mkdir(exist_ok=True)
                path = sub / f"{a.sha256[:16]}_{a.filename}"
                if not path.exists():
                    path.write_bytes(a.content)
                self.db.execute(
                    "INSERT INTO attachments(message_id, filename, content_type,"
                    " sha256, size, path) VALUES(?,?,?,?,?,?)",
                    (msg_id, a.filename, a.content_type, a.sha256, a.size, str(path)),
                )
        return msg_id

    # -- pipeline state --------------------------------------------------
    def get_message(self, msg_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()

    def set_state(self, msg_id: int, *, stage: str | None = None, status: str | None = None) -> None:
        row = self.get_message(msg_id)
        if row is None:
            raise KeyError(f"no message {msg_id}")
        with self.db:
            self.db.execute(
                "UPDATE messages SET stage=?, status=?, updated_at=? WHERE id=?",
                (stage or row["stage"], status or row["status"], utcnow(), msg_id),
            )

    def add_finding(self, msg_id: int, stage: str, gate_id: str, severity: str, summary: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO findings(message_id, stage, gate_id, severity, summary,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (msg_id, stage, gate_id, severity, summary, utcnow()),
            )

    def add_approval(
        self, msg_id: int, stage: str, gate_id: str, *, approved_by: str, role: str, note: str = ""
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO approvals(message_id, stage, gate_id, approved_by, role,"
                " note, created_at) VALUES(?,?,?,?,?,?,?)",
                (msg_id, stage, gate_id, approved_by, role, note, utcnow()),
            )

    def approval_roles(self, msg_id: int, stage: str, gate_id: str) -> set[str]:
        rows = self.db.execute(
            "SELECT role FROM approvals WHERE message_id=? AND stage=? AND gate_id=?",
            (msg_id, stage, gate_id),
        ).fetchall()
        return {r["role"] for r in rows}

    def findings_for(self, msg_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM findings WHERE message_id=? ORDER BY id", (msg_id,)
        ).fetchall()

    def list_by_status(self, status: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM messages WHERE status=? ORDER BY id", (status,)
        ).fetchall()

    def counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) n FROM messages GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # -- imap cursor -----------------------------------------------------
    def imap_cursor(self, mailbox: str) -> tuple[int | None, int]:
        row = self.db.execute(
            "SELECT uidvalidity, last_uid FROM imap_state WHERE mailbox=?", (mailbox,)
        ).fetchone()
        return (row["uidvalidity"], row["last_uid"]) if row else (None, 0)

    def set_imap_cursor(self, mailbox: str, uidvalidity: int | None, last_uid: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO imap_state(mailbox, uidvalidity, last_uid) VALUES(?,?,?)"
                " ON CONFLICT(mailbox) DO UPDATE SET uidvalidity=excluded.uidvalidity,"
                " last_uid=excluded.last_uid",
                (mailbox, uidvalidity, last_uid),
            )
