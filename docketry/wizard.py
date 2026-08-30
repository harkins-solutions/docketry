"""`docketry init` as a set of questions, and the files it writes.

Asks nine questions and writes config.toml, guardrails.toml and roles.toml
from the answers, each with comments explaining the setting so the files can
be edited afterwards without re-running this.

The manifest and the role registry are built in memory and validated by the
same loaders that read them from disk (validate()), so answers that would not
load fail before anything is written.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Where the common providers actually keep IMAP. A firm on Microsoft 365 with
# its own domain cannot be guessed from the address, so the guess is offered
# as a default and never forced.
IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com",
    "office365.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "aol.com": "imap.aol.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "zoho.com": "imap.zoho.com",
    "fastmail.com": "imap.fastmail.com",
}

# The senders court e-service actually comes from, as shipped. A firm outside
# Florida will replace these; the point is that the list starts populated
# rather than empty, because an empty allow list holds everything.
DEFAULT_ESERVICE = ["@myflcourtaccess.com", "@uscourts.gov"]


class WizardAborted(RuntimeError):
    """The operator stopped, or there was no one there to answer."""


def imap_guess(address: str) -> str:
    domain = address.partition("@")[2].strip().lower()
    if not domain:
        return ""
    return IMAP_HOSTS.get(domain, f"imap.{domain}")


@dataclass
class Answers:
    """Everything the wizard asked, in the order it asked it."""
    address: str = ""
    host: str = ""
    folder: str = "INBOX"
    store_password: bool = False
    password: str = ""
    firm_domains: tuple[str, ...] = ()
    reviewer: str = "paralegal"
    releaser: str = "attorney"
    screened: tuple[str, ...] = ()
    eservice: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_ESERVICE))
    max_size_mb: float = 25.0


class Asker:
    """Prompts, with the defaults visible and an explanation above each one."""

    def __init__(self, input_fn=input, out=print, getpass_fn=None):
        self._input = input_fn
        self._out = out
        self._getpass = getpass_fn

    def say(self, text: str = "") -> None:
        self._out(text)

    def _read(self, prompt: str) -> str:
        try:
            return self._input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise WizardAborted(
                "setup stopped — nothing was written. Run `docketry init` again,"
                " or pass --host and --user to skip the questions."
            ) from None

    def text(self, question: str, default: str = "", *, required: bool = False) -> str:
        while True:
            shown = f" [{default}]" if default else ""
            answer = self._read(f"{question}{shown}: ") or default
            if answer or not required:
                return answer
            self.say("  (this one is needed)")

    def secret(self, question: str) -> str:
        if self._getpass is None:
            import getpass
            self._getpass = getpass.getpass
        try:
            return self._getpass(f"{question}: ")
        except (EOFError, KeyboardInterrupt):
            raise WizardAborted("setup stopped — nothing was written.") from None

    def yes_no(self, question: str, default: bool = True) -> bool:
        shown = "Y/n" if default else "y/N"
        while True:
            answer = self._read(f"{question} [{shown}]: ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("  (please answer y or n)")

    def items(self, question: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        """A comma-separated list. Typing `none` clears a populated default."""
        shown = ", ".join(default)
        answer = self.text(question, shown)
        if answer.strip().lower() in ("none", "-"):
            return ()
        return tuple(p.strip() for p in answer.split(",") if p.strip())

    def number(self, question: str, default: float) -> float:
        while True:
            answer = self.text(question, f"{default:g}")
            try:
                return float(answer)
            except ValueError:
                self.say("  (a number, please)")


def interview(ask: Asker) -> Answers:
    """Ask the questions. Writes nothing; returns what was said."""
    a = Answers()
    ask.say("Docketry setup: nine questions. Everything you answer is written")
    ask.say("to files you can edit later. Enter accepts the [default].")

    ask.say()
    ask.say("-- The mailbox Docketry watches ------------------------------")
    ask.say("Point a forwarding rule at a mailbox nobody works out of.")
    ask.say("Docketry reads it read-only: it never sends, marks or deletes.")
    # No default here on purpose: a placeholder default means pressing Enter
    # configures a mailbox that does not exist.
    a.address = ask.text("Intake mailbox address (e.g. intake@yourfirm.com)",
                         required=True)
    a.host = ask.text("IMAP host for that mailbox", imap_guess(a.address),
                      required=True)
    a.folder = ask.text("Folder to read", "INBOX")
    a.store_password = ask.yes_no(
        "Store the mailbox password in config.toml? (no = read it from"
        " DOCKETRY_IMAP_PASSWORD)", False)
    if a.store_password:
        a.password = ask.secret("Mailbox password or app password")
    domain = a.address.partition("@")[2].strip().lower()
    a.firm_domains = ask.items(
        "Your firm's own email domains, so inbound can be split internal from"
        " external", (domain,) if domain else ())

    ask.say()
    ask.say("-- Who releases a hold ---------------------------------------")
    ask.say("Releasing a hold records a name and a role. Use whatever your")
    ask.say("firm calls these jobs; the names go into roles.toml as written.")
    a.reviewer = ask.text("Who reviews intake day to day", "paralegal",
                          required=True)
    a.releaser = ask.text("Who can release anything, including a conflict hold",
                          "attorney", required=True)

    ask.say()
    ask.say("-- The ethical wall ------------------------------------------")
    ask.say("Parties or matters to screen. Any message naming one stops until")
    ask.say("released, and the release is recorded with a name and a time.")
    ask.say("Leave blank if you have no wall to enforce.")
    a.screened = ask.items("Screened names or matters (comma separated)")

    ask.say()
    ask.say("-- Court e-service -------------------------------------------")
    ask.say("Service and docket notices are parsed into case numbers, titles")
    ask.say("and dates. Anything that will not parse is held, not guessed at.")
    a.eservice = ask.items(
        "Addresses or domains court e-service arrives from",
        tuple(DEFAULT_ESERVICE))

    ask.say()
    ask.say("-- Attachment hygiene ----------------------------------------")
    ask.say("Extension and size limits on attachments. Not antivirus.")
    a.max_size_mb = ask.number("Hold attachments larger than (MB)", 25.0)
    return a


def _toml_list(values) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def roles_toml(a: Answers) -> str:
    """The registry: the two roles the answers named, and what each releases."""
    routine = ["sender-scope", "attachment-policy", "notice-parser",
               "provenance-stamp"]
    return f"""\
