"""Deterministic litigation-document classifier.

Types a filed document from its title first (high confidence), body anchors
second (medium), and falls back to correspondence (low). No model calls, no
network, no cost — LLMs are a last resort that this module deliberately is
not. The classifier only ever PROPOSES: writes are staged for approval and
applied fill-only by a human with the declared authority (see store/CLI).
"""
from __future__ import annotations

import re

LABELS = (
    "amended_complaint", "complaint", "answer",
    "motion_msj", "motion_dismiss", "motion_compel", "motion",
    "order", "notice_of_hearing", "notice",
    "discovery_request", "discovery_response",
    "subpoena", "deposition", "correspondence",
)

_T = [  # title anchors, ordered: first match wins
    ("amended_complaint", r"amended\s+(?:complaint|petition)"),
    ("order", r"^(?:agreed\s+|proposed\s+)?order\b|order\s+(?:granting|denying|on|setting)"),
    ("motion_msj", r"motion\s+for\s+(?:final\s+)?summary\s+judgment"),
    ("motion_dismiss", r"motion\s+to\s+dismiss"),
    ("motion_compel", r"motion\s+to\s+compel"),
    ("deposition", r"(?:notice\s+of\s+taking\s+)?deposition\s+of|depo(?:sition)?\s+transcript"),
    ("notice_of_hearing", r"notice\s+of\s+hearing"),
    ("subpoena", r"subpoena"),
    ("discovery_response", r"(?:response|objection)s?\s+to\s+.*(?:interrogator|production|admission|discovery)"),
    ("discovery_request", r"interrogator|request\s+(?:for|to)\s+produc|request\s+for\s+admission"),
    ("answer", r"\banswer\b.*affirmative\s+defenses|\banswer\s+to\b|defendant'?s?\s+answer"),
    ("motion", r"\bmotion\b"),
    ("complaint", r"\b(?:complaint|petition)\b"),
    ("notice", r"\bnotice\b"),
]
_TITLE_ANCHORS = [(label, re.compile(rx, re.IGNORECASE)) for label, rx in _T]

_B = [  # body anchors, checked in the first 3000 chars
    ("order", r"ORDERED\s+AND\s+ADJUDGED|it\s+is\s+hereby\s+ORDERED"),
    ("complaint", r"COMES\s+NOW.{0,200}(?:complaint|sues?\s+defendant)"),
    ("discovery_request", r"propounds?\s+the\s+following\s+interrogator"),
    ("answer", r"answers?\s+the\s+complaint\s+and\s+(?:asserts?\s+)?affirmative\s+defenses"),
    ("deposition", r"APPEARANCES.{0,600}(?:COURT\s+REPORTER|STENOGRAPH)"),
]
_BODY_ANCHORS = [(label, re.compile(rx, re.IGNORECASE | re.DOTALL)) for label, rx in _B]


def classify(title: str, text: str = "") -> tuple[str, str]:
    """(label, tier) — tier: high (title anchor) / medium (body) / low."""
    title = (title or "").replace("_", " ").replace("-", " ")
    for label, rx in _TITLE_ANCHORS:
        if rx.search(title):
            return label, "high"
    head = (text or "")[:3000]
    if head:
        for label, rx in _BODY_ANCHORS:
            if rx.search(head):
                return label, "medium"
    return "correspondence", "low"
