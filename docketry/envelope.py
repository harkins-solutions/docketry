"""MIME message -> normalized Envelope.

The port's one job: whatever arrives, reduce it to the same provenance-stamped
shape before anything downstream sees it. Parsing is stdlib-only and read-only;
the raw message is never modified, and the raw hash travels with the envelope
so provenance survives every later stage.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import re
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._ ()\[\]-]")


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "head"}
    _BREAK = {"p", "br", "div", "tr", "li", "table"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [ln.strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html
    return parser.text()


def sanitize_filename(name: str) -> str:
    """Keep only the basename and drop anything shell- or path-hostile."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = _FILENAME_SAFE.sub("_", name)
    return name or "attachment.bin"


@dataclass
class Attachment:
    filename: str
    content_type: str
    sha256: str
    size: int
    content: bytes = field(repr=False, compare=False)


@dataclass
class Envelope:
    message_id: str
    from_addr: str
    to: list[str]
    cc: list[str]
    date: str            # ISO 8601, "" when unparseable
    subject: str
    body_text: str
    attachments: list[Attachment]
    raw_sha256: str
    source: str          # intake mailbox this arrived through
    fetched_at: str      # ISO 8601, stamped by the port
    # Threading. Captured at ingest because they cannot be recovered later:
    # a message stored without them can only ever be threaded by subject and
    # participant guesswork, which is materially worse. Cheap now, impossible
    # to backfill.
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)

    @property
    def thread_key(self) -> str:
        """Stable id for the conversation this message belongs to.

        The first entry in References is the root of the thread; failing that
        In-Reply-To names our parent; failing both, a message is its own
        thread root. This is the real header chain, not a subject match —
        subjects get edited, forwarded and reused across unrelated matters.
        """
        if self.references:
            return self.references[0]
        return self.in_reply_to or self.message_id

    def to_record(self) -> dict:
        """JSON-safe form; attachment bytes are stored on disk, not in the row."""
        d = asdict(self)
        for a in d["attachments"]:
            a.pop("content", None)
        return d


def _msgids(value: str | None) -> list[str]:
    """Message-ids out of a References/In-Reply-To header, in order."""
    if not value:
        return []
    return [m.strip("<>") for m in re.findall(r"<[^<>@\s]+@[^<>\s]+>", value)]


def _addresses(msg: EmailMessage, header: str) -> list[str]:
    return [addr for _, addr in getaddresses(msg.get_all(header, [])) if addr]


def _body_text(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    try:
        content = body.get_content()
    except Exception:
        payload = body.get_payload(decode=True) or b""
        content = payload.decode("utf-8", "replace")
    if body.get_content_type() == "text/html":
        return html_to_text(content)
    return content.strip()


def _attachments(msg: EmailMessage) -> list[Attachment]:
    out: list[Attachment] = []
    for part in msg.iter_attachments():
        content = part.get_payload(decode=True)
        if content is None:
            payload = part.get_payload()
            content = payload.encode("utf-8", "replace") if isinstance(payload, str) else b""
        out.append(
            Attachment(
                filename=sanitize_filename(part.get_filename() or "attachment.bin"),
                content_type=part.get_content_type(),
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                content=content,
            )
        )
    return out


def parse_message(raw: bytes, *, source: str, fetched_at: str) -> Envelope:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    raw_sha = hashlib.sha256(raw).hexdigest()

    message_id = (msg.get("Message-ID") or "").strip().strip("<>")
    if not message_id:
        message_id = f"docketry-{raw_sha[:32]}"

    date_iso = ""
    if msg.get("Date"):
        try:
            date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            date_iso = ""

    from_pairs = getaddresses(msg.get_all("From", []))
    from_addr = from_pairs[0][1] if from_pairs else ""

    return Envelope(
        message_id=message_id,
        from_addr=from_addr,
        to=_addresses(msg, "To"),
        cc=_addresses(msg, "Cc"),
        date=date_iso,
        subject=str(msg.get("Subject") or ""),
        body_text=_body_text(msg),
        attachments=_attachments(msg),
        raw_sha256=raw_sha,
        source=source,
        fetched_at=fetched_at,
        in_reply_to=(msg.get("In-Reply-To") or "").strip().strip("<>"),
        references=_msgids(msg.get("References")),
    )
