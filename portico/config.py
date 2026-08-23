"""Home-directory config for a Portico installation.

A Portico "home" is one directory holding config.toml, the guardrail
manifest, the SQLite store, and attachments — the whole installation is one
folder on the firm's own disk. The IMAP password is read from the
PORTICO_IMAP_PASSWORD environment variable first; storing it in config.toml
is supported for single-machine setups (the file is chmod 0600 on write).
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


@dataclass
class HomeConfig:
    home: Path
    mailbox: MailboxConfig | None
    manifest_path: Path
    store_path: Path


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
    if cfg_path.exists():
        data = tomllib.loads(cfg_path.read_text())
        mb = data.get("mailbox", {})
        password = os.environ.get("PORTICO_IMAP_PASSWORD") or mb.get("password", "")
        if mb.get("host") and mb.get("user"):
            mailbox = MailboxConfig(
                host=mb["host"],
                user=mb["user"],
                password=password,
                folder=mb.get("folder", "INBOX"),
                port=int(mb.get("port", 993)),
            )
    return HomeConfig(
        home=home,
        mailbox=mailbox,
        manifest_path=home / MANIFEST_NAME,
        store_path=home / STORE_DIR,
    )
