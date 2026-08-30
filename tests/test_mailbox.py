import unittest
from email.message import EmailMessage
from unittest import mock

from docketry.core import mailbox as mb


def raw_msg(subject):
    m = EmailMessage()
    m["From"] = "a@b.c"
    m["Subject"] = subject
    m.set_content("body")
    return bytes(m)


class FakeIMAP:
    """Stub of imaplib.IMAP4_SSL — enough surface for IntakeMailbox."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.calls = []
        self.messages = {2: raw_msg("two"), 3: raw_msg("three")}
        self.select_ok = True

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return ("OK" if self.select_ok else "NO"), [b"2"]

    def response(self, key):
        if key == "UIDVALIDITY":
            return "OK", [b"77"]
        return "OK", [None]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            # IMAP quirk under test: "n:*" returns the highest UID even when
            # n exceeds it, so stale UIDs come back and must be skipped.
            return "OK", [b"2 3"]
        if cmd == "FETCH":
            uid = int(args[0])
            return "OK", [(f"{uid} (RFC822 {{x}}".encode(), self.messages[uid]), b")"]
        raise AssertionError(cmd)

    def logout(self):
        self.calls.append(("logout",))


class TestIntakeMailbox(unittest.TestCase):
    def _open(self, fake):
        with mock.patch.object(mb.imaplib, "IMAP4_SSL", return_value=fake):
            return mb.IntakeMailbox(
                mb.MailboxConfig(host="h", user="u", password="p")
            ).__enter__()

    def test_opens_readonly_always(self):
        fake = FakeIMAP("h", 993)
        box = self._open(fake)
        self.assertIn(("select", "INBOX", True), fake.calls)
        self.assertEqual(box.uidvalidity(), 77)
        box.__exit__(None, None, None)
        self.assertIn(("logout",), fake.calls)

    def test_new_messages_skips_already_swept_uids(self):
        box = self._open(FakeIMAP("h", 993))
        got = list(box.new_messages(last_uid=2))
        self.assertEqual([uid for uid, _ in got], [3])
        self.assertIn(b"three", got[0][1])
        box.__exit__(None, None, None)

    def test_new_messages_from_zero_yields_all(self):
        box = self._open(FakeIMAP("h", 993))
        self.assertEqual([u for u, _ in box.new_messages(last_uid=0)], [2, 3])
        box.__exit__(None, None, None)

    def test_bad_folder_raises(self):
        fake = FakeIMAP("h", 993)
        fake.select_ok = False
        with mock.patch.object(mb.imaplib, "IMAP4_SSL", return_value=fake):
            with self.assertRaises(RuntimeError):
                mb.IntakeMailbox(mb.MailboxConfig(host="h", user="u", password="p")).__enter__()


if __name__ == "__main__":
    unittest.main()
