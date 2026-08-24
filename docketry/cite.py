"""Citation verification: the existence + name + quote + pin check.

Four factual checks against CourtListener, and nothing else:

1. The cited case exists (volume/reporter/page resolves).
2. The case NAME matches what that reporter cite actually resolves to —
   the hallucination class where a real citation carries an invented name.
3. Quoted language attributed to the case actually appears in the opinion.
4. A pin cite points at the page where the quoted language sits (via the
   opinion's star pagination, when the source provides it).

This module never says anything is good law, current, binding, or apt. It
reports that a citation string does not match the document it points to, and
it degrades loudly: no network means extraction-only, never a silent pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_GENERIC_TOKENS = {
    "state", "company", "companies", "insurance", "corp", "corporation",
    "inc", "llc", "llp", "co", "of", "the", "and", "in", "re", "ex", "rel",
    "et", "al", "v", "vs", "a", "an", "county", "city", "board", "dept",
    "department", "america", "american", "national", "united", "states",
}

_QUOTE_RX = re.compile(r'[“"]([^“”"]{25,}?)[”"]')
_STAR_RX = re.compile(r'<span[^>]*class="star-pagination"[^>]*label="(\d+)"[^>]*>')
_TAG_RX = re.compile(r"<[^>]+>")


class CiteError(RuntimeError):
    pass


@dataclass
class Citation:
    text: str            # the citation as written, e.g. "260 So. 3d 323"
    plaintiff: str
    defendant: str
    pin_page: int | None
    span: tuple[int, int]  # character span in the source text


@dataclass
class Lookup:
    exists: bool
    case_name: str = ""
    cluster_id: int | None = None


@dataclass
class CiteFinding:
    citation: str
    check: str           # exists | name | quote | pin
    severity: str        # fail | warn | info
    summary: str


@dataclass
class Report:
    citations: list[Citation]
    short_citations: int = 0
    findings: list[CiteFinding] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(f.severity == "fail" for f in self.findings)


def _normalize(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokens(name: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", name.lower())
        if len(t) > 2 and t not in _GENERIC_TOKENS
    }


def extract_citations(text: str) -> list[Citation]:
    """Full case citations via eyecite. Needs the 'cite' extra."""
    return _extract(text)[0]


def citation_inventory(text: str) -> tuple[list[Citation], int]:
    """(full citations, count of short-form/supra/id citations).

    Short-form cites ("329 So. 3d at 153") depend on an antecedent full cite;
    when the fulls live outside the document (a redline, an excerpt, a brief
    section), the shorts are UNVERIFIABLE AS WRITTEN — and that must be said
    loudly, never reported as "0 citations found".
    """
    return _extract(text)


def _extract(text: str) -> tuple[list[Citation], int]:
    try:
        import eyecite
        from eyecite.models import (
            FullCaseCitation, IdCitation, ShortCaseCitation, SupraCitation,
        )
    except ImportError:
        raise CiteError(
            "citation extraction needs the 'cite' extra:"
            " pip install 'docketry[cite]'"
        ) from None

    out: list[Citation] = []
    n_short = 0
    for cite in eyecite.get_citations(text):
        if isinstance(cite, (ShortCaseCitation, SupraCitation, IdCitation)):
            n_short += 1
            continue
        if not isinstance(cite, FullCaseCitation):
            continue
        meta = cite.metadata
        pin = None
        if meta.pin_cite:
            m = re.search(r"\d+", meta.pin_cite)
            pin = int(m.group()) if m else None
        out.append(
            Citation(
                text=cite.corrected_citation(),
                plaintiff=meta.plaintiff or "",
                defendant=meta.defendant or "",
                pin_page=pin,
                span=cite.span(),
            )
        )
    return out, n_short


def name_matches(plaintiff: str, defendant: str, resolved_name: str) -> bool:
    """Side-aware significant-token check of the cited name vs the resolved one.

    Every cited side that has significant tokens must land at least one of
    them in the corresponding side of the resolved caption (either order, to
    tolerate cross-appeals); a consolidated/inre caption without "v." is
    checked against the whole name.
    """
    cited = [(i, s) for i, s in enumerate((_tokens(plaintiff), _tokens(defendant))) if s]
    if not cited:
        return True  # nothing asserted, nothing to contradict
    resolved_sides = [
        _tokens(part) for part in re.split(r"\s+v\.?\s+", resolved_name, maxsplit=1)
    ]
    if len(resolved_sides) == 2:
        if all(toks & resolved_sides[i] for i, toks in cited):
            return True
        # A full swap is a legitimate cross-appeal caption; a single cited
        # side matching only the OPPOSITE side is the classic wrong-name
        # hallucination (e.g. the defendant's surname promoted to plaintiff),
        # so the swap is only honored when BOTH sides swap cleanly.
        if len(cited) == 2 and all(toks & resolved_sides[1 - i] for i, toks in cited):
            return True
        return False
    whole = _tokens(resolved_name)
    return all(toks & whole for _, toks in cited)


def quotes_near(text: str, span: tuple[int, int], *, window: int = 600) -> list[str]:
    """Quoted passages (>=25 chars) in the window before the citation."""
    region = text[max(0, span[0] - window):span[0]]
    return [m.group(1) for m in _QUOTE_RX.finditer(region)]


def star_pages(opinion_html: str) -> list[tuple[int, str]]:
    """(page_label, normalized text of the segment that FOLLOWS it)."""
    out: list[tuple[int, str]] = []
    matches = list(_STAR_RX.finditer(opinion_html))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(opinion_html)
        segment = _TAG_RX.sub(" ", opinion_html[m.end():end])
        out.append((int(m.group(1)), _normalize(segment)))
    return out


def verify(text: str, client) -> Report:
    """Run the four checks over every full citation in `text`.

    `client` provides lookup(citation_text) -> Lookup and
    opinion_text(cluster_id) -> (plain_text, html_or_None).
    """
    citations, n_short = citation_inventory(text)
    report = Report(citations=citations, short_citations=n_short)
    if n_short and not citations:
        report.findings.append(CiteFinding(
            "(short-form)", "exists", "fail",
            f"{n_short} short-form citation(s) (e.g. '329 So. 3d at 153') with"
            " no full citation anywhere in this document — unverifiable as"
            " written; a filed brief must carry the full cites",
        ))
    elif n_short:
        report.findings.append(CiteFinding(
            "(short-form)", "exists", "info",
            f"{n_short} short-form citation(s) ride on the full citations"
            " verified above",
        ))
    for cite in citations:
        lookup: Lookup = client.lookup(cite.text)
        if not lookup.exists:
            report.findings.append(
                CiteFinding(cite.text, "exists", "fail",
                            f"{cite.text}: no case found at this citation")
            )
            continue
        cited_name = f"{cite.plaintiff} v. {cite.defendant}".strip(" v.")
        if not name_matches(cite.plaintiff, cite.defendant, lookup.case_name):
            report.findings.append(
                CiteFinding(
                    cite.text, "name", "fail",
                    f"cited as \"{cited_name}\" but {cite.text} resolves to"
                    f" \"{lookup.case_name}\"",
                )
            )
        quotes = quotes_near(text, cite.span)
        if quotes and lookup.cluster_id is not None:
            opinion_plain, opinion_html = client.opinion_text(lookup.cluster_id)
            norm_opinion = _normalize(opinion_plain)
            pages = star_pages(opinion_html) if opinion_html else []
            for q in quotes:
                nq = _normalize(q).rstrip(".;:,!?")
                if nq not in norm_opinion and not any(nq in seg for _, seg in pages):
                    report.findings.append(
                        CiteFinding(
                            cite.text, "quote", "fail",
                            f"quoted language not found in {lookup.case_name}:"
                            f" \"{q[:80]}...\"" if len(q) > 80 else
                            f"quoted language not found in {lookup.case_name}:"
                            f" \"{q}\"",
                        )
                    )
                elif cite.pin_page is not None and pages:
                    hit = [label for label, seg in pages if nq in seg]
                    if hit and cite.pin_page not in hit:
                        report.findings.append(
                            CiteFinding(
                                cite.text, "pin", "warn",
                                f"pin cite says p. {cite.pin_page} but the"
                                f" quoted language sits at p. {hit[0]}",
                            )
                        )
        if not report.findings or report.findings[-1].citation != cite.text:
            report.findings.append(
                CiteFinding(cite.text, "exists", "info",
                            f"{cite.text} -> {lookup.case_name}")
            )
    return report
