"""Who an address belongs to, so the pipeline can tell them apart.

Two axes, deliberately not one. A contact's KIND is what they are to the firm —
staff, client, opposing counsel, the court. Their ROLES are what they may
release, and only make sense for staff. Collapsing the two would make
"opposing counsel" the sort of thing that could clear a hold, which is not a
mistake worth leaving available.

Kind is what the rest of the system was missing. The timeline has always
declared a `client` layer, described it as privileged and different in kind,
and then never put anything in it, because nothing knew which addresses
belonged to the client. A layer that is always empty is worse than no layer:
somebody filters by it, sees nothing, and concludes there was no client
communication.

Keyed by EMAIL, not by name. An address is unique, comparable, and already on
every message; a name is typed by hand, and one person spelling theirs three
ways is three people. Names live here too, but only so a report can read
nicely.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STAFF = "staff"
CLIENT = "client"
OPPOSING_COUNSEL = "opposing_counsel"
COURT = "court"
EXPERT = "expert"
VENDOR = "vendor"
OTHER = "other"
KINDS = (STAFF, CLIENT, OPPOSING_COUNSEL, COURT, EXPERT, VENDOR, OTHER)

# Kinds whose mail is privileged and must not sit in the same list as
# correspondence with the other side.
PRIVILEGED = (CLIENT,)


class ContactError(ValueError):
    """A malformed directory refuses to load; it never half-applies."""


@dataclass
class Contact:
    email: str
    name: str = ""
    kind: str = OTHER
    roles: tuple[str, ...] = ()
    note: str = ""

    @property
    def privileged(self) -> bool:
        return self.kind in PRIVILEGED

    @property
    def label(self) -> str:
        return self.name or self.email


@dataclass
class Directory:
    by_email: dict[str, Contact] = field(default_factory=dict)
    domains: dict[str, Contact] = field(default_factory=dict)

    def find(self, address: str) -> Contact | None:
        """Exact address first, then a whole-domain entry.

        A firm can name one address or claim a domain — '@theirfirm.com' is
        how you say "everyone there is opposing counsel" without listing them.
        """
        if not address:
            return None
        addr = address.strip().lower()
        hit = self.by_email.get(addr)
        if hit:
            return hit
        return self.domains.get(addr.split("@")[-1])

    def kind_of(self, address: str) -> str:
        c = self.find(address)
        return c.kind if c else OTHER

    def is_privileged(self, address: str) -> bool:
        c = self.find(address)
        return bool(c and c.privileged)

    def may_claim(self, address_or_name: str, role: str) -> bool:
        """Unlisted people are not refused — a firm should not have to
        enumerate everyone before anyone can approve anything."""
        c = self.find(address_or_name)
        if c is None:
            needle = address_or_name.strip().casefold()
            for known in self.by_email.values():
                if known.name.casefold() == needle:
                    c = known
                    break
        return True if c is None else role in c.roles

    def __len__(self) -> int:
        return len(self.by_email) + len(self.domains)


def load_contacts(path: str | Path, registry=None) -> Directory:
    data = tomllib.loads(Path(path).read_text())
    d = Directory()
    for i, row in enumerate(data.get("contact", []), start=1):
        email = (row.get("email") or "").strip().lower()
        if not email:
            raise ContactError(f"contact #{i} has no email")
        kind = (row.get("kind") or OTHER).strip()
        if kind not in KINDS:
            raise ContactError(
                f"{email}: kind '{kind}' is not one of {', '.join(KINDS)}")
        roles = tuple(row.get("roles", []))
        if roles and kind != STAFF:
            raise ContactError(
                f"{email}: only a staff contact holds roles — '{kind}' contacts"
                " are people the firm deals with, not people who may release"
                " a hold")
        if registry is not None:
            for r in roles:
                registry.check_authority(f"contact {email}", r)
        contact = Contact(email=email, name=(row.get("name") or "").strip(),
                          kind=kind, roles=roles,
                          note=(row.get("note") or "").strip())
        if email.startswith("@"):
            d.domains[email.lstrip("@")] = contact
        elif "@" not in email:
            raise ContactError(
                f"'{email}' is neither an address nor a domain — write"
                " someone@firm.com, or @firm.com for everyone there")
        else:
            d.by_email[email] = contact
    return d


def load_if_present(home: str | Path, registry=None) -> Directory | None:
    path = Path(home) / "contacts.toml"
    return load_contacts(path, registry) if path.exists() else None
