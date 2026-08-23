"""Drain a dedicated intake mailbox over IMAP.

Plan-1 intake: the firm creates a mailbox that exists solely to be the port,
points forwarding rules at it, and this module reads it with credentials the
firm holds. Strictly read-only: the folder is opened readonly, nothing is
marked, moved, or deleted, and a UID cursor (with UIDVALIDITY tracking) makes
every sweep idempotent.
"""
from __future__ import annotations

import imaplib
from dataclasses import dataclass
from typing import Iterator


@dataclass
class MailboxConfig:
    host: str
    user: str
    password: str
    folder: str = "INBOX"
    port: int = 993


class IntakeMailbox:
    def __init__(self, cfg: MailboxConfig):
        self.cfg = cfg
        self.conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "IntakeMailbox":
        self.conn = imaplib.IMAP4_SSL(self.cfg.host, self.cfg.port)
        self.conn.login(self.cfg.user, self.cfg.password)
        typ, _ = self.conn.select(self.cfg.folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder {self.cfg.folder!r}")
        return self

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None

    @property
    def label(self) -> str:
        return f"{self.cfg.user}/{self.cfg.folder}@{self.cfg.host}"

    def uidvalidity(self) -> int | None:
        typ, data = self.conn.response("UIDVALIDITY")
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError):
            return None

    def new_messages(self, last_uid: int) -> Iterator[tuple[int, bytes]]:
        """Yield (uid, raw_rfc822) for every message with uid > last_uid."""
        typ, data = self.conn.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if typ != "OK" or not data or not data[0]:
            return
        for uid_b in data[0].split():
            uid = int(uid_b)
            if uid <= last_uid:
                # IMAP quirk: "n:*" matches the highest-numbered message even
                # when n exceeds it; skip anything we've already swept.
                continue
            typ, msg_data = self.conn.uid("FETCH", str(uid), "(RFC822)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw = part[1]
                    break
            if raw:
                yield uid, raw
