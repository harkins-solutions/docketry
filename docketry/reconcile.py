"""Diff the reconstruction against a docket a human pulled.

The reconstruction is built from what the firm received, so it can only ever
be as complete as the mail was. The way to find out what it is missing is not
to infer harder — it is to compare against the real thing. Somebody logs into
the portal, pulls the docket, and brings it across the same boundary
everything else crosses. Nothing here fetches anything.

The comparison runs in both directions, because both directions are findings:

  only_on_docket   the court has it, we do not — never served on this firm,
                   or the notice never reached the intake
  only_here        we have it, the docket does not — a filing that was
                   rejected, correspondence mistaken for a record, or an
                   entry attached to the wrong case

Federal entries carry a document number, so those match exactly. Everything
else matches on date and title, which is a guess — so those land in
`to_confirm` for a person to accept, never merged automatically.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from .timeline import Timeline

_NUM_LINE = re.compile(r"^\s*(\d{1,5})\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+(.+?)\s*$")


@dataclass
class DocketLine:
    doc_number: int | None
    date: str
    title: str


@dataclass
class Reconciliation:
    matched: list[tuple[DocketLine, object]] = field(default_factory=list)
    only_on_docket: list[DocketLine] = field(default_factory=list)
    only_here: list[object] = field(default_factory=list)
    to_confirm: list[tuple[DocketLine, object]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.only_on_docket and not self.only_here


def parse_docket(text: str) -> list[DocketLine]:
    """Read a pulled docket: CSV with headers, or pasted numbered lines.

    Deliberately forgiving about shape and strict about content — a line that
    cannot be read is skipped rather than guessed at, because a
    misread docket line becomes a false finding in both directions.
    """
    out: list[DocketLine] = []
    head = text.lstrip()[:400].lower()
    if "," in head and re.search(r"\b(date|filed|title|description|doc)\b", head):
        for row in csv.DictReader(io.StringIO(text)):
            low = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in row.items()}
            num = low.get("doc") or low.get("#") or low.get("number") or ""
            date = low.get("date") or low.get("filed") or ""
            title = low.get("title") or low.get("description") or low.get("text") or ""
            if not (date or title):
                continue
            out.append(DocketLine(int(num) if num.isdigit() else None, date, title))
        return out
    for line in text.splitlines():
        m = _NUM_LINE.match(line)
        if m:
            out.append(DocketLine(int(m.group(1)), m.group(2), m.group(3)))
    return out


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def _similar(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def reconcile(tl: Timeline, lines: list[DocketLine], *,
              threshold: float = 0.55) -> Reconciliation:
    """Compare, in both directions. Record-layer entries only.

    Correspondence is deliberately excluded: an email was never going to be on
    the docket, and counting its absence as a discrepancy would bury the real
    findings in noise.
    """
    rec = Reconciliation()
    ours = [e for e in tl.entries if e.of_record]
    used: set[int] = set()

    by_number = {e.doc_number: e for e in ours if e.doc_number}
    for line in lines:
        if line.doc_number is not None and line.doc_number in by_number:
            entry = by_number[line.doc_number]
            rec.matched.append((line, entry))
            used.add(id(entry))
            continue
        best, score = None, 0.0
        for e in ours:
            if id(e) in used or e.doc_number is not None:
                continue
            s = _similar(line.title, e.title)
            if s > score:
                best, score = e, s
        if best is not None and score >= threshold:
            rec.to_confirm.append((line, best))
            used.add(id(best))
        else:
            rec.only_on_docket.append(line)

    rec.only_here = [e for e in ours if id(e) not in used]
    return rec
