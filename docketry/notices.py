"""Court-notice parsing: adapters turn notification emails into typed notices.

An adapter recognizes one source system's notification email (by sender and
format fingerprint) and extracts its facts into a common schema. Built-in
adapters cover the big systems; firms add their own local courts as TOML
config — no code — and firm adapters are consulted first so a local template
can override a built-in.

Design rules, enforced here:
- Format parsing only. An adapter that matches but cannot extract a field it
  declared required reports the miss loudly (the gate bounces the message)
  instead of passing a notice through with silent holes.
- Capture, never consume. A PACER NEF's one-time "free look" document link is
  extracted as data; nothing in this module fetches URLs, ever — an automated
  fetch would silently burn the single free access.
- Extraction is open; judgment is not. A hearing notice yields date, time,
  judge, location. What lands on whose calendar is a human decision that
  happens downstream of this code.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .envelope import Envelope

NOTICE_TYPES = ("service_notice", "filing_receipt", "hearing_notice")


class AdapterError(ValueError):
    """A misdeclared adapter refuses to load; it never half-parses."""


@dataclass
class NoticeResult:
    adapter: str
    notice_type: str
    fields: dict
    missing: list[str] = field(default_factory=list)


def _searchable(env: Envelope) -> str:
    return f"{env.subject}\n{env.body_text}"


@dataclass
class PatternAdapter:
    """One source system: match rules + anchored field patterns.

    Both built-in and TOML-defined adapters are instances of this class, so
    a firm's config adapter has exactly the powers of a shipped one.
    """

    name: str
    notice_type: str
    from_endswith: tuple[str, ...] = ()
    subject_contains: tuple[str, ...] = ()
    body_contains: tuple[str, ...] = ()
    fields: dict[str, re.Pattern] = field(default_factory=dict)
    list_fields: dict[str, re.Pattern] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.notice_type not in NOTICE_TYPES:
            raise AdapterError(
                f"adapter '{self.name}': notice_type '{self.notice_type}'"
                f" is not one of {NOTICE_TYPES}"
            )
        if not (self.from_endswith or self.subject_contains or self.body_contains):
            raise AdapterError(f"adapter '{self.name}' declares no match rules")
        for fname in self.required:
            if fname not in self.fields and fname not in self.list_fields:
                raise AdapterError(
                    f"adapter '{self.name}' requires field '{fname}' but has no pattern for it"
                )

    def match(self, env: Envelope) -> bool:
        sender = env.from_addr.lower()
        if self.from_endswith and not any(sender.endswith(s.lower()) for s in self.from_endswith):
            return False
        subject = env.subject.lower()
        if self.subject_contains and not any(s.lower() in subject for s in self.subject_contains):
            return False
        if self.body_contains:
            body = env.body_text.lower()
            if not any(s.lower() in body for s in self.body_contains):
                return False
        return True

    def extract(self, env: Envelope) -> NoticeResult:
        text = _searchable(env)
        out: dict = {}
        for fname, pat in self.fields.items():
            m = pat.search(text)
            if m:
                out[fname] = m.group(1).strip()
        for fname, pat in self.list_fields.items():
            hits = [h.strip() for h in pat.findall(text) if h.strip()]
            if hits:
                out[fname] = hits
        missing = [f for f in self.required if f not in out]
        return NoticeResult(
            adapter=self.name, notice_type=self.notice_type, fields=out, missing=missing
        )


def _rx(pattern: str, *, name: str, fname: str) -> re.Pattern:
    try:
        pat = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        raise AdapterError(f"adapter '{name}': field '{fname}' pattern does not compile: {e}")
    if pat.groups != 1:
        raise AdapterError(
            f"adapter '{name}': field '{fname}' pattern must have exactly one capture group"
        )
    return pat


# ---------------------------------------------------------------------------
# Built-in adapters. Template fingerprints for the major systems; formats
# vary by district/circuit, so patterns are anchored and conservative, and a
# firm TOML adapter can always override (firm adapters run first).
# ---------------------------------------------------------------------------

def builtin_adapters() -> list[PatternAdapter]:
    return [
        PatternAdapter(
            name="fl-eportal-service",
            notice_type="service_notice",
            from_endswith=("@myflcourtaccess.com",),
            subject_contains=("service of court document", "notice of service"),
            fields={
                "case_number": _rx(r"^\s*Case\s*(?:Number|#)\s*:?\s*([A-Z0-9-]{6,25})",
                                   name="fl-eportal-service", fname="case_number"),
                "case_style": _rx(r"^\s*Case Style\s*:?\s*(.+)",
                                  name="fl-eportal-service", fname="case_style"),
                "court": _rx(r"^\s*Court\s*:?\s*(.+)", name="fl-eportal-service", fname="court"),
            },
            list_fields={
                "documents": _rx(r"^\s*Document(?:s)?\s*:?\s*(.+)$",
                                 name="fl-eportal-service", fname="documents"),
                "served": _rx(r"^\s*Served\s*:?\s*(.+@.+)$",
                              name="fl-eportal-service", fname="served"),
            },
            required=("case_number",),
        ),
        PatternAdapter(
            name="pacer-nef",
            notice_type="service_notice",
            from_endswith=("uscourts.gov",),
            subject_contains=("activity in case",),
            fields={
                "case_number": _rx(r"Activity in Case\s+(\S+)",
                                   name="pacer-nef", fname="case_number"),
                "case_name": _rx(r"Activity in Case\s+\S+\s+(.+?)(?:\s{2,}|$)",
                                 name="pacer-nef", fname="case_name"),
                "docket_text": _rx(r"^\s*Docket Text\s*:?\s*(.+)",
                                   name="pacer-nef", fname="docket_text"),
                "document_number": _rx(r"^\s*Document Number\s*:?\s*(\d+)",
                                       name="pacer-nef", fname="document_number"),
                # Captured as data only. NEVER fetched: the NEF link is the
                # recipient's one-time free look, and an automated fetch
                # silently consumes it.
                "document_link": _rx(r"(https?://ecf\.\S*uscourts\.gov/\S+)",
                                     name="pacer-nef", fname="document_link"),
            },
            required=("case_number",),
        ),
        PatternAdapter(
            name="jacs-hearing",
            notice_type="hearing_notice",
            body_contains=("judicial automated calendaring system", "jacs"),
            fields={
                "hearing_date": _rx(r"^\s*(?:Hearing\s+)?Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
                                    name="jacs-hearing", fname="hearing_date"),
                "hearing_time": _rx(r"^\s*Time\s*:?\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)",
                                    name="jacs-hearing", fname="hearing_time"),
                "judge": _rx(r"^\s*Judge\s*:?\s*(.+)", name="jacs-hearing", fname="judge"),
                "case_number": _rx(r"^\s*Case\s*(?:Number|#|No\.?)\s*:?\s*([A-Z0-9-]{6,25})",
                                   name="jacs-hearing", fname="case_number"),
                "matter": _rx(r"^\s*Matter\s*:?\s*(.+)", name="jacs-hearing", fname="matter"),
            },
            required=("hearing_date",),
        ),
        PatternAdapter(
            name="jaws-hearing",
            notice_type="hearing_notice",
            body_contains=("judicial automated workflow system", "jaws"),
            fields={
                "hearing_date": _rx(r"^\s*(?:Hearing\s+)?Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
                                    name="jaws-hearing", fname="hearing_date"),
                "hearing_time": _rx(r"^\s*Time\s*:?\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)",
                                    name="jaws-hearing", fname="hearing_time"),
                "judge": _rx(r"^\s*Judge\s*:?\s*(.+)", name="jaws-hearing", fname="judge"),
                "case_number": _rx(r"^\s*Case\s*(?:Number|#|No\.?)\s*:?\s*([A-Z0-9-]{6,25})",
                                   name="jaws-hearing", fname="case_number"),
            },
            required=("hearing_date",),
        ),
        PatternAdapter(
            name="efile-receipt",
            notice_type="filing_receipt",
            from_endswith=("efilingmail.tylertech.cloud", "tylerhost.net"),
            subject_contains=("filing",),
            fields={
                "envelope_number": _rx(r"^\s*Envelope\s*(?:Number|#)\s*:?\s*(\d+)",
                                       name="efile-receipt", fname="envelope_number"),
                "case_number": _rx(r"^\s*Case\s*(?:Number|#)\s*:?\s*([A-Z0-9-]{6,25})",
                                   name="efile-receipt", fname="case_number"),
                "status": _rx(r"\bFiling\s+(Accepted|Submitted|Rejected|Returned)\b",
                              name="efile-receipt", fname="status"),
                "filing_description": _rx(r"^\s*Filing\s+(?:Description|Type)\s*:?\s*(.+)",
                                          name="efile-receipt", fname="filing_description"),
            },
            required=("envelope_number",),
        ),
    ]


# ---------------------------------------------------------------------------
# Firm-defined adapters: TOML in, PatternAdapter out, validated at load.
# ---------------------------------------------------------------------------

def load_adapters_file(path: str | Path) -> list[PatternAdapter]:
    data = tomllib.loads(Path(path).read_text())
    adapters: list[PatternAdapter] = []
    for i, a in enumerate(data.get("adapter", [])):
        name = a.get("name")
        if not name:
            raise AdapterError(f"adapter #{i + 1} has no name")
        match = a.get("match", {})

        def _tuple(val) -> tuple[str, ...]:
            if val is None:
                return ()
            return (val,) if isinstance(val, str) else tuple(val)

        adapters.append(
            PatternAdapter(
                name=name,
                notice_type=a.get("notice_type", ""),
                from_endswith=_tuple(match.get("from")),
                subject_contains=_tuple(match.get("subject_contains")),
                body_contains=_tuple(match.get("body_contains")),
                fields={
                    fname: _rx(pat, name=name, fname=fname)
                    for fname, pat in a.get("fields", {}).items()
                },
                required=tuple(a.get("required", [])),
            )
        )
    return adapters


def parse(env: Envelope, adapters: list[PatternAdapter]) -> NoticeResult | None:
    """First matching adapter wins; callers put firm adapters before built-ins."""
    for adapter in adapters:
        if adapter.match(env):
            return adapter.extract(env)
    return None


def stack(adapters_file: str | Path | None = None) -> list[PatternAdapter]:
    """Firm adapters (if any) first, then built-ins."""
    firm = load_adapters_file(adapters_file) if adapters_file else []
    return firm + builtin_adapters()
