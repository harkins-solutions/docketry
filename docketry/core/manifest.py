"""Load and validate guardrails.toml.

The manifest declares pipeline stages and which gate binds to which stage.
Validation is strict and happens at load: an unknown gate id, an unknown
stage, a gate bound outside its declared `allowed_stages`, an `on_fail` value
that is not block/bounce/warn, or options a gate's validate_options() rejects
all refuse the file. A partially valid manifest never runs.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import gates as gate_registry
from .pipeline import GateBinding, ON_FAIL, Pipeline

DEFAULT_MANIFEST = """\
# Docketry guardrail manifest.
# Stages run left to right; each [[gate]] declares where it binds, what
# happens on failure (block | bounce | warn), and which role can approve.
#
# `docketry init` without --host/--user asks questions and writes this file
# from the answers, including an ethical wall. This is the starter version.

[pipeline]
stages = ["ingest", "review"]

# A court notice this cannot read is held rather than guessed at.
[[gate]]
id = "notice-parser"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

# Uncomment and list your own screened parties to turn on the ethical wall.
#
# [[gate]]
# id = "name-screen"
# binds_to = ["ingest"]
# on_fail = "block"
# authority = "attorney"
#
# [gate.options]
# terms = ["Walled Party LLC"]
# note = "ethical wall"

[[gate]]
id = "attachment-policy"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[gate.options]
max_size_mb = 25

[[gate]]
id = "provenance-stamp"
binds_to = ["ingest"]
on_fail = "warn"
authority = "paralegal"
"""


class ManifestError(ValueError):
    pass


def load_manifest(path: str | Path, registry=None) -> Pipeline:
    data = tomllib.loads(Path(path).read_text())
    return build_pipeline(data, registry)


def build_pipeline(data: dict, registry=None) -> Pipeline:
    stages = data.get("pipeline", {}).get("stages", [])
    if not stages or not all(isinstance(s, str) and s for s in stages):
        raise ManifestError("[pipeline].stages must be a non-empty list of stage names")
    if len(set(stages)) != len(stages):
        raise ManifestError("duplicate stage names in [pipeline].stages")

    bindings: list[GateBinding] = []
    for i, g in enumerate(data.get("gate", [])):
        gid = g.get("id")
        if not gid:
            raise ManifestError(f"gate #{i + 1} has no id")
        try:
            cls = gate_registry.get(gid)
        except KeyError as e:
            raise ManifestError(str(e)) from None

        binds_to = g.get("binds_to", [])
        if not binds_to:
            raise ManifestError(f"gate '{gid}' declares no binds_to stages")
        unknown = [s for s in binds_to if s not in stages]
        if unknown:
            raise ManifestError(f"gate '{gid}' binds to unknown stage(s): {unknown}")

        allowed = getattr(cls, "allowed_stages", None)
        if allowed is not None:
            out_of_scope = [s for s in binds_to if s not in allowed]
            if out_of_scope:
                raise ManifestError(
                    f"gate '{gid}' is not meant for stage(s) {out_of_scope};"
                    f" it belongs in: {sorted(allowed)}"
                )

        on_fail = g.get("on_fail", "bounce")
        if on_fail not in ON_FAIL:
            raise ManifestError(
                f"gate '{gid}' has on_fail='{on_fail}' (must be one of {ON_FAIL})"
            )

        gate = cls()
        validator = getattr(gate, "validate_options", None)
        if validator is not None:
            problems = validator(g.get("options", {}))
            if problems:
                raise ManifestError(f"gate '{gid}' options invalid: {'; '.join(problems)}")

        authority = g.get("authority", "attorney")
        if registry is not None:
            # Caught here, where it is cheap. A gate naming a role nobody
            # declared fails closed at the moment someone needs to release it,
            # which is the worst possible time to discover a typo.
            registry.check_authority(f"gate '{gate.id}'", authority)
        bindings.append(
            GateBinding(
                gate=gate,
                binds_to=list(binds_to),
                on_fail=on_fail,
                authority=authority,
                options=g.get("options", {}),
            )
        )
    return Pipeline(stages=list(stages), bindings=bindings)
