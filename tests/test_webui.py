import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from portico import store as st
from portico.envelope import Attachment, Envelope
from portico.manifest import load_manifest
from portico.pipeline import Runner
from portico.store import Store
from portico.webui import make_server

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
