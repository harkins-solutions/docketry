import unittest

from portico.envelope import Attachment, Envelope
from portico.gates.builtin import AttachmentPolicy, SenderScope


def env(attachments=None, from_addr="noreply@myflcourtaccess.com"):
    return Envelope(
        message_id="m", from_addr=from_addr, to=[], cc=[], date="", subject="s",
        body_text="", attachments=attachments or [], raw_sha256="c" * 64,
        source="t", fetched_at="now",
    )


def attach(name, size=10):
    return Attachment(filename=name, content_type="application/octet-stream",
                      sha256="d" * 64, size=size, content=b"")


class TestBuiltinGates(unittest.TestCase):
    def test_attachment_policy_denies_executables(self):
        f = AttachmentPolicy().check(env([attach("run.exe")]), {})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "fail")

    def test_attachment_policy_accepts_pdf(self):
        self.assertEqual(AttachmentPolicy().check(env([attach("motion.pdf")]), {}), [])

    def test_attachment_policy_size_cap(self):
        f = AttachmentPolicy().check(env([attach("big.pdf", size=30 * 1024 * 1024)]),
                                     {"max_size_mb": 25})
        self.assertEqual(len(f), 1)

    def test_sender_scope_domain_match(self):
        g = SenderScope()
        opts = {"allow": ["@myflcourtaccess.com", "staff@firm.com"]}
        self.assertEqual(g.check(env(), opts), [])
        self.assertEqual(g.check(env(from_addr="staff@firm.com"), opts), [])
        f = g.check(env(from_addr="stranger@example.com"), opts)
        self.assertEqual(len(f), 1)

    def test_sender_scope_empty_allow_is_noop(self):
        self.assertEqual(SenderScope().check(env(from_addr="anyone@x.com"), {}), [])


if __name__ == "__main__":
    unittest.main()
