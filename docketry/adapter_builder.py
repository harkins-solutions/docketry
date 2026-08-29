"""Turn a real notice email into an adapter, without anyone writing a regex.

Adding a local court is the wall between installing Docketry and using it.
Today it means authoring regular expressions with exactly one capture group
against a template you have to reason about in your head — which is a fair
ask of a developer and an impossible one of the person who actually receives
these emails every morning.

So: paste the email you got. This finds the `Label: value` lines court systems
are built out of, proposes a field for each, and then PROVES the result by
running the real parser over that same email and showing what came out. The
patterns it writes are the same shape as the built-ins, and they are saved
through the same loader, which refuses anything that would not have worked.

Nothing here guesses what a field MEANS. It finds where the values sit; a
person names them and says which ones matter enough to bounce a message when
they go missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .envelope import Envelope, parse_message
from .notices import NOTICE_TYPES, AdapterError, PatternAdapter, _rx

# "Case Number: 8:26-cv-01234" — a label, a colon, a value. Anchored to the
# start of a line because a colon mid-sentence is prose, not a field.
_LABELLED = re.compile(r"^[ \t]*([A-Za-z][A-Za-z /#.'-]{1,38}?)[ \t]*:[ \t]*(\S.*?)[ \t]*$",
                       re.MULTILINE)

# Field labels are short. A colon inside a sentence ("Please do not reply to
# this message: it is unmonitored") is prose, and capturing it would write an
# adapter that bounces every message for a missing "please_do_not_reply".
MAX_LABEL_WORDS = 4

# Labels that are email plumbing or boilerplate rather than case facts.
_NOT_A_FIELD = {
    "from", "to", "cc", "bcc", "sent", "subject", "reply to", "return path",
    "date", "importance", "note", "notice", "warning", "disclaimer",
    "confidentiality notice", "http", "https", "tel", "fax", "phone",
    # Raw-header noise, for when someone pastes a whole .eml into the box.
    "message-id", "mime-version", "content-type", "content-transfer-encoding",
    "received", "authentication-results", "dkim-signature", "return-path",
    "delivered-to", "user-agent", "thread-topic", "thread-index",
    "in-reply-to", "references", "precedence", "auto-submitted",
}

_KNOWN = {
    "case number": "case_number", "case no": "case_number", "case": "case_number",
    "case style": "case_style", "case title": "case_style",
    "court": "court", "judge": "judge", "division": "division",
    "hearing date": "hearing_date", "hearing time": "hearing_time",
    "time": "hearing_time", "location": "location", "courtroom": "courtroom",
    "document": "documents", "documents": "documents",
    "docket text": "docket_text", "document number": "document_number",
    "envelope number": "envelope_number", "filing type": "filing_description",
    "filing description": "filing_description", "status": "status",
    "filed by": "filed_by", "served": "served", "matter": "matter",
}


@dataclass
class Candidate:
    label: str
    field: str          # suggested snake_case name
    value: str          # what would be captured from THIS email
    pattern: str        # the regex that would be written

    @property
    def known(self) -> bool:
        return self.label.strip().lower() in _KNOWN


def _field_name(label: str) -> str:
    key = label.strip().lower()
    if key in _KNOWN:
        return _KNOWN[key]
    name = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return name or "field"


def _pattern_for(label: str) -> str:
    """Anchored, whitespace-tolerant, exactly one capture group."""
    words = [re.escape(w) for w in label.split()]
    return r"^\s*" + r"\s+".join(words) + r"\s*:?\s*(.+)$"


def scan(text: str) -> list[Candidate]:
    """Every labelled line that looks like a field, in the order it appears."""
    out: list[Candidate] = []
    seen: set[str] = set()
    for m in _LABELLED.finditer(text):
        label, value = m.group(1).strip(), m.group(2).strip()
        key = label.lower()
        if (key in _NOT_A_FIELD or key in seen or key.startswith("x-")
                or len(label.split()) > MAX_LABEL_WORDS or len(value) > 200):
            continue
        seen.add(key)
        out.append(Candidate(label=label, field=_field_name(label),
                             value=value, pattern=_pattern_for(label)))
    return out


def scan_email(raw: bytes) -> tuple[Envelope, list[Candidate]]:
    """Scan a whole .eml, and hand back the envelope so match rules can be
    pre-filled from the address and subject the message actually carried."""
    env = parse_message(raw, source="adapter-builder", fetched_at="")
    return env, scan(env.body_text)


def suggest_match(env: Envelope) -> dict:
    """Pre-fill the match rules from the sample, narrowest useful form.

    The sender's DOMAIN, not the full address: court systems send from
    per-case or per-clerk addresses, and an adapter pinned to one of them
    matches exactly one email ever.
    """
    domain = env.from_addr.split("@")[-1].strip().lower() if "@" in env.from_addr else ""
    return {
        "from": f"@{domain}" if domain else "",
        "subject_contains": _stable_subject(env.subject),
    }


def _stable_subject(subject: str) -> str:
    """The part of a subject line that does not change between messages.

    It has to be a CONTIGUOUS substring, not a bag of keywords: matching is a
    plain `in` test, so "notice hearing case" assembled from three separate
    words matches nothing. Cut at the first digit, since what follows is the
    case or envelope number that differs every time.
    """
    head = re.split(r"\d", subject, maxsplit=1)[0]
    head = head.strip().rstrip("-–—:#(,/ ").strip()
    if len(head) < 4:
        head = subject.strip()[:40]
    return head.lower()


# Distinctive words, checked in order. A filing receipt talks about the
# envelope and its acceptance; a hearing notice talks about when to appear.
_TYPE_HINTS = (
    ("filing_receipt", ("envelope number", "filing accepted", "filing rejected",
                        "filing submitted", "returned for correction",
                        "filing status", "e-filing receipt")),
    ("hearing_notice", ("notice of hearing", "hearing date", "docket sounding",
                        "case management conference", "trial setting",
                        "calendar call", "scheduled for hearing")),
)


def suggest_type(env: Envelope) -> str:
    """Guess the notice type from the message, so the form does not default
    to the wrong one.

    The type drives what happens downstream, and a hearing notice silently
    saved as a service notice is a routing bug nobody would think to look for.
    Defaults to service_notice, which is the broadest and least surprising.
    """
    text = f"{env.subject}\n{env.body_text}".lower()
    for ntype, words in _TYPE_HINTS:
        if any(w in text for w in words):
            return ntype
    return "service_notice"


def build(name: str, notice_type: str, match: dict,
          fields: dict[str, str], required: list[str]) -> PatternAdapter:
    """Assemble an adapter, refusing the same things the loader refuses."""
    if not name.strip():
        raise AdapterError("an adapter needs a name")
    if notice_type not in NOTICE_TYPES:
        raise AdapterError(
            f"notice_type must be one of {', '.join(NOTICE_TYPES)}, got {notice_type!r}")
    if not fields:
        raise AdapterError("an adapter with no fields extracts nothing")
    missing = [r for r in required if r not in fields]
    if missing:
        raise AdapterError(
            f"required field(s) {', '.join(missing)} are not among the fields"
            " being extracted — a message would bounce for a value the adapter"
            " never looks for")
    return PatternAdapter(
        name=name.strip(),
        notice_type=notice_type,
        from_endswith=tuple(f for f in [match.get("from", "").strip()] if f),
        subject_contains=tuple(
            s for s in [match.get("subject_contains", "").strip().lower()] if s),
        fields={f: _rx(p, name=name, fname=f) for f, p in fields.items()},
        required=tuple(required),
    )


def to_toml(name: str, notice_type: str, match: dict,
            fields: dict[str, str], required: list[str]) -> str:
    """The adapters.toml block, in the shape the loader reads back."""
    lines = ["", "[[adapter]]", f'name = "{name}"',
             f'notice_type = "{notice_type}"',
             "required = [" + ", ".join(f'"{r}"' for r in required) + "]", ""]
    m = {k: v for k, v in match.items() if v}
    if m:
        lines.append("[adapter.match]")
        for k, v in m.items():
            lines.append(f'{k} = "{v}"')
        lines.append("")
    lines.append("[adapter.fields]")
    for f, p in fields.items():
        # Single quotes: TOML literal strings, so backslashes stay as written.
        lines.append(f"{f} = '{p}'")
    lines.append("")
    return "\n".join(lines)
