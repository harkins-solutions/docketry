"""The Docketry home: config.toml, and what load_home() reads from it.

A home is one directory holding config.toml, guardrails.toml, the SQLite
store and attachment bytes. Nothing is written outside it.

The IMAP password comes from DOCKETRY_IMAP_PASSWORD if set, otherwise from
config.toml. write_config() creates that file with mode 0600 rather than
writing it and tightening it afterwards, which would leave the password
readable in between.

On Windows os.chmod only moves the read-only bit; the file's permissions come
from the directory's ACL. Use the environment variable there — `docketry
init` says so when a password is stored.
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .mailbox import MailboxConfig

CONFIG_NAME = "config.toml"
MANIFEST_NAME = "guardrails.toml"
STORE_DIR = "store"

# Two minutes: a local model on modest hardware answering a long prompt.
DEFAULT_LLM_TIMEOUT = 120.0


@dataclass
class LLMConfig:
    """Where the firm's own model lives, if it configured one.

    The settings live here rather than in the client that uses them, so the
    port can read a config file without importing anything from tools. The
    client that vets the endpoint and speaks to it is docketry.tools.llm.
    """
    base_url: str
    model: str
    timeout: float = DEFAULT_LLM_TIMEOUT


@dataclass
class HomeConfig:
    home: Path
    mailbox: MailboxConfig | None
    manifest_path: Path
    store_path: Path
    # None unless the firm configured a model. Nothing calls one by default.
    llm: LLMConfig | None = None
    # Filled in by the CLI once roles.toml has been read.
    registry: object | None = None
    # Your own domains, so inbound can be split internal/external.
    firm_domains: tuple = ()
    directory: object | None = None


def write_config(
    home: Path,
    *,
    host: str,
    user: str,
    folder: str = "INBOX",
    password: str | None = None,
) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / CONFIG_NAME
    lines = [
        "[mailbox]",
        f'host = "{host}"',
        f'user = "{user}"',
        f'folder = "{folder}"',
    ]
    if password:
        lines.append(f'password = "{password}"')
    lines.append("")
    # Create at 0600 rather than writing then chmod'ing: the latter leaves
    # the password on disk at the umask's permissions in between, and a
    # process that opened it in that window keeps its handle afterwards.
    mode = stat.S_IRUSR | stat.S_IWUSR
    if cfg.exists():
        os.chmod(cfg, mode)      # O_CREAT's mode is ignored for an existing file
    fd = os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines))
    return cfg


def load_home(home: str | Path) -> HomeConfig:
    home = Path(home)
    cfg_path = home / CONFIG_NAME
    mailbox = None
    llm = None
    firm_domains = ()
    if cfg_path.exists():
        data = tomllib.loads(cfg_path.read_text())
        mb = data.get("mailbox", {})
        password = os.environ.get("DOCKETRY_IMAP_PASSWORD") or mb.get("password", "")
        if mb.get("host") and mb.get("user"):
            mailbox = MailboxConfig(
                host=mb["host"],
                user=mb["user"],
                password=password,
                folder=mb.get("folder", "INBOX"),
                port=int(mb.get("port", 993)),
            )
        firm_domains = tuple(data.get("firm", {}).get("domains", []))
        ml = data.get("llm", {})
        if ml.get("base_url") and ml.get("model"):
            # Not validated here: an unreachable or public endpoint must be
            # reported by `doctor`, not raised while merely reading config.
            llm = LLMConfig(
                base_url=ml["base_url"],
                model=ml["model"],
                timeout=float(ml.get("timeout", 120.0)),
            )
    return HomeConfig(
        home=home,
        mailbox=mailbox,
        manifest_path=home / MANIFEST_NAME,
        store_path=home / STORE_DIR,
        llm=llm,
        firm_domains=firm_domains,
    )
