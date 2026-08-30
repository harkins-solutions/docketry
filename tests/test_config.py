import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docketry.config import load_home, write_config

POSIX = os.name != "nt"


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
            os.environ["DOCKETRY_IMAP_PASSWORD"] = "env-secret"
            try:
                self.assertEqual(load_home(home).mailbox.password, "env-secret")
            finally:
                del os.environ["DOCKETRY_IMAP_PASSWORD"]
            self.assertEqual(load_home(home).mailbox.password, "stored-secret")

    @unittest.skipUnless(POSIX, "0600 is a POSIX guarantee")
    def test_the_password_file_is_created_0600_not_widened_then_fixed(self):
        # write_text() then chmod puts the password on disk at the umask's
        # permissions first. The window is small; the password is not.
        seen = {}
        real_open = os.open

        def spy(path, flags, mode=0o777, *a, **kw):
            seen[str(path)] = (flags, mode)
            return real_open(path, flags, mode, *a, **kw)

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch("docketry.config.os.open", spy):
                cfg = write_config(home, host="h", user="u", password="hunter2")
            flags, mode = seen[str(cfg)]
            self.assertEqual(mode, 0o600, "created at the wrong mode")
            self.assertTrue(flags & os.O_CREAT)
            self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o600)
            self.assertIn("hunter2", cfg.read_text())

    @unittest.skipUnless(POSIX, "0600 is a POSIX guarantee")
    def test_rewriting_a_loose_config_tightens_it_first(self):
        # O_CREAT's mode is ignored when the file already exists, so an old
        # 0644 config would keep its permissions and gain a password.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / "config.toml").write_text("[mailbox]\n")
            os.chmod(home / "config.toml", 0o644)
            cfg = write_config(home, host="h", user="u", password="hunter2")
            self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o600)

    def test_missing_config_means_no_mailbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_home(tmp).mailbox)


if __name__ == "__main__":
    unittest.main()
