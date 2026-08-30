"""What `docketry new-gate` writes: a gate that already works.

A blank file with a TODO in it is not a starting point, it is homework. This
writes a gate that runs, holds something, and explains every part of itself in
comments — so the first thing an author does is change a working thing rather
than assemble one.

Placeholders are substituted by plain replacement rather than str.format,
because the template is Python source and Python source is full of braces.
"""
from __future__ import annotations

GATE_TEMPLATE = '''"""__TITLE__

A Docketry gate. It gets one message and answers one question about it.

The rules a gate lives by:

  * It is deterministic. Same message in, same findings out — a guardrail
    that answers differently on Tuesday is not a guardrail.
  * It reads. It never sends mail, writes to the store, or moves a message.
    A `fail` finding is how a gate stops something; the runner and a recorded
    human approval are what move it again.
  * It never consults a model. Models propose elsewhere; gates decide. A test
    in the Docketry repo enforces that separation for the shipped gates.
  * It says why. Your summary is what someone reads at five o'clock to
    understand why their message is sitting in the queue. Write it for them.
"""
from docketry.core.gates import register
from docketry.core.pipeline import Finding, SEVERITY_FAIL, SEVERITY_INFO, SEVERITY_WARN


@register
class __CLASS__:
    """__TITLE__"""

    # How manifests bind this gate. Lowercase words joined by hyphens.
    id = "__GATE_ID__"

    # Which stages this gate is meant for; None means anywhere. Binding it
    # outside these refuses when the manifest loads, rather than at 5pm.
    allowed_stages = None

    def validate_options(self, options: dict) -> list[str]:
        """Optional. Return problems with this gate's [gate.options] block.

        Anything returned here refuses the manifest at load time, which is the
        cheapest possible moment for a firm to find out it made a typo.
        """
        problems = []
        if "max_words" in options:
            try:
                int(options["max_words"])
            except (TypeError, ValueError):
                problems.append("max_words must be a whole number")
        return problems

    def check(self, envelope, options: dict) -> list[Finding]:
        """The whole job. Return findings; an empty list means it passes.

        `envelope` carries message_id, from_addr, to, cc, subject, body_text,
        date, source, fetched_at, raw_sha256, and attachments — each with
        filename, content_type, size, sha256 and content (the real bytes,
        every time this runs).

        Severity decides what happens next. SEVERITY_FAIL is the only one that
        holds a message; HOW it is held (block, bounce, warn) is the manifest's
        `on_fail`, not this gate's business. WARN and INFO are recorded and the
        message keeps moving.
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
    """The scaffold, with this gate's id, class name and title filled in."""
    cls = "".join(part.capitalize() for part in gate_id.split("-")) or "MyGate"
    title = title or f"{gate_id.replace('-', ' ').capitalize()}."
    return (GATE_TEMPLATE
            .replace("__CLASS__", cls)
            .replace("__GATE_ID__", gate_id)
            .replace("__TITLE__", title))


def gate_binding_toml(gate_id: str, authority: str = "paralegal") -> str:
    """The block to paste into guardrails.toml to actually run it."""
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
