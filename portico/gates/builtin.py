"""Starter gates. Deterministic pipeline-hygiene checks only.

None of these is a security control. Portico makes no malware, phishing, or
other cybersecurity claims anywhere — these gates decide what the *pipeline*
will accept, nothing more. Firms should get actual security from their mail
provider and endpoint tooling.
"""
from __future__ import annotations

from ..envelope import Envelope
from ..pipeline import Finding, SEVERITY_FAIL, SEVERITY_INFO
from . import register


@register
class AttachmentPolicy:
    """What file types and sizes this pipeline accepts. Hygiene, not AV."""

    id = "attachment-policy"
    allowed_stages = {"ingest"}

    _DEFAULT_DENY = (
        ".exe .js .vbs .scr .bat .cmd .com .ps1 .jar .msi .hta .lnk".split()
    )

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        deny = {e.lower() for e in options.get("deny_extensions", self._DEFAULT_DENY)}
        max_mb = float(options.get("max_size_mb", 25))
        findings: list[Finding] = []
        for a in env.attachments:
            ext = ("." + a.filename.rsplit(".", 1)[-1].lower()) if "." in a.filename else ""
            if ext in deny:
                findings.append(
                    Finding(self.id, SEVERITY_FAIL, f"attachment type not accepted: {a.filename}")
                )
            if a.size > max_mb * 1024 * 1024:
                findings.append(
                    Finding(
                        self.id,
                        SEVERITY_FAIL,
                        f"attachment over {max_mb:g} MB: {a.filename} ({a.size} bytes)",
                    )
                )
        return findings


@register
class SenderScope:
    """Optionally hold mail from senders outside the expected set.

    An intake mailbox fed by forwarding rules mostly hears from known portals
    and staff; anything else bounces to a human instead of flowing onward.
    """

    id = "sender-scope"
    allowed_stages = {"ingest"}

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        allowed = [s.lower() for s in options.get("allow", [])]
        if not allowed:
            return []
        sender = env.from_addr.lower()
        for entry in allowed:
            if sender == entry or (entry.startswith("@") and sender.endswith(entry)):
                return []
        return [
            Finding(self.id, SEVERITY_FAIL, f"sender outside intake scope: {env.from_addr}")
        ]


@register
class ProvenanceStamp:
    """Records where each message came from; informational, never holds."""

    id = "provenance-stamp"
    allowed_stages = None

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        return [
            Finding(
                self.id,
                SEVERITY_INFO,
                f"source={env.source} raw_sha256={env.raw_sha256[:16]} fetched_at={env.fetched_at}",
            )
        ]
