"""Docketry: a local, gate-enforced port that email flows into.

Working name — Docketry is the base layer of a family of small open-source
legal workflow tools. It never reaches into a mailbox the firm works from;
it drains a dedicated intake mailbox the firm forwards into, normalizes each
message, and enforces guardrail gates before anything moves downstream.
"""
__version__ = "0.17.1"

# The tool-backed gates register here rather than inside the port. Importing
# docketry gives you the whole thing; importing docketry.core gives you the
# port, and a manifest naming `notice-parser` there refuses with "unknown
# gate" — which is the honest answer, not a bug.
from .tools import gates_classifier, gates_notice  # noqa: E402,F401