# Who may release what. Written by `docketry init` from your answers.
#
# may_release lets seniority work: a role listed against a gate id can release
# that gate even when the gate's `authority` names a different role. "*"
# releases anything. Without this file, an approval's role must match the
# gate's authority exactly.
#
# Docketry has no login. A role is a name typed at approval time and checked
# against this file, which catches mistakes; it does not authenticate anyone.

[[role]]
name = "{a.reviewer}"
description = "Clears routine intake holds."
may_release = {_toml_list(routine)}

[[role]]
name = "{a.releaser}"
description = "Releases anything, including a conflict hold."
may_release = ["*"]

# Optional. List a person and they cannot approve under a role not listed
# here. Leave them out and any name may claim any declared role.
# [[person]]
# name = "Dana Reyes"
# roles = ["{a.reviewer}"]
"""


def guardrails_toml(a: Answers) -> str:
    """The manifest, in the order the gates actually matter to a firm."""
    parts = ["""\
# Docketry guardrail manifest. Written by `docketry init` from your answers.
#
# Stages run left to right. Each [[gate]] declares the stages it binds to,
# what a failing check does (block = stop, bounce = review queue, warn =
# record and continue), and which role may release it.

[pipeline]
stages = ["ingest", "review"]
"""]

    if a.screened:
        parts.append(f"""
