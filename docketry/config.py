"""Home-directory config for a Docketry installation.

A Docketry "home" is one directory holding config.toml, the guardrail
manifest, the SQLite store, and attachments — the whole installation is one
folder on the firm's own disk. The IMAP password is read from the
DOCKETRY_IMAP_PASSWORD environment variable first; storing it in config.toml
is supported for single-machine setups (the file is chmod 0600 on write).
"""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .llm import LLMConfig
from .mailbox import MailboxConfig

CONFIG_NAME = "config.toml"
MANIFEST_NAME = "guardrails.toml"
STORE_DIR = "store"


@dataclass
class HomeConfig:
    home: Path
    mailbox: MailboxConfig | None
    manifest_path: Path
    store_path: Path
    # None unless the firm configured a model. Nothing calls one by default.
    llm: LLMConfig | None = None


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
    cfg.write_text("\n".join(lines))
    os.chmod(cfg, stat.S_IRUSR | stat.S_IWUSR)
    return cfg


def load_home(home: str | Path) -> HomeConfig:
    home = Path(home)
    cfg_path = home / CONFIG_NAME
    mailbox = None
    llm = None
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
    )
