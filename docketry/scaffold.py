"""The gate file `docketry new-gate` writes.

Placeholders are substituted by plain replacement rather than str.format,
because the template is Python source and Python source is full of braces.
"""
from __future__ import annotations

GATE_TEMPLATE = '''"""__TITLE__

Written by `docketry new-gate __GATE_ID__`. As written, this gate holds any
message whose subject runs longer than `max_words` words.

Reference: GATES.md in the Docketry repository.
"""
from docketry.core.gates import register
from docketry.core.pipeline import Finding, SEVERITY_FAIL, SEVERITY_INFO, SEVERITY_WARN


@register
class __CLASS__:
    """__TITLE__"""

    # The name manifests bind this gate by. Lowercase words joined by hyphens.
    id = "__GATE_ID__"

    # Stages this gate may be bound to; None means any. Binding it elsewhere
    # is an error when guardrails.toml loads.
    allowed_stages = None

    def validate_options(self, options: dict) -> list[str]:
        """Check this gate's [gate.options] block. Optional.

        Anything returned refuses the manifest at load time, with your text
        in the error. Return an empty list if the options are usable.
        """
        problems = []
        if "max_words" in options:
            try:
                int(options["max_words"])
            except (TypeError, ValueError):
                problems.append("max_words must be a whole number")
        return problems

    def check(self, envelope, options: dict) -> list[Finding]:
        """Examine one message. Return findings; an empty list passes it.

        envelope:
            message_id, from_addr, to, cc, subject, body_text, date,
            source, fetched_at, raw_sha256, in_reply_to, references,
            and attachments — each with filename, content_type, size,
            sha256 and content (bytes).

        options:
            the [gate.options] table from guardrails.toml, TOML types intact.

        Finding(gate_id, severity, summary). Only SEVERITY_FAIL can hold a
        message; whether that means blocked or queued for review is the
        manifest's on_fail, not this gate's. WARN and INFO are recorded and
        the message continues.

        check() runs again after every approval, so it must return the same
        result for the same message.
        """
        max_words = int(options.get("max_words", 5))
        words = len(envelope.subject.split())
        if words > max_words:
            return [Finding(
                self.id,
                SEVERITY_FAIL,
                f"subject is {words} words, over the {max_words} this pipeline"
                f" accepts: {envelope.subject[:60]!r}",
            )]
        return []
'''


def gate_source(gate_id: str, title: str = "") -> str:
    """The scaffold with this gate's id, class name and title filled in."""
    cls = "".join(part.capitalize() for part in gate_id.split("-")) or "MyGate"
    title = title or f"{gate_id.replace('-', ' ').capitalize()}."
    return (GATE_TEMPLATE
            .replace("__CLASS__", cls)
            .replace("__GATE_ID__", gate_id)
            .replace("__TITLE__", title))


def gate_binding_toml(gate_id: str, authority: str = "paralegal") -> str:
    """The guardrails.toml block that binds this gate."""
    return (
        "[[gate]]\n"
        f'id = "{gate_id}"\n'
        'binds_to = ["ingest"]\n'
        'on_fail = "bounce"\n'
        f'authority = "{authority}"\n'
        "\n"
        "[gate.options]\n"
        "max_words = 5\n"
    )
