import unittest
from email.message import EmailMessage

from docketry.envelope import parse_message, sanitize_filename


def make_raw(subject="Service of Court Documents", attach=True, html=False):
    msg = EmailMessage()
    msg["From"] = "eService <noreply@myflcourtaccess.com>"
    msg["To"] = "intake@examplefirm.com"
    msg["Cc"] = "staff@examplefirm.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc123@portal>"
    msg["Date"] = "Sat, 22 Aug 2026 10:15:00 -0400"
    if html:
        msg.set_content("plain fallback")
        msg.add_alternative("<html><body><p>Hello</p><p>World</p></body></html>", subtype="html")
    else:
        msg.set_content("A motion was served.")
    if attach:
        msg.add_attachment(
            b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="Motion to Compel.pdf"
        )
    return bytes(msg)


class TestEnvelope(unittest.TestCase):
    def test_parse_basic(self):
        env = parse_message(make_raw(), source="test", fetched_at="2026-08-23T00:00:00+00:00")
        self.assertEqual(env.message_id, "abc123@portal")
        self.assertEqual(env.from_addr, "noreply@myflcourtaccess.com")
        self.assertEqual(env.to, ["intake@examplefirm.com"])
        self.assertEqual(env.cc, ["staff@examplefirm.com"])
        self.assertIn("2026-08-22", env.date)
        self.assertIn("motion was served", env.body_text)
        self.assertEqual(len(env.attachments), 1)
        a = env.attachments[0]
        self.assertEqual(a.filename, "Motion to Compel.pdf")
        self.assertEqual(a.content_type, "application/pdf")
        self.assertEqual(a.size, len(b"%PDF-1.4 fake"))
        self.assertEqual(len(env.raw_sha256), 64)

    def test_html_only_body_to_text(self):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "a@b.c"
        msg.add_alternative("<html><body><p>Hello</p><p>World</p><script>x()</script></body></html>", subtype="html")
        env = parse_message(bytes(msg), source="t", fetched_at="x")
        self.assertEqual(env.body_text.splitlines(), ["Hello", "World"])

    def test_missing_message_id_falls_back_to_hash(self):
        msg = EmailMessage()
        msg["From"] = "a@b.c"
        msg.set_content("x")
        env = parse_message(bytes(msg), source="t", fetched_at="x")
        self.assertTrue(env.message_id.startswith("docketry-"))

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("c:\\evil\\x.pdf"), "x.pdf")
        self.assertEqual(sanitize_filename(""), "attachment.bin")


if __name__ == "__main__":
    unittest.main()
