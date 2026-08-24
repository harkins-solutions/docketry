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

    def validate_options(self, options: dict) -> list[str]:
        problems = []
        if "max_size_mb" in options:
            try:
                float(options["max_size_mb"])
            except (TypeError, ValueError):
                problems.append("max_size_mb must be a number")
        deny = options.get("deny_extensions")
        if deny is not None and (not isinstance(deny, list)
                                 or not all(isinstance(e, str) for e in deny)):
            problems.append("deny_extensions must be a list of strings")
        return problems

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

    def validate_options(self, options: dict) -> list[str]:
        problems = []
        for key in ("allow", "deny"):
            val = options.get(key)
            if val is not None and (not isinstance(val, list)
                                    or not all(isinstance(s, str) for s in val)):
                problems.append(f"{key} must be a list of strings")
        return problems

    @staticmethod
    def _matches(sender: str, domain: str, entry: str) -> bool:
        if sender == entry:
            return True
        if entry.startswith("@"):
            entry_domain = entry[1:]
            # "@uscourts.gov" covers the domain and its subdomains —
            # federal NEFs arrive from per-district hosts like
            # flsd.uscourts.gov.
            return domain == entry_domain or domain.endswith("." + entry_domain)
        return False

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        sender = env.from_addr.lower()
        domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
        for entry in (s.lower() for s in options.get("deny", [])):
            if self._matches(sender, domain, entry):
                return [Finding(self.id, SEVERITY_FAIL,
                                f"sender on the deny list: {env.from_addr}")]
        allowed = [s.lower() for s in options.get("allow", [])]
        if not allowed:
            return []
        for entry in allowed:
            if self._matches(sender, domain, entry):
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


@register
class NameScreen:
    """Hold any message whose content mentions a screened name.

    The legal use is an ethical wall / conflict screen: a firm lists the
    parties or matters a reviewer must not see flowing through the pipeline
    unreviewed, and anything mentioning them parks for the declared
    authority. Terms match case-insensitively on word boundaries across
    subject, body, and attachment filenames. The screen list lives in the
    firm's local guardrails.toml and never leaves the machine.
    """

    id = "name-screen"
    allowed_stages = None

    def validate_options(self, options: dict) -> list[str]:
        terms = options.get("terms")
        if not isinstance(terms, list) or not terms or not all(
            isinstance(t, str) and t.strip() for t in terms
        ):
            return ["terms must be a non-empty list of strings"]
        return []

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        import re as _re

        haystack = "\n".join(
            [env.subject, env.body_text, *[a.filename for a in env.attachments]]
        )
        note = options.get("note", "screened name")
        findings = []
        for term in options.get("terms", []):
            if _re.search(rf"\b{_re.escape(term)}\b", haystack, _re.IGNORECASE):
                findings.append(Finding(
                    self.id, SEVERITY_FAIL,
                    f"message mentions '{term}' ({note}) — held for the declared authority",
                ))
        return findings
