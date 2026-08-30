"""Role registry: which roles exist, and what each may release.

Optional. Without roles.toml, an approval's role must equal the gate's
`authority` string exactly, and nothing validates that the authority names a
role the firm has. With it:

  * a gate or workflow naming an undeclared role is refused when config
    loads, rather than when someone needs to release something;
  * `may_release` lets a role clear gates whose authority names another role
    ("*" clears anything), so an attorney can release a paralegal's hold;
  * a person listed under [[person]] cannot approve under a role they do not
    hold.

Docketry has no login. A role here is a string typed at approval time and
checked against this file: it catches mistakes, not lies.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ANY = "*"


class RoleError(ValueError):
    """A malformed registry refuses to load; it never half-applies."""


@dataclass
class Role:
    name: str
    description: str = ""
    may_release: tuple[str, ...] = ()      # gate ids, or ("*",)

    def releases(self, gate_id: str) -> bool:
        return ANY in self.may_release or gate_id in self.may_release


@dataclass
class Registry:
    roles: dict[str, Role] = field(default_factory=dict)
    people: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def known(self, role: str) -> bool:
        return role in self.roles

    def names(self) -> list[str]:
        return sorted(self.roles)

    def can_release(self, role: str, gate_id: str, required: str) -> bool:
        """Either you hold the role the gate names, or yours covers that gate."""
        if role == required:
            return True
        r = self.roles.get(role)
        return bool(r and r.releases(gate_id))

    def roles_of(self, person: str) -> tuple[str, ...] | None:
        """The roles a person is listed with, or None when they are unlisted."""
        return self.people.get(person.strip().casefold())

    def person_may_claim(self, person: str, role: str) -> bool:
        """True if this person may claim this role.

        People are optional: someone unlisted may claim any declared role, so
        a firm need not enumerate its staff before approving anything.
        """
        held = self.roles_of(person)
        return True if held is None else role in held

    def check_authority(self, where: str, role: str) -> None:
        if not role or self.known(role):
            return
        raise RoleError(
            f"{where} requires the role '{role}', which is not declared in"
            f" roles.toml (declared: {', '.join(self.names()) or 'none'})"
        )


def load_roles(path: str | Path) -> Registry:
    return parse_roles(tomllib.loads(Path(path).read_text()))


def parse_roles(data: dict) -> Registry:
    """Validate an already-parsed registry. Same rules, no file needed.

    Used by the wizard to check what it is about to write, through this code
    rather than a second construction path that could disagree with it.
    """
    reg = Registry()
    for i, r in enumerate(data.get("role", []), start=1):
        name = (r.get("name") or "").strip()
        if not name:
            raise RoleError(f"role #{i} has no name")
        if name in reg.roles:
            raise RoleError(f"role '{name}' is declared twice")
        may = r.get("may_release", [])
        if isinstance(may, str):
            may = [may]
        reg.roles[name] = Role(name=name,
                               description=r.get("description", ""),
                               may_release=tuple(may))
    if not reg.roles:
        raise RoleError("roles.toml declares no roles — delete it or fill it in")
    for i, p in enumerate(data.get("person", []), start=1):
        pname = (p.get("name") or "").strip()
        if not pname:
            raise RoleError(f"person #{i} has no name")
        held = tuple(p.get("roles", []))
        if not held:
            raise RoleError(f"'{pname}' is listed with no roles")
        for role in held:
            if role not in reg.roles:
                raise RoleError(
                    f"'{pname}' holds '{role}', which is not a declared role")
        reg.people[pname.casefold()] = held
    return reg


def load_if_present(home: str | Path) -> Registry | None:
    """The registry when the firm has written one, otherwise None."""
    path = Path(home) / "roles.toml"
    return load_roles(path) if path.exists() else None


def refuse_approval(registry, *, person: str, role: str, gate_id: str,
                    required: str) -> str | None:
    """Authorize one approval. Returns the reason to refuse it, or None.

    The CLI and the review UI both call this; the rule lives in one place so
    the two surfaces cannot diverge.

    Without a registry: the recorded role must equal the gate's authority.
    With one: the role must be declared, its may_release must cover the gate
    (or match the authority), and a listed person must actually hold it.
    """
    if registry is None:
        if role != required:
            return (f"gate '{gate_id}' requires role '{required}',"
                    f" not '{role}'")
        return None
    if not registry.known(role):
        return (f"'{role}' is not a declared role"
                f" (declared: {', '.join(registry.names()) or 'none'})")
    if not registry.can_release(role, gate_id, required):
        return (f"gate '{gate_id}' requires '{required}', and '{role}' does not"
                f" release it — add '{gate_id}' to that role's may_release in"
                " roles.toml if it should")
    if not registry.person_may_claim(person, role):
        held = ", ".join(registry.roles_of(person) or ())
        return (f"roles.toml lists {person} as {held}, not '{role}'")
    return None
