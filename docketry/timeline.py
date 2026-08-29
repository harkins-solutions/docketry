"""The case timeline: what this firm received, in the order it arrived.

This is NOT the court's docket. It is a reconstruction assembled from the
notices, receipts and correspondence that crossed the firm's own intake
boundary, and it never claims to be complete. Where something is missing the
timeline says so; it does not quietly close the gap.

Four layers, kept apart on purpose:

  record          served, filed, court events — of record, authoritative
  correspondence  threads with counsel and third parties — context
  client          communications with the client — privileged, different in kind
  derived         OUR inferences (a gap, a computed date) — never a record

They share one clock and are never flattened into one another. A service
notice and an email from opposing counsel carry different legal weight, and a
timeline that renders them identically eventually gets one cited as the other.
Every entry carries the message it came from, so any line can be traced back
to the thing that produced it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

RECORD = "record"
CORRESPONDENCE = "correspondence"
CLIENT = "client"
DERIVED = "derived"
LAYERS = (RECORD, CORRESPONDENCE, CLIENT, DERIVED)

# What we physically hold for an entry.
ATTACHED = "attached"                # the document arrived with the notice
LINK_CAPTURED = "link_captured"      # we have a URL, not the document
REFERENCED_ONLY = "referenced_only"  # we know it exists and hold nothing

# A gap we can prove from a sequence, versus one we merely suspect.
PROVEN = "proven"
SUSPECTED = "suspected"


@dataclass
class Entry:
    when: str                    # ISO 8601; "" when the source carried no date
    layer: str
    kind: str                    # service | filing | hearing | email | gap ...
    title: str
    case_number: str = ""
    actor: str = ""              # who filed, served, or wrote
    availability: str = REFERENCED_ONLY
    document_link: str = ""
    doc_number: int | None = None
    thread_key: str = ""
    source_message: int | None = None   # messages.id — provenance, always
    source_adapter: str = ""
    note: str = ""

    @property
    def of_record(self) -> bool:
        return self.layer == RECORD


@dataclass
class Timeline:
    case_number: str
    entries: list[Entry] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def sorted_entries(self, layers: tuple[str, ...] = LAYERS,
                       thread: str | None = None) -> list[Entry]:
        """Chronological. Entries with no date sort last, not first —
        an undated item is unplaced, and floating it to the top of a
        chronology reads as 'this happened first'."""
        rows = [e for e in self.entries if e.layer in layers
                and (thread is None or e.thread_key == thread)]
        return sorted(rows, key=lambda e: (e.when == "", e.when))

    def threads(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            if e.thread_key:
                out[e.thread_key] = out.get(e.thread_key, 0) + 1
        return out


def normalise_case_number(raw: str) -> str:
    """Strip formatting so the same case matches across source systems.

    PACER, the Florida ePortal and Tyler each punctuate case numbers
    differently. This collapses them to comparable form — it does NOT decide
    that two different numbers are the same case; anything beyond exact match
    after normalisation is a human's call.
    """
    return re.sub(r"[^0-9A-Za-z]", "", raw or "").upper()


def _kind_for(notice_type: str) -> str:
    return {"service_notice": "service", "filing_receipt": "filing",
            "hearing_notice": "hearing"}.get(notice_type, notice_type)


def build(store, case_number: str, *, threads: list[str] | None = None,
          directory=None) -> Timeline:
    """Weave the record layer for one case, plus any threads a human attached.

    Notices are matched on the case number they carry — exact, after
    normalisation. Correspondence carries no case number, so it is never
    guessed at: a thread joins this timeline only because someone pointed at
    it, which is both more accurate than a classifier and consistent with
    nothing entering that the firm did not send across the boundary.
    """
    want = normalise_case_number(case_number)
    tl = Timeline(case_number=case_number)
    attached_threads = set(threads or [])

    for row in store.list_notices():
        fields = json.loads(row["fields_json"])
        if normalise_case_number(fields.get("case_number", "")) != want:
            continue
        msg = store.get_message(row["message_id"])
        env = json.loads(msg["envelope_json"]) if msg else {}
        doc_no = fields.get("document_number")
        link = fields.get("document_link", "")
        tl.entries.append(Entry(
            when=env.get("date", ""),
            layer=RECORD,
            kind=_kind_for(row["notice_type"]),
            title=(fields.get("docket_text") or fields.get("documents")
                   or fields.get("filing_description")
                   or fields.get("matter") or row["notice_type"]),
            case_number=fields.get("case_number", ""),
            actor=env.get("from_addr", ""),
            availability=(ATTACHED if env.get("attachments")
                          else LINK_CAPTURED if link else REFERENCED_ONLY),
            document_link=link,
            doc_number=int(doc_no) if str(doc_no or "").isdigit() else None,
            thread_key=env.get("message_id", ""),
            source_message=row["message_id"],
            source_adapter=row["adapter"],
            note="; ".join(f"missing {m}" for m in json.loads(row["missing_json"])),
        ))

    if attached_threads:
        _add_threads(store, tl, attached_threads, directory)

    tl.gaps = find_gaps(tl)
    tl.findings = cross_layer_findings(tl)
    return tl


def _add_threads(store, tl: Timeline, keys: set[str], directory=None) -> None:
    """Pull in every message belonging to a thread a human attached.

    Where each message lands depends on who wrote it. Client mail is
    privileged and belongs in its own layer; without a contacts directory
    nothing can tell it from correspondence with the other side, so it all
    falls to CORRESPONDENCE — which is the safe direction, because the mistake
    that matters is client mail sitting in a list somebody hands over.
    """
    for row in store.list_by_status("ok"):
        env = json.loads(row["envelope_json"])
        refs = env.get("references") or []
        key = refs[0] if refs else (env.get("in_reply_to")
                                    or env.get("message_id", ""))
        if key not in keys:
            continue
        author = env.get("from_addr", "")
        layer = CORRESPONDENCE
        kind = "email"
        if directory is not None:
            contact_kind = directory.kind_of(author)
            if directory.is_privileged(author):
                layer = CLIENT
            if contact_kind and contact_kind != "other":
                kind = contact_kind
        tl.entries.append(Entry(
            when=env.get("date", ""),
            layer=layer,
            kind=kind,
            title=env.get("subject", "") or "(no subject)",
            actor=env.get("from_addr", ""),
            availability=ATTACHED if env.get("attachments") else REFERENCED_ONLY,
            thread_key=key,
            source_message=row["id"],
        ))


def find_gaps(tl: Timeline) -> list[dict]:
    """Missing entries, split by whether we can actually prove it.

    A federal docket number is a monotonic sequence, so a hole in it is a
    fact. State service notices carry no sequence at all, so nothing there is
    provable from the stream alone — those are reconciled against a docket a
    human pulls, never asserted from inference. Keeping the two apart is the
    difference between a finding and a guess.
    """
    numbered = sorted({e.doc_number for e in tl.entries if e.doc_number})
    if not numbered:
        return []
    have = set(numbered)
    missing = [n for n in range(numbered[0], numbered[-1] + 1) if n not in have]
    if not missing:
        return []

    # Collapse consecutive numbers into runs. A firm served 1, 12 and 15 is
    # missing twelve numbers, and reporting that as twelve separate findings
    # buries the one fact worth reading. A finding that fires on every case
    # gets ignored on every case.
    runs: list[list[int]] = [[missing[0]]]
    for n in missing[1:]:
        if n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    spans = [str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs]
    return [{
        "class": PROVEN,
        "count": len(missing),
        "numbers": missing,
        "detail": f"{len(missing)} document(s) between #{numbered[0]} and"
                  f" #{numbered[-1]} are not in this reconstruction"
                  f" ({', '.join(spans)}) — they were never served on this"
                  " firm, or their notices never reached the intake."
                  " A firm is not served with everything on a docket, so this"
                  " is a list to check against a pulled docket, not proof that"
                  " anything went wrong",
    }]


def cross_layer_findings(tl: Timeline) -> list[str]:
    """What only shows up once the layers share a clock."""
    out: list[str] = []
    rejected = [e for e in tl.entries
                if e.kind == "filing" and "reject" in (e.title or "").lower()]
    for r in rejected:
        later = [e for e in tl.entries
                 if e.kind == "filing" and e.when > r.when and e is not r]
        if not later:
            out.append(
                f"a filing was REJECTED ({r.when[:10] or 'undated'}) and no"
                " later filing appears — if it was never re-filed, the firm"
                " may believe a document is of record that is not"
            )
    return out
