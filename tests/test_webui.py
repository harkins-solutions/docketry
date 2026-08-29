import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from docketry import store as st
from docketry.envelope import Attachment, Envelope
from docketry.manifest import load_manifest
from docketry.pipeline import Runner
from docketry.store import Store
from docketry.webui import make_server

MANIFEST = Path("examples/guardrails-litigation-team.toml")


def held_envelope(i=0):
    return Envelope(
        message_id=f"m{i}", from_addr=f"stranger{i}@random.net", to=[], cc=[],
        date="", subject=f"invoice {i}", body_text="pay up",
        attachments=[Attachment(filename="statement.pdf", content_type="application/pdf",
                                sha256=f"{i:064d}"[:64], size=4, content=b"%PDF")],
        raw_sha256=f"{i:064x}".rjust(64, "a")[:64], source="t", fetched_at="now",
    )


class TestWebUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pipeline = load_manifest(MANIFEST)
        store = Store(self.tmp.name)
        runner = Runner(self.pipeline, store)
        self.msg_id = store.ingest(held_envelope(), first_stage="ingest")
        runner.enter(self.msg_id)
        store.close()
        self.server = make_server(self.tmp.name, self.pipeline, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.tmp.cleanup()

    def _get(self, path="/"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read().decode()

    def _post(self, path, data):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = urlencode(data)
        conn.request("POST", path, body,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        return r.status, r.read().decode()

    def _token(self):
        _, html_text = self._get()
        return re.search(r'name="token" value="([^"]+)"', html_text).group(1)

    def test_dashboard_shows_held_message(self):
        status, body = self._get()
        self.assertEqual(status, 200)
        self.assertIn("sender outside intake scope", body)
        self.assertIn("invoice 0", body)

    def test_post_without_token_refused(self):
        status, _ = self._post("/approve", {"message": self.msg_id,
                                            "gate": "sender-scope",
                                            "by": "Dana", "role": "paralegal"})
        self.assertEqual(status, 403)
        store = Store(self.tmp.name)
        self.assertEqual(store.get_message(self.msg_id)["status"], st.PENDING_REVIEW)
        store.close()

    def test_wrong_role_refused(self):
        status, body = self._post("/approve", {
            "token": self._token(), "message": self.msg_id,
            "gate": "sender-scope", "by": "Dana", "role": "attorney"})
        self.assertEqual(status, 400)

    def test_named_approval_releases(self):
        status, _ = self._post("/approve", {
            "token": self._token(), "message": self.msg_id,
            "gate": "sender-scope", "by": "Dana", "role": "paralegal"})
        self.assertEqual(status, 303)
        store = Store(self.tmp.name)
        self.assertEqual(store.get_message(self.msg_id)["status"], st.DONE)
        roles = store.approval_roles(self.msg_id, "ingest", "sender-scope")
        self.assertIn("paralegal", roles)
        store.close()

    def test_blank_name_refused(self):
        status, _ = self._post("/approve", {
            "token": self._token(), "message": self.msg_id,
            "gate": "sender-scope", "by": "  ", "role": "paralegal"})
        self.assertEqual(status, 400)

    def test_refuses_nonlocal_bind(self):
        with self.assertRaises(ValueError):
            make_server(self.tmp.name, self.pipeline, host="0.0.0.0", port=0)

    def test_html_escapes_content(self):
        store = Store(self.tmp.name)
        env = held_envelope(1)
        env.subject = '<script>alert(1)</script>'
        mid = store.ingest(env, first_stage="ingest")
        Runner(self.pipeline, store).enter(mid)
        store.close()
        _, body = self._get()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)


if __name__ == "__main__":
    unittest.main()


SAMPLE_NOTICE = """From: noreply@stlucieclerk.com\r
To: firm@example.com\r
Subject: Notice of Hearing - Case 2026-CA-000123\r
Date: Mon, 3 Aug 2026 09:00:00 -0400\r
Message-ID: <s1@stlucieclerk.com>\r
\r
NOTICE OF HEARING

Case Number: 2026-CA-000123
Judge: Hon. A. Rivera
Hearing Date: 09/14/2026
Time: 10:30 AM
"""


class TestAdapterPanel(unittest.TestCase):
    """The whole point: add a court without writing a regular expression."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "store").mkdir()
        self.pipeline = load_manifest(MANIFEST)
        self.server = make_server(self.home / "store", self.pipeline, port=0,
                                  home=self.home)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read().decode()

    def _post(self, path, data):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, urlencode(data, doseq=True),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        return r.status, r.read().decode()

    def _token(self, body):
        return re.search(r'name="token" value="([^"]+)"', body).group(1)

    def test_the_panel_loads_and_says_there_are_none_yet(self):
        code, body = self._get("/adapters")
        self.assertEqual(code, 200)
        self.assertIn("only the built-in adapters", body)

    def test_scanning_a_pasted_email_shows_the_values_it_would_capture(self):
        _, page = self._get("/adapters")
        code, body = self._post("/adapters/scan",
                                {"token": self._token(page), "sample": SAMPLE_NOTICE})
        self.assertEqual(code, 200)
        self.assertIn("2026-CA-000123", body)      # the value, from this email
        self.assertIn("Hon. A. Rivera", body)
        self.assertIn("case_number", body)         # the suggested field name
        self.assertIn("@stlucieclerk.com", body)   # match rule pre-filled

    def _save(self, **over):
        _, page = self._get("/adapters")
        token = self._token(page)
        data = {"token": token, "sample": SAMPLE_NOTICE,
                "use": ["0", "1", "2", "3"], "req": ["2"],
                "field_0": "case_number", "field_1": "judge",
                "field_2": "hearing_date", "field_3": "hearing_time",
                "name": "st-lucie-hearing", "notice_type": "hearing_notice",
                "from": "@stlucieclerk.com",
                "subject_contains": "notice of hearing - case"}
        data.update(over)
        return self._post("/adapters/save", data)

    def test_saving_writes_a_file_the_real_loader_accepts(self):
        from docketry.notices import load_adapters_file
        code, body = self._save()
        self.assertEqual(code, 200)
        self.assertIn("Saved", body)
        self.assertIn("2026-CA-000123", body)   # shows what it actually read
        adapters = load_adapters_file(self.home / "adapters.toml")
        self.assertEqual(adapters[0].name, "st-lucie-hearing")
        self.assertIn("hearing_date", adapters[0].required)

    def test_it_appears_in_the_list_afterwards(self):
        self._save()
        _, body = self._get("/adapters")
        self.assertIn("st-lucie-hearing", body)

    def test_match_rules_that_would_miss_this_email_are_refused(self):
        code, body = self._save(**{"from": "@someothercourt.gov"})
        self.assertIn("would not recognise the email you just pasted", body)
        self.assertFalse((self.home / "adapters.toml").exists())

    def test_a_bad_adapter_never_leaves_a_broken_file_behind(self):
        self._save()                                  # one good adapter
        before = (self.home / "adapters.toml").read_text()
        self._save(**{"name": "", "sample": SAMPLE_NOTICE})   # refused
        self.assertEqual((self.home / "adapters.toml").read_text(), before)

    def test_a_stale_token_cannot_write_an_adapter(self):
        code, body = self._post("/adapters/save",
                                {"token": "wrong", "sample": SAMPLE_NOTICE,
                                 "name": "x", "notice_type": "hearing_notice"})
        self.assertEqual(code, 403)
        self.assertFalse((self.home / "adapters.toml").exists())
