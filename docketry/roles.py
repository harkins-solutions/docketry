"""Who may release what — declared by the firm, checked when config loads.

Roles were free text: a gate said `authority = "paralegal"` and nothing
anywhere knew whether such a role existed. A typo in a manifest failed closed
at five o'clock on a Friday, a firm that calls the job "associate" could have
a gate quietly demanding "attorney", and — the one that actually bites — an
attorney could not release a hold marked for a paralegal, because clearing
compared the two strings and found them different.

Declaring roles fixes all three, and it does it at load time, where a mistake
is cheap. `may_release` is what lets seniority work: an attorney with `["*"]`
can release anything, so nobody is blocked by a gate meant for someone junior.

Read the limit plainly. Docketry has no login and is not going to grow one:
a role here is an ATTESTATION, recorded against a name, not an authenticated
identity. This registry stops mistakes. It does not stop lies, and anything
that suggested otherwise would be worse than leaving it out.

The file is optional. Without it nothing is validated, which is how every
existing installation already works — but `doctor` says so out loud, because
"unchecked" should never be something you discover later.
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

    def person_may_claim(self, person: str, role: str) -> bool:
        """People are optional. Someone unlisted is not refused — a firm should
        not have to enumerate its staff before it can approve anything."""
        held = self.people.get(person.strip().casefold())
        return True if held is None else role in held

    def check_authority(self, where: str, role: str) -> None:
        if not role or self.known(role):
            return
        raise RoleError(
            f"{where} requires the role '{role}', which is not declared in"
            f" roles.toml (declared: {', '.join(self.names()) or 'none'})"
        )


def load_roles(path: str | Path) -> Registry:
    data = tomllib.loads(Path(path).read_text())
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
