"""Doc-classifier gate: proposes a type for each attachment, never applies.

Informational only — the write path is the staged queue (class-apply),
fill-only and role-recorded. Classification is free and deterministic first;
model tiers are somebody else's optional add-on, never a default.
"""
from __future__ import annotations

from .classify import classify
from ..core.envelope import Envelope
from ..core.pipeline import Finding, SEVERITY_INFO
from ..core.gates import register


@register
class DocClassifier:
    id = "doc-classifier"
    allowed_stages = None

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        findings = []
        for a in env.attachments:
            label, tier = classify(a.filename)
            if tier != "low":
                findings.append(
                    Finding(self.id, SEVERITY_INFO, f"{a.filename}: proposed {label} ({tier})")
                )
        return findings
