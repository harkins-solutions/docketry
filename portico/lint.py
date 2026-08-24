"""Brief linter: deterministic writing checks for litigation drafts.

Lint drafts like code: cheap, deterministic checks run before a human spends
review time — the human reviews findings, not raw text. Every rule here is
an editor, not a lawyer: nothing asserts what the law is, only what the
document does to itself.

Built-in rules:
- credibility-language: in a summary-judgment context, words that accuse the
  other side of lying (misrepresented, concealed, intentionally, conveniently
  omitted) invite "that's a jury question" — flagged only when the draft is
  SJ briefing.
- uncited-testimony: a line asserting sworn testimony with no record pin cite
  on that line.
- date-contradiction: the draft asserts a deadline closed/expired on a date
  that is AFTER the draft's own certificate-of-service date.
- reporter-spacing: So.2d/So.3d written without the space (So. 2d / So. 3d).

Firm rulepacks are TOML: pattern rules with an id, message, and severity,
validated at load. Packs are shareable the way linter configs are.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class RulepackError(ValueError):
    pass


@dataclass
class LintFinding:
    rule: str
    severity: str    # error | warn
    line: int        # 1-based; 0 = document-level
    message: str
    excerpt: str = ""


_SJ_CONTEXT = re.compile(
    r"summary\s+judgment|rule\s+1\.510|fed\.?\s*r\.?\s*civ\.?\s*p\.?\s*56|rule\s+56\b",
    re.IGNORECASE,
)
_CREDIBILITY = re.compile(
    r"\b(misrepresent(?:s|ed|ation|ing)?|conceal(?:s|ed|ment|ing)?|"
    r"intentionally\s+(?:misled|omitted|hid)|conveniently\s+omit(?:s|ted)?|"
    r"lie[sd]?\b|lying|self-serving)\b",
    re.IGNORECASE,
)
_TESTIMONY = re.compile(
    r"\b(testified|testifies|swore|sworn\s+testimony|admitted\s+(?:at|in|during)|"
    r"stated\s+(?:at|in)\s+(?:his|her|their)\s+deposition)\b",
    re.IGNORECASE,
)
_PIN_CITE = re.compile(
    r"(\bEx(?:h)?\.\s|\bExhibit\s+[A-Z0-9]|¶|\bp\.\s*\d|\bpp\.\s*\d|"
    r"\bat\s+\d+|\d+:\d+|\bDep\.|\bT\.\s*(?:at\s*)?\d|\bR\.\s*(?:at\s*)?\d)",
)
_REPORTER_SPACING = re.compile(r"\bSo\.([23])d\b")

_DATE_RX = re.compile(
    r"((?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})"
)
_CLOSED_RX = re.compile(
    r"(?:discovery|disclosure)s?\s+(?:period\s+)?(?:has\s+)?"
    r"(?:closed|expired|ended|passed)\s+on\s+" + _DATE_RX.pattern,
    re.IGNORECASE,
)
_CERT_RX = re.compile(r"certificate\s+of\s+service", re.IGNORECASE)


def _parse_date(s: str) -> datetime | None:
    for fmt in ("%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped == stripped.upper() and len(stripped) < 80


def lint(text: str, rulepack: list[dict] | None = None) -> list[LintFinding]:
    lines = text.splitlines()
    findings: list[LintFinding] = []
    sj = bool(_SJ_CONTEXT.search(text))

    for n, line in enumerate(lines, start=1):
        if _is_heading(line):
            continue
        if sj:
            m = _CREDIBILITY.search(line)
            if m and '"' not in line[: m.start()]:  # quoted testimony is fair game
                findings.append(LintFinding(
                    "credibility-language", "warn", n,
                    f"'{m.group(0)}' in summary-judgment briefing invites a"
                    " credibility question a court cannot resolve on SJ —"
                    " recast as absence of competent evidence",
                    line.strip()[:120],
                ))
        if _TESTIMONY.search(line) and not _PIN_CITE.search(line):
            findings.append(LintFinding(
                "uncited-testimony", "error", n,
                "sworn-testimony assertion with no record pin cite on the line",
                line.strip()[:120],
            ))
        for m in _REPORTER_SPACING.finditer(line):
            findings.append(LintFinding(
                "reporter-spacing", "warn", n,
                f"'So.{m.group(1)}d' should be 'So. {m.group(1)}d'",
                line.strip()[:120],
            ))

    closed = _CLOSED_RX.search(text)
    if closed:
        closed_date = _parse_date(closed.group(1))
        cert = _CERT_RX.search(text)
        if closed_date and cert:
            cert_dates = _DATE_RX.findall(text[cert.start():cert.start() + 600])
            for ds in cert_dates:
                cert_date = _parse_date(ds)
                if cert_date and cert_date < closed_date:
                    findings.append(LintFinding(
                        "date-contradiction", "error", 0,
                        f"the draft says discovery closed on"
                        f" {closed.group(1)} but its certificate of service"
                        f" is dated {ds} — it asserts a past event that has"
                        " not happened yet",
                    ))
                    break

    for rule in rulepack or []:
        rx = rule["_compiled"]
        for n, line in enumerate(lines, start=1):
            m = rx.search(line)
            if m:
                findings.append(LintFinding(
                    rule["id"], rule.get("severity", "warn"), n,
                    rule["message"], line.strip()[:120],
                ))
    findings.sort(key=lambda f: (f.line or 10**9, f.rule))
    return findings


def load_rulepack(path: str | Path) -> list[dict]:
    data = tomllib.loads(Path(path).read_text())
    rules = []
    for i, r in enumerate(data.get("rule", [])):
        rid = r.get("id")
        if not rid:
            raise RulepackError(f"rule #{i + 1} has no id")
        if r.get("severity", "warn") not in ("error", "warn"):
            raise RulepackError(f"rule '{rid}': severity must be error or warn")
        pattern = r.get("pattern")
        if not pattern:
            raise RulepackError(f"rule '{rid}' has no pattern")
        if not r.get("message"):
            raise RulepackError(f"rule '{rid}' has no message")
        try:
            r["_compiled"] = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise RulepackError(f"rule '{rid}': pattern does not compile: {e}")
        rules.append(r)
    return rules
