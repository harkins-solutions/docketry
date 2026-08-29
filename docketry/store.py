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
CREATE TABLE IF NOT EXISTS notices(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id),
  adapter TEXT NOT NULL,
  notice_type TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  missing_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classifications(
  id INTEGER PRIMARY KEY,
  attachment_id INTEGER NOT NULL REFERENCES attachments(id),
  label TEXT NOT NULL,
  tier TEXT NOT NULL,
  applied INTEGER NOT NULL DEFAULT 0,
  applied_by TEXT,
  applied_role TEXT,
  applied_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matters(
  id INTEGER PRIMARY KEY,
  case_number TEXT NOT NULL UNIQUE,      -- normalised; the join key everywhere
  display_name TEXT NOT NULL DEFAULT '',
  matter_type TEXT NOT NULL DEFAULT 'generic',
  stage TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
-- Every stage change, kept forever. A matter's position is a claim about
-- where the work stands, so how it got there has to be answerable.
CREATE TABLE IF NOT EXISTS matter_events(
  id INTEGER PRIMARY KEY,
  matter_id INTEGER NOT NULL REFERENCES matters(id),
  from_stage TEXT NOT NULL,
  to_stage TEXT NOT NULL,
  moved_by TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
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
        self.db = sqlite3.connect(self.root / "docketry.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(attachments)")}
        if "doc_type" not in cols:
            with self.db:
                self.db.execute("ALTER TABLE attachments ADD COLUMN doc_type TEXT")

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

    # -- notices ---------------------------------------------------------
    def add_notice(self, msg_id: int, adapter: str, notice_type: str,
                   fields: dict, missing: list[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO notices(message_id, adapter, notice_type,"
                " fields_json, missing_json, created_at) VALUES(?,?,?,?,?,?)",
                (msg_id, adapter, notice_type, json.dumps(fields),
                 json.dumps(missing), utcnow()),
            )

    def list_notices(self, notice_type: str | None = None) -> list[sqlite3.Row]:
        if notice_type:
            return self.db.execute(
                "SELECT * FROM notices WHERE notice_type=? ORDER BY id",
                (notice_type,),
            ).fetchall()
        return self.db.execute("SELECT * FROM notices ORDER BY id").fetchall()

    # -- classifications (stage-for-approval, fill-only) -----------------
    def stage_classification(self, attachment_id: int, label: str, tier: str) -> int | None:
        """Stage a proposed doc type; skip if an open proposal already exists."""
        row = self.db.execute(
            "SELECT id FROM classifications WHERE attachment_id=? AND applied=0",
            (attachment_id,),
        ).fetchone()
        if row:
            return None
        with self.db:
            cur = self.db.execute(
                "INSERT INTO classifications(attachment_id, label, tier, created_at)"
                " VALUES(?,?,?,?)",
                (attachment_id, label, tier, utcnow()),
            )
        return cur.lastrowid

    def open_classifications(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT c.*, a.filename, a.doc_type FROM classifications c"
            " JOIN attachments a ON a.id = c.attachment_id"
            " WHERE c.applied=0 ORDER BY c.id"
        ).fetchall()

    def apply_classification(self, class_id: int, *, by: str, role: str) -> str:
        """Fill-only: sets attachments.doc_type only when it is NULL."""
        row = self.db.execute(
            "SELECT c.*, a.doc_type FROM classifications c"
            " JOIN attachments a ON a.id = c.attachment_id WHERE c.id=?",
            (class_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"no classification {class_id}")
        if row["applied"]:
            return "already-applied"
        with self.db:
            if row["doc_type"] is None:
                self.db.execute(
                    "UPDATE attachments SET doc_type=? WHERE id=?",
                    (row["label"], row["attachment_id"]),
                )
                outcome = "applied"
            else:
                outcome = f"kept-existing:{row['doc_type']}"
            self.db.execute(
                "UPDATE classifications SET applied=1, applied_by=?, applied_role=?,"
                " applied_at=? WHERE id=?",
                (by, role, utcnow(), class_id),
            )
        return outcome

    def attachments_for(self, msg_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM attachments WHERE message_id=? ORDER BY id", (msg_id,)
        ).fetchall()

    # -- stats: queue health, not people scores --------------------------
    def stats(self, days: int = 7) -> dict:
        cutoff = f"-{days} days"
        q = self.db.execute
        ingested = q(
            "SELECT COUNT(*) n FROM messages WHERE fetched_at >= datetime('now', ?)",
            (cutoff,),
        ).fetchone()["n"]
        by_status = {r["status"]: r["n"] for r in q(
            "SELECT status, COUNT(*) n FROM messages"
            " WHERE fetched_at >= datetime('now', ?) GROUP BY status", (cutoff,))}
        holds_by_gate = {r["gate_id"]: r["n"] for r in q(
            "SELECT gate_id, COUNT(DISTINCT message_id) n FROM findings"
            " WHERE severity='fail' AND created_at >= datetime('now', ?)"
            " GROUP BY gate_id ORDER BY n DESC", (cutoff,))}
        notices_by_type = {r["notice_type"]: r["n"] for r in q(
            "SELECT notice_type, COUNT(*) n FROM notices"
            " WHERE created_at >= datetime('now', ?) GROUP BY notice_type", (cutoff,))}
        drift = q(
            "SELECT COUNT(DISTINCT message_id) n FROM findings"
            " WHERE gate_id='notice-parser' AND severity='fail'"
            " AND created_at >= datetime('now', ?)", (cutoff,),
        ).fetchone()["n"]
        release = q(
            "SELECT AVG((julianday(a.created_at) - julianday(m.fetched_at)) * 24) h"
            " FROM approvals a JOIN messages m ON m.id = a.message_id"
            " WHERE a.created_at >= datetime('now', ?)", (cutoff,),
        ).fetchone()["h"]
        approvals_by_role = {r["role"]: r["n"] for r in q(
            "SELECT role, COUNT(*) n FROM approvals"
            " WHERE created_at >= datetime('now', ?) GROUP BY role", (cutoff,))}
        classifications = {r["k"]: r["n"] for r in q(
            "SELECT CASE WHEN applied=0 THEN 'open' ELSE 'applied' END k, COUNT(*) n"
            " FROM classifications WHERE created_at >= datetime('now', ?)"
            " GROUP BY k", (cutoff,))}
        return {
            "days": days,
            "ingested": ingested,
            "by_status": by_status,
            "holds_by_gate": holds_by_gate,
            "notices_by_type": notices_by_type,
            "template_drift_messages": drift,
            "avg_hours_to_release": round(release, 1) if release is not None else None,
            "approvals_by_role": approvals_by_role,
            "classifications": classifications,
        }

    # -- imap cursor -----------------------------------------------------

    # -- matters ---------------------------------------------------------
    def open_matter(self, case_number: str, *, stage: str,
                    display_name: str = "", matter_type: str = "generic") -> int:
        """Create a matter, or return the existing one for this case number."""
        existing = self.get_matter(case_number)
        if existing:
            return existing["id"]
        now = utcnow()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO matters(case_number, display_name, matter_type,"
                " stage, opened_at, updated_at) VALUES(?,?,?,?,?,?)",
                (case_number, display_name, matter_type, stage, now, now),
            )
        return cur.lastrowid

    def get_matter(self, case_number: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM matters WHERE case_number=?", (case_number,)
        ).fetchone()

    def list_matters(self, stage: str | None = None) -> list[sqlite3.Row]:
        if stage:
            return self.db.execute(
                "SELECT * FROM matters WHERE stage=? ORDER BY updated_at DESC",
                (stage,)).fetchall()
        return self.db.execute(
            "SELECT * FROM matters ORDER BY updated_at DESC").fetchall()

    def move_matter(self, matter_id: int, to_stage: str, *, by: str,
                    role: str = "", note: str = "") -> str:
        """Record the move and the mover. Refuses an unattributed change.

        The engine decides whether a move is allowed; this records that it
        happened. Splitting the two is deliberate — nothing should be able to
        shift a matter without leaving a name behind.
        """
        if not by.strip():
            raise ValueError("a stage change must name who made it")
        row = self.db.execute("SELECT * FROM matters WHERE id=?",
                              (matter_id,)).fetchone()
        if row is None:
            raise ValueError(f"no matter {matter_id}")
        now = utcnow()
        with self.db:
            self.db.execute(
                "INSERT INTO matter_events(matter_id, from_stage, to_stage,"
                " moved_by, role, note, at) VALUES(?,?,?,?,?,?,?)",
                (matter_id, row["stage"], to_stage, by.strip(), role, note, now))
            self.db.execute("UPDATE matters SET stage=?, updated_at=? WHERE id=?",
                            (to_stage, now, matter_id))
        return row["stage"]

    def matter_events(self, matter_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM matter_events WHERE matter_id=? ORDER BY id",
            (matter_id,)).fetchall()

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
