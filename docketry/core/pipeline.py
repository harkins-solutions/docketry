"""The gate runner: stages, findings, and the only path forward.

A message enters a stage; every gate bound to that stage runs against it. A
gate returning a finding with severity `fail` holds the message unless a
recorded approval clears it, and how it is held comes from the binding:

  block  -> status `blocked`
  bounce -> status `pending_review`, listed by `docketry queue`
  warn   -> finding recorded, message continues

advance() is the only function that moves a message to the next stage. It
re-runs the current stage's gates first, so a hold clears because the gates
clear it, not because a stored status says so. There is no bypass flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from . import store as st
from .envelope import Envelope

SEVERITY_FAIL = "fail"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

ON_FAIL = ("block", "bounce", "warn")


@dataclass
class Finding:
    gate_id: str
    severity: str
    summary: str


class Gate(Protocol):
    """A gate checks one thing about a message and reports findings.

    `allowed_stages` declares where in the pipeline this gate is meant to run
    (None = anywhere). Binding it elsewhere is a manifest error, so the
    meant-for / not-meant-for scoping is enforced at load time, not documented
    in prose.
    """

    id: str
    allowed_stages: set[str] | None

    def check(self, envelope: Envelope, options: dict) -> list[Finding]: ...


@dataclass
class GateBinding:
    gate: Gate
    binds_to: list[str]
    on_fail: str = "bounce"
    authority: str = "attorney"
    options: dict = field(default_factory=dict)


class GateRefusal(Exception):
    """Raised when advance() is asked to move a message a gate is holding."""


@dataclass
class Pipeline:
    stages: list[str]
    bindings: list[GateBinding]

    def bindings_for(self, stage: str) -> list[GateBinding]:
        return [b for b in self.bindings if stage in b.binds_to]

    def next_stage(self, stage: str) -> str | None:
        idx = self.stages.index(stage)
        return self.stages[idx + 1] if idx + 1 < len(self.stages) else None


class Runner:
    def __init__(self, pipeline: Pipeline, store: st.Store, registry=None):
        self.pipeline = pipeline
        self.store = store
        self.registry = registry

    # -- internals -------------------------------------------------------
    def _run_stage_gates(self, msg_id: int, stage: str, env: Envelope) -> str:
        """Run every gate bound to `stage`; return the resulting status."""
        status = st.OK
        for binding in self.pipeline.bindings_for(stage):
            approved = self.store.approval_roles(msg_id, stage, binding.gate.id)
            if self.registry is None:
                cleared = binding.authority in approved
            else:
                # With a registry, a role whose may_release covers this gate
                # clears it even when the binding names another role.
                cleared = any(
                    self.registry.can_release(r, binding.gate.id, binding.authority)
                    for r in approved
                )
            findings = binding.gate.check(env, binding.options)
            failed = False
            for f in findings:
                self.store.add_finding(msg_id, stage, f.gate_id, f.severity, f.summary)
                if f.severity == SEVERITY_FAIL:
                    failed = True
            if not failed or cleared:
                continue
            if binding.on_fail == "block":
                status = st.BLOCKED
            elif binding.on_fail == "bounce" and status != st.BLOCKED:
                status = st.PENDING_REVIEW
        return status

    def _envelope(self, msg_id: int) -> Envelope:
        """The stored envelope, with attachment bytes read back from disk.

        Gates run again on every advance(), so a gate that inspects
        attachment content must see the same bytes it saw at ingest. The
        store verifies each blob against its recorded digest as it reads.
        """
        import json

        row = self.store.get_message(msg_id)
        if row is None:
            raise KeyError(f"no message {msg_id}")
        d = json.loads(row["envelope_json"])
        blobs = self.store.attachment_bytes(msg_id)
        from .envelope import Attachment

        d["attachments"] = [
            Attachment(**{**a, "content": blobs.get(a["sha256"], b"")})
            for a in d.get("attachments", [])
        ]
        return Envelope(**d)

    # -- public API ------------------------------------------------------
    def enter(self, msg_id: int) -> str:
        """Run the gates of the message's current stage (used at ingest)."""
        row = self.store.get_message(msg_id)
        status = self._run_stage_gates(msg_id, row["stage"], self._envelope(msg_id))
        if status == st.OK and self.pipeline.next_stage(row["stage"]) is None:
            status = st.DONE
        self.store.set_state(msg_id, status=status)
        return status

    def advance(self, msg_id: int) -> str:
        """Move one stage forward. The ONLY forward path; re-checks holds."""
        row = self.store.get_message(msg_id)
        if row is None:
            raise KeyError(f"no message {msg_id}")
        env = self._envelope(msg_id)

        if row["status"] in (st.BLOCKED, st.PENDING_REVIEW):
            # Re-run this stage: a hold clears only if the gates now pass.
            status = self._run_stage_gates(msg_id, row["stage"], env)
            if status != st.OK:
                self.store.set_state(msg_id, status=status)
                raise GateRefusal(
                    f"message {msg_id} is {status} at stage '{row['stage']}';"
                    " approval by the declared authority is required"
                )
            self.store.set_state(msg_id, status=st.OK)

        nxt = self.pipeline.next_stage(row["stage"])
        if nxt is None:
            self.store.set_state(msg_id, status=st.DONE)
            return st.DONE

        status = self._run_stage_gates(msg_id, nxt, env)
        if status == st.OK and self.pipeline.next_stage(nxt) is None:
            status = st.DONE
        self.store.set_state(msg_id, stage=nxt, status=status)
        return status
