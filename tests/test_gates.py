import unittest

from docketry.envelope import Attachment, Envelope
from docketry.gates.builtin import AttachmentPolicy, SenderScope


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

    def test_sender_scope_admits_subdomains(self):
        g = SenderScope()
        opts = {"allow": ["@uscourts.gov"]}
        self.assertEqual(g.check(env(from_addr="ecf_bounces@flsd.uscourts.gov"), opts), [])
        self.assertEqual(g.check(env(from_addr="x@uscourts.gov"), opts), [])
        # lookalike domains must NOT ride the suffix
        self.assertEqual(len(g.check(env(from_addr="x@evil-uscourts.gov"), opts)), 1)
        self.assertEqual(len(g.check(env(from_addr="x@uscourts.gov.evil.com"), opts)), 1)

    def test_sender_scope_deny_beats_allow(self):
        g = SenderScope()
        opts = {"allow": ["@myflcourtaccess.com"], "deny": ["spoof@myflcourtaccess.com"]}
        self.assertEqual(len(g.check(env(from_addr="spoof@myflcourtaccess.com"), opts)), 1)
        self.assertEqual(g.check(env(from_addr="real@myflcourtaccess.com"), opts), [])

    def test_sender_scope_deny_without_allow(self):
        g = SenderScope()
        opts = {"deny": ["@badcorp.example"]}
        self.assertEqual(len(g.check(env(from_addr="x@badcorp.example"), opts)), 1)
        self.assertEqual(g.check(env(from_addr="x@fine.example"), opts), [])

    def test_sender_scope_empty_allow_is_noop(self):
        self.assertEqual(SenderScope().check(env(from_addr="anyone@x.com"), {}), [])


if __name__ == "__main__":
    unittest.main()


class TestNameScreen(unittest.TestCase):
    def test_screened_name_holds_across_fields(self):
        from docketry.gates.builtin import NameScreen
        g = NameScreen()
        opts = {"terms": ["Walled Party LLC"], "note": "ethical wall"}
        hit = g.check(env(attachments=[attach("Walled Party LLC agreement.pdf")]), opts)
        self.assertEqual(len(hit), 1)
        self.assertIn("ethical wall", hit[0].summary)
        e = env()
        e.body_text = "re the walled party llc claim"
        self.assertEqual(len(g.check(e, opts)), 1)

    def test_word_boundaries_prevent_substring_hits(self):
        from docketry.gates.builtin import NameScreen
        g = NameScreen()
        e = env()
        e.body_text = "the market analysis"
        self.assertEqual(g.check(e, {"terms": ["Mark"]}), [])

    def test_options_validated(self):
        from docketry.gates.builtin import NameScreen
        self.assertTrue(NameScreen().validate_options({}))
        self.assertTrue(NameScreen().validate_options({"terms": []}))
        self.assertEqual(NameScreen().validate_options({"terms": ["X"]}), [])
