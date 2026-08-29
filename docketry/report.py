"""Pipeline health: is the machine working, and is the config still right.

Not a people report. Docketry has no login, so the only names it holds are the
free-text strings typed into an approval, where one person spelling their own
name three ways is three people to the database and anyone can type anyone
else's. Numbers built on that field would be both wrong and unfalsifiable,
which is the practical reason before the obvious one. Approvals are counted by ROLE, and turnaround
is measured per GATE: the answerable question is which check is the
bottleneck, not who was slowest to click it.

What this is for is the two things nobody notices by hand. Configuration rots
— a gate written eight months ago that has never once fired is not protecting
anything, and a court adapter that produced forty notices a month and now
produces none means that court changed its template. Both are silent, and both
are visible here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank. With a handful of samples a mean hides the bad days."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[k], 1)


_NOREPLY = ("noreply", "no-reply", "donotreply", "do-not-reply",
            "mailer-daemon", "postmaster", "notification", "automated")


def _is_noreply(address: str) -> bool:
    return any(m in address.split("@")[0].lower() for m in _NOREPLY)


def domain_of(address: str) -> str:
    return address.split("@")[-1].strip().lower() if "@" in address else ""


@dataclass
class Report:
    days: int
    ingested: int = 0
    by_status: dict = field(default_factory=dict)
    by_domain: list = field(default_factory=list)        # (domain, count)
    # Announcements and conversations counted apart. A court's four hundred
    # automatic notices and the twelve emails someone actually has to answer
    # are different work, and averaging them together hides the second.
    notifications: list = field(default_factory=list)    # (domain, count)
    correspondence: list = field(default_factory=list)   # (domain, count)
    by_kind: list = field(default_factory=list)          # (kind, count)
    internal: int = 0
    external: int = 0
    unknown_side: int = 0
    turnaround: dict = field(default_factory=dict)       # gate -> {n,p50,p90}
    silent_gates: list = field(default_factory=list)
    quiet_adapters: list = field(default_factory=list)   # (adapter, was, now)
    hold_reasons: list = field(default_factory=list)
    documents_not_held: int = 0
    stuck_matters: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.silent_gates or self.quiet_adapters
                    or self.documents_not_held or self.stuck_matters)


def build(store, pipeline=None, *, days: int = 30, firm_domains=(),
          stuck_after_days: float = 30.0, directory=None) -> Report:
    rep = Report(days=days)
    base = store.stats(days)
    rep.ingested = base["ingested"]
    rep.by_status = base["by_status"]

    # Where the volume comes from.
    counts: dict[str, int] = {}
    one_way: dict[str, int] = {}
    two_way: dict[str, int] = {}
    kinds: dict[str, int] = {}
    firm = {d.strip().lower().lstrip("@") for d in firm_domains if d.strip()}
    for addr, n, machine, noticed in store.sender_profile(days):
        d = domain_of(addr)
        if not d:
            rep.unknown_side += n
            continue
        counts[d] = counts.get(d, 0) + n
        # One-way if the sender declared it, an adapter recognised it as a
        # court notice, or the address is a noreply. Any one is enough.
        announces = machine > 0 or noticed > 0 or _is_noreply(addr)
        bucket = one_way if announces else two_way
        bucket[d] = bucket.get(d, 0) + n
        if directory is not None and not announces:
            # Who they are, not just where they are. "Twelve from opposing
            # counsel" is a different fact from "twelve from a domain".
            kinds[directory.kind_of(addr)] = (
                kinds.get(directory.kind_of(addr), 0) + n)
        if d in firm:
            rep.internal += n
        else:
            rep.external += n
    rep.by_domain = sorted(counts.items(), key=lambda kv: -kv[1])
    rep.notifications = sorted(one_way.items(), key=lambda kv: -kv[1])
    rep.correspondence = sorted(two_way.items(), key=lambda kv: -kv[1])
    rep.by_kind = sorted(kinds.items(), key=lambda kv: -kv[1])
    if not firm:
        rep.notes.append(
            "internal and external cannot be separated: no firm domains are"
            " configured. Add [firm] domains = [\"yourfirm.com\"] to config.toml."
        )

    # How long each CHECK held things, not each person.
    for gate, hours in store.release_hours_by_gate(days).items():
        rep.turnaround[gate] = {"n": len(hours),
                                "p50": percentile(hours, 50),
                                "p90": percentile(hours, 90)}

    # Configuration that is not doing anything.
    if pipeline is not None:
        fired = set(base["holds_by_gate"]) | set(rep.turnaround)
        declared = {b.gate.id for b in getattr(pipeline, "bindings", [])}
        rep.silent_gates = sorted(declared - fired)

    # A court that has gone quiet usually changed its template.
    now = store.adapter_counts(days)
    before = store.adapter_counts(days, offset=days)
    rep.quiet_adapters = sorted(
        (name, was, now.get(name, 0))
        for name, was in before.items() if was > 0 and now.get(name, 0) == 0)

    rep.hold_reasons = store.hold_reasons(days)[:10]
    rep.documents_not_held = store.documents_not_held(days)
    rep.stuck_matters = [(c, s, d) for c, s, d in store.matters_by_age()
                         if d >= stuck_after_days]
    return rep
