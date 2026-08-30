"""`docketry init`, as questions rather than three files to author.

The install path used to assume someone comfortable with pip, an IMAP app
password, a TOML manifest and a home directory. At a six-attorney firm that
person frequently does not exist, and whoever gets closest becomes the
permanent maintainer of a config surface they did not choose. So the config
surface is not something anyone has to author: this asks in plain words and
writes guardrails.toml and roles.toml from the answers.

Two rules it keeps. Nothing here is a hidden default — every answer is
written into the file as ordinary TOML with a comment saying what it does, so
the firm can read and change it afterwards without running this again. And it
never writes a home that refuses to load: the manifest and the registry are
built in memory and validated first, so a wizard that produced a broken
install would fail before touching the disk rather than after.
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
    ask.say("Docketry setup. Nine questions, all changeable afterwards by")
    ask.say("editing the files this writes. Enter accepts the [default].")

    ask.say()
    ask.say("-- The mailbox Docketry watches ------------------------------")
    ask.say("Point a forwarding rule at a mailbox nobody works out of, and")
    ask.say("Docketry reads it. It never sends, and never deletes.")
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
    ask.say("When a gate holds a message, someone has to say so on the record.")
    ask.say("Use whatever your firm actually calls these jobs.")
    a.reviewer = ask.text("Who reviews intake day to day", "paralegal",
                          required=True)
    a.releaser = ask.text("Who can release anything, including a conflict hold",
                          "attorney", required=True)

    ask.say()
    ask.say("-- The ethical wall ------------------------------------------")
    ask.say("Names or matters that must not move through intake unreviewed.")
    ask.say("Anything mentioning one stops, and only the release is on the")
    ask.say("record — with who, and when. Blank if you have no wall today.")
    a.screened = ask.items("Screened names or matters (comma separated)")

    ask.say()
    ask.say("-- Court e-service -------------------------------------------")
    ask.say("Docketry parses service and docket notices into dates and case")
    ask.say("numbers, and holds any that it cannot read rather than guessing.")
    a.eservice = ask.items(
        "Addresses or domains court e-service arrives from",
        tuple(DEFAULT_ESERVICE))

    ask.say()
    ask.say("-- Attachment hygiene ----------------------------------------")
    ask.say("Pipeline policy, not antivirus: what intake will accept.")
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
# that gate even when the gate names someone else, so nobody senior is blocked
# by a hold meant for someone junior. "*" releases anything.
#
# A role here is an attestation recorded against a name. Docketry has no login.
# This catches mistakes; it does not authenticate anyone.

[[role]]
name = "{a.reviewer}"
description = "Clears routine intake holds."
may_release = {_toml_list(routine)}

[[role]]
name = "{a.releaser}"
description = "Releases anything, including a conflict hold."
may_release = ["*"]

# Optional: list people and Docketry refuses a role they do not hold. Leave it
# out and any name may claim any role.
# [[person]]
# name = "Dana Reyes"
# roles = ["{a.reviewer}"]
"""


def guardrails_toml(a: Answers) -> str:
    """The manifest, in the order the gates actually matter to a firm."""
    parts = ["""\
# Docketry guardrail manifest. Written by `docketry init` from your answers.
# Stages run left to right; each [[gate]] declares where it binds, what happens
# on failure (block | bounce | warn), and which role can release it.

[pipeline]
stages = ["ingest", "review"]
"""]

    if a.screened:
        parts.append(f"""
# The ethical wall. A message mentioning one of these stops here, and only a
# recorded release by {a.releaser} moves it — which is the record that matters
# if the screen is ever the thing being questioned.
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
# Court e-service. A notice this cannot read is held rather than guessed at,
# because a misread hearing date is worse than an unread one.
[[gate]]
id = "notice-parser"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"
""")

    if a.eservice:
        parts.append(f"""
# Who intake accepts mail from. Everything else parks for a look.
[[gate]]
id = "sender-scope"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"

[gate.options]
allow = {_toml_list(list(a.eservice) + ['@' + d for d in a.firm_domains])}
""")

    parts.append(f"""
# Pipeline hygiene, not antivirus: what intake will accept.
[[gate]]
id = "attachment-policy"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "{a.reviewer}"

[gate.options]
max_size_mb = {a.max_size_mb:g}

# Records where each message came from. Never holds anything.
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

    from .manifest import build_pipeline
    from .roles import parse_roles

    registry = parse_roles(tomllib.loads(roles_toml(a)))
    build_pipeline(tomllib.loads(guardrails_toml(a)), registry)


def write(home: Path, a: Answers) -> list[Path]:
    """Write config.toml, roles.toml and guardrails.toml. Validated first."""
    from .config import CONFIG_NAME, MANIFEST_NAME, write_config

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
