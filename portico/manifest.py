"""Load and validate the guardrail manifest (TOML).

The manifest is where a firm declares its pipeline stages and which gate runs
where. Validation is strict and load-time: unknown gates, unknown stages, a
gate bound outside its declared allowed_stages, or a bare on_fail value all
refuse to load — a misconfigured pipeline never runs half-enforced.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import gates as gate_registry
from .pipeline import GateBinding, ON_FAIL, Pipeline

DEFAULT_MANIFEST = """\
# Portico guardrail manifest.
# Stages run left to right; each [[gate]] declares where it binds, what
# happens on failure (block | bounce | warn), and which role can approve.

[pipeline]
stages = ["ingest", "review"]

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


def load_manifest(path: str | Path) -> Pipeline:
    data = tomllib.loads(Path(path).read_text())
    return build_pipeline(data)


def build_pipeline(data: dict) -> Pipeline:
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

        bindings.append(
            GateBinding(
                gate=cls(),
                binds_to=list(binds_to),
                on_fail=on_fail,
                authority=g.get("authority", "attorney"),
                options=g.get("options", {}),
            )
        )
    return Pipeline(stages=list(stages), bindings=bindings)
