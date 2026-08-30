"""Home-directory config for a Docketry installation.

A Docketry "home" is one directory holding config.toml, the guardrail
manifest, the SQLite store, and attachments — the whole installation is one
folder on the firm's own disk. The IMAP password is read from the
DOCKETRY_IMAP_PASSWORD environment variable first; storing it in config.toml
is supported for single-machine setups (the file is created 0600).

That 0600 is a POSIX guarantee and nothing more. On Windows os.chmod moves the
read-only bit and says nothing about who else may read the file, which inherits
its ACL from the directory — so on Windows the environment variable is the only
way to keep the password off a readable disk, and `init` says so out loud
rather than leaving the number to imply a protection it does not have.
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
    # Created 0600, not fixed up afterwards. write_text() followed by chmod
    # leaves a window — short, but a window — in which the password sits on
    # disk at whatever the umask allows, and any process that opened it in
    # that window keeps its handle after the mode changes.
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