# Ethical wall. Terms are matched case-insensitively on word boundaries in
# the subject, body and attachment filenames. A match stops the message; only
# a recorded release by {a.releaser} moves it, and that release is stored with
# the approver's name, role and timestamp.
[[gate]]
id = "name-screen"
binds_to = ["ingest"]
on_fail = "block"
authority = "{a.releaser}"

[gate.options]
terms = {_toml_list(a.screened)}
note = "ethical wall"
""")

    parts.append(f"""
# Court e-service. Extracts case numbers, document titles and dates. A notice
# the adapters cannot read is held rather than partially parsed.
[[gate]]
id = "notice-parser"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"
""")

    if a.eservice:
        parts.append(f"""
# Senders intake accepts. An address or an @domain suffix; anything else is
# held for review.
[[gate]]
id = "sender-scope"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"

[gate.options]
allow = {_toml_list(list(a.eservice) + ['@' + d for d in a.firm_domains])}
""")

    parts.append(f"""
# Attachment policy: extension and size limits. Not antivirus.
[[gate]]
id = "attachment-policy"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"

[gate.options]
max_size_mb = {a.max_size_mb:g}

# Records source and hashes for each message. Informational; never holds.
[[gate]]
id = "provenance-stamp"
binds_to = ["ingest"]
on_fail = "warn"
authority = "{a.reviewer}"
""")
    return "".join(parts)


def config_toml_extras(a: Answers) -> str:
    """The [firm] block; the mailbox block is written by config.write_config."""
    if not a.firm_domains:
        return ""
    return f"\n[firm]\ndomains = {_toml_list(a.firm_domains)}\n"


def validate(a: Answers) -> None:
    """Build the manifest and the registry in memory. Nothing is written yet.

    A wizard that leaves behind a home which refuses to load has done worse
    than nothing: the person running it has no way to tell whether the tool is
    broken or their answers were.
    """
    import tomllib

    from .core.manifest import build_pipeline
    from .core.roles import parse_roles

    registry = parse_roles(tomllib.loads(roles_toml(a)))
    build_pipeline(tomllib.loads(guardrails_toml(a)), registry)


def write(home: Path, a: Answers) -> list[Path]:
    """Write config.toml, roles.toml and guardrails.toml. Validated first."""
    from .core.config import CONFIG_NAME, MANIFEST_NAME, write_config

    validate(a)
    home.mkdir(parents=True, exist_ok=True)
    cfg = write_config(home, host=a.host, user=a.address, folder=a.folder,
                       password=a.password or None)
    extras = config_toml_extras(a)
    if extras:
        with open(cfg, "a") as fh:
            fh.write(extras)
    manifest = home / MANIFEST_NAME
    manifest.write_text(guardrails_toml(a))
    roles = home / "roles.toml"
    roles.write_text(roles_toml(a))
    assert cfg.name == CONFIG_NAME
    return [cfg, manifest, roles]


def run(home: Path, ask: Asker | None = None) -> list[Path]:
    """Ask, then write. Returns the files written, in the order written."""
    ask = ask or Asker()
    answers = interview(ask)
    written = write(Path(home), answers)
    ask.say()
    ask.say(f"Written to {home}:")
    for path in written:
        ask.say(f"  {path.name}")
    ask.say()
    if answers.screened:
        ask.say(f"The wall is live: {len(answers.screened)} screened"
                f" name(s), released only by {answers.releaser}.")
    if not answers.store_password:
        ask.say("Set DOCKETRY_IMAP_PASSWORD in the environment before polling.")
    ask.say("Next: point a forwarding rule at " + answers.address +
            ", then run `docketry poll`.")
    return written


def available(stdin=None) -> bool:
    """Whether there is a person there to answer. Scripts get the flags."""
    stream = stdin if stdin is not None else sys.stdin
    try:
        return bool(stream) and stream.isatty()
    except (AttributeError, ValueError):
        return False
