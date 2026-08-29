"""Matters moving through stages, gated the same way messages already are.

This is not case management. There is no billing, no trust accounting, no
client portal and no calendar here, and there should never be — that ground
belongs to the practice-management system the firm already pays for. What is
here is the one thing those systems are worst at: a matter cannot reach the
next stage until the record actually supports it, and someone with the
authority to say so has said it.

The engine ships with no opinion about how a case is run. Stages, the order
they come in, and what must be true to leave one are a firm's own workflow,
declared in TOML the same way gates, lint rules and notice adapters are. The
shape is ours; the practice is theirs. A workflow that encoded one firm's
strategy would be worthless to every other firm and would give away the only
part that was hard to learn.

Conditions are deliberately few and all deterministic. Each one reads the
record that is already there — documents, notices, fields — and each carries
its own plain-language reason, so a matter that will not advance can say why
in words a person can act on rather than a rule name.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def article(word: str) -> str:
    """'a paralegal' but 'an attorney' — the message is read by people."""
    return "an" if word[:1].lower() in "aeiou" else "a"


class WorkflowError(ValueError):
    """A malformed workflow refuses to load; it never half-applies."""


@dataclass
class Condition:
    """One checkable fact, and how to say it when it is not true."""
    kind: str            # document | notice | field | stage_before
    value: str

    def describe(self) -> str:
        if self.kind == "document":
            return f"a document classified as '{self.value}' is in the matter"
        if self.kind == "notice":
            return f"a {self.value.replace('_', ' ')} has been received"
        if self.kind == "field":
            return f"'{self.value}' is filled in on the matter"
        return f"{self.kind}:{self.value}"

    def unmet(self) -> str:
        if self.kind == "document":
            return f"no document classified as '{self.value}' is in the matter yet"
        if self.kind == "notice":
            return f"no {self.value.replace('_', ' ')} has been received yet"
        if self.kind == "field":
            return f"'{self.value}' is not filled in on the matter"
        return f"{self.kind}:{self.value} is not satisfied"

    @classmethod
    def parse(cls, raw: str) -> "Condition":
        if ":" not in raw:
            raise WorkflowError(
                f"condition {raw!r} must look like 'document:complaint' —"
                f" one of document, notice, field")
        kind, value = raw.split(":", 1)
        kind, value = kind.strip(), value.strip()
        if kind not in ("document", "notice", "field"):
            raise WorkflowError(
                f"condition {raw!r}: unknown kind {kind!r}"
                " (document, notice or field)")
        if not value:
            raise WorkflowError(f"condition {raw!r} has no value")
        return cls(kind=kind, value=value)


@dataclass
class Transition:
    source: str
    target: str
    requires: list[Condition] = field(default_factory=list)
    authority: str = ""      # "" means no human approval needed to advance


@dataclass
class Workflow:
    matter_type: str
    stages: list[str]
    transitions: list[Transition] = field(default_factory=list)
    as_of: str = ""          # the firm's own date on this workflow

    @property
    def first_stage(self) -> str:
        return self.stages[0]

    def outgoing(self, stage: str) -> list[Transition]:
        return [t for t in self.transitions if t.source == stage]

    def transition(self, source: str, target: str) -> Transition | None:
        for t in self.transitions:
            if t.source == source and t.target == target:
                return t
        return None


@dataclass
class MatterFacts:
    """What the record currently supports, gathered once and passed in.

    The engine never queries anything itself: it is handed the facts and
    decides. That keeps the rules testable without a database and makes it
    possible to run a hypothetical matter through a workflow before saving it.
    """
    documents: set[str] = field(default_factory=set)
    notices: set[str] = field(default_factory=set)
    fields: set[str] = field(default_factory=set)

    def satisfies(self, c: Condition) -> bool:
        if c.kind == "document":
            return c.value in self.documents
        if c.kind == "notice":
            return c.value in self.notices
        if c.kind == "field":
            return c.value in self.fields
        return False


@dataclass
class Blocked:
    """Why a matter is not moving, in words rather than rule names."""
    target: str
    reasons: list[str]
    needs_authority: str = ""


def load_workflow(path: str | Path, registry=None) -> Workflow:
    data = tomllib.loads(Path(path).read_text())
    wf = data.get("workflow", {})
    stages = wf.get("stages") or []
    if not isinstance(stages, list) or len(stages) < 2:
        raise WorkflowError("[workflow].stages must list at least two stages")
    if len(set(stages)) != len(stages):
        raise WorkflowError("[workflow].stages contains a duplicate")
    matter_type = wf.get("matter_type", "").strip()
    if not matter_type:
        raise WorkflowError("[workflow].matter_type is required")

    transitions: list[Transition] = []
    for i, t in enumerate(data.get("transition", []), start=1):
        src, dst = t.get("from", ""), t.get("to", "")
        for label, stage in (("from", src), ("to", dst)):
            if stage not in stages:
                raise WorkflowError(
                    f"transition #{i}: {label} = {stage!r} is not one of the"
                    f" declared stages ({', '.join(stages)})")
        authority = t.get("authority", "").strip()
        if registry is not None:
            registry.check_authority(f"transition {src} -> {dst}", authority)
        transitions.append(Transition(
            source=src, target=dst,
            requires=[Condition.parse(r) for r in t.get("requires", [])],
            authority=authority,
        ))
    # A stage nothing leads out of is a dead end unless it is the last one.
    for s in stages[:-1]:
        if not any(t.source == s for t in transitions):
            raise WorkflowError(
                f"stage '{s}' has no transition out of it — a matter that"
                " reaches it can never move again")
    return Workflow(matter_type=matter_type, stages=stages,
                    transitions=transitions, as_of=wf.get("as_of", ""))


def check(wf: Workflow, stage: str, target: str, facts: MatterFacts) -> Blocked | None:
    """None when the move is allowed; otherwise why not."""
    t = wf.transition(stage, target)
    if t is None:
        return Blocked(target=target,
                       reasons=[f"'{stage}' does not lead to '{target}' in the"
                                f" {wf.matter_type} workflow"])
    unmet = [c.unmet() for c in t.requires if not facts.satisfies(c)]
    if unmet or t.authority:
        return Blocked(target=target, reasons=unmet, needs_authority=t.authority)
    return None


def available(wf: Workflow, stage: str, facts: MatterFacts) -> list[Blocked]:
    """Every move out of this stage and what each one is still waiting on."""
    return [check(wf, stage, t.target, facts) or Blocked(target=t.target, reasons=[])
            for t in wf.outgoing(stage)]


def simulate(wf: Workflow, facts: MatterFacts) -> tuple[list[str], Blocked | None]:
    """Walk a hypothetical matter forward until something stops it.

    This is the sandbox: run a workflow before saving it and watch where it
    holds, rather than being told it is valid. Approvals count as stops —
    a workflow that would sail to the end untouched is usually a workflow
    with no gates in it, and that is worth seeing before it goes live.
    """
    path = [wf.first_stage]
    stage = wf.first_stage
    seen = {stage}
    while True:
        moves = wf.outgoing(stage)
        if not moves:
            return path, None
        blocked = check(wf, stage, moves[0].target, facts)
        if blocked is not None:
            return path, blocked
        stage = moves[0].target
        if stage in seen:                      # a loop in the workflow
            return path, Blocked(target=stage,
                                 reasons=[f"'{stage}' has already been visited;"
                                          " this workflow loops"])
        seen.add(stage)
        path.append(stage)


# ---------------------------------------------------------------------------
# The bridge between the record and the engine. Kept separate and below on
# purpose: check(), available() and simulate() above touch no database, which
# is what lets a workflow be tried out before it is ever saved.
# ---------------------------------------------------------------------------

def facts_from_store(store, case_number: str) -> MatterFacts:
    """What this matter's record currently supports.

    Reads only what is already there. It does not infer, and it does not
    treat an absent record as a satisfied one — a matter with nothing filed
    simply has no facts, and every gate stays shut.
    """
    import json

    facts = MatterFacts()
    matter = store.get_matter(case_number)
    if matter:
        if matter["case_number"]:
            facts.fields.add("case_number")
        if matter["display_name"]:
            facts.fields.add("display_name")

    message_ids: list[int] = []
    for row in store.list_notices():
        fields = json.loads(row["fields_json"])
        if _same_case(fields.get("case_number", ""), case_number):
            facts.notices.add(row["notice_type"])
            message_ids.append(row["message_id"])

    for msg_id in message_ids:
        for att in store.attachments_for(msg_id):
            if att["doc_type"]:
                facts.documents.add(att["doc_type"])
    return facts


def _same_case(a: str, b: str) -> bool:
    from .timeline import normalise_case_number
    return bool(a) and normalise_case_number(a) == normalise_case_number(b)


def workflow_for(home: Path, matter_type: str, registry=None) -> Workflow:
    """Load the firm's workflow for a matter type.

    Docketry ships no workflow of its own. A missing file is not a default to
    fall back on; it is a question for the firm, and the message says so.
    """
    path = Path(home) / "workflows" / f"{matter_type}.toml"
    if not path.exists():
        raise WorkflowError(
            f"no workflow for matter type '{matter_type}'."
            f" Write {path} — examples/workflow-generic.toml is a bare"
            " starting point meant to be rewritten, not used as-is."
        )
    return load_workflow(path, registry)
