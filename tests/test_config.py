import os
import stat
import tempfile
import unittest
from pathlib import Path

from portico.config import load_home, write_config


class TestConfig(unittest.TestCase):
    def test_write_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cfg = write_config(home, host="imap.x.com", user="intake@f.com", folder="Intake")
            mode = stat.S_IMODE(os.stat(cfg).st_mode)
            self.assertEqual(mode, 0o600)
            loaded = load_home(home)
            self.assertEqual(loaded.mailbox.host, "imap.x.com")
            self.assertEqual(loaded.mailbox.user, "intake@f.com")
            self.assertEqual(loaded.mailbox.folder, "Intake")
            self.assertEqual(loaded.mailbox.port, 993)

    def test_env_password_wins_over_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            write_config(home, host="h", user="u", password="stored-secret")
            os.environ["PORTICO_IMAP_PASSWORD"] = "env-secret"
            try:
                self.assertEqual(load_home(home).mailbox.password, "env-secret")
            finally:
                del os.environ["PORTICO_IMAP_PASSWORD"]
            self.assertEqual(load_home(home).mailbox.password, "stored-secret")

    def test_missing_config_means_no_mailbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_home(tmp).mailbox)


if __name__ == "__main__":
    unittest.main()
