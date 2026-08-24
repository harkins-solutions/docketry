import contextlib
import io
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from portico import cli
from portico import mailbox as mb

try:
    import eyecite  # noqa: F401
    HAVE_EYECITE = True
except ImportError:
    HAVE_EYECITE = False


def run_cli(*argv):
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            cli.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue()


def eportal_raw():
    m = EmailMessage()
    m["From"] = "eservice@myflcourtaccess.com"
    m["To"] = "intake@examplefirm.com"
    m["Subject"] = "SERVICE OF COURT DOCUMENT"
    m.set_content("Case Number: 562026CA001234\nDocument: Motion to Compel\n")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf",
                     filename="Motion to Compel.pdf")
    return bytes(m)


class FakeIMAP:
    def __init__(self, host, port):
        self.messages = {1: eportal_raw()}

    def login(self, u, p): pass
    def select(self, folder, readonly=False):
        assert readonly, "poll must open the mailbox read-only"
        return "OK", [b"1"]
    def response(self, key):
        return "OK", [b"5"] if key == "UIDVALIDITY" else [None]
    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b"1"]
        uid = int(args[0])
        return "OK", [(b"1 (RFC822", self.messages[uid]), b")"]
    def logout(self): pass


class TestCliFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = str(Path(self.tmp.name) / "home")
        code, out = run_cli("--home", self.home, "init",
                            "--host", "imap.x.com", "--user", "intake@f.com")
        self.assertEqual(code, 0)
        self.assertIn("initialized", out)

    def tearDown(self):
        self.tmp.cleanup()

    def _poll(self):
        with mock.patch.object(mb.imaplib, "IMAP4_SSL", FakeIMAP):
            with mock.patch.dict("os.environ", {"PORTICO_IMAP_PASSWORD": "pw"}):
                return run_cli("--home", self.home, "poll")

    def test_poll_ingests_and_parses(self):
        code, out = self._poll()
        self.assertEqual(code, 0)
        self.assertIn("ingested 1 new message(s)", out)
        self.assertIn("1 parsed as court notices", out)
        # cursor advanced: a second sweep ingests nothing
        code, out = self._poll()
        self.assertIn("ingested 0", out)
        code, out = run_cli("--home", self.home, "notices")
        self.assertIn("service_notice via fl-eportal-service", out)
        code, out = run_cli("--home", self.home, "notices", "--type", "hearing_notice")
        self.assertIn("no notices", out)
        code, out = run_cli("--home", self.home, "status")
        self.assertIn("done", out)
        code, out = run_cli("--home", self.home, "class-queue")
        self.assertIn("motion_compel", out)
        code, out = run_cli("--home", self.home, "class-apply", "1",
                            "--by", "Dana", "--role", "paralegal")
        self.assertIn("applied", out)

    def test_poll_without_password_is_loud(self):
        code, out = run_cli("--home", self.home, "poll")
        self.assertNotEqual(code, 0)

    def test_queue_empty(self):
        code, out = run_cli("--home", self.home, "queue")
        self.assertIn("queue empty", out)

    def test_missing_home_is_loud(self):
        code, _ = run_cli("--home", str(Path(self.tmp.name) / "nope"), "status")
        self.assertNotEqual(code, 0)


class TestCliTools(unittest.TestCase):
    def test_classify_command(self):
        code, out = run_cli("classify", "Notice of Hearing.pdf")
        self.assertEqual(code, 0)
        self.assertIn("notice_of_hearing (high)", out)

    def test_lint_command_exits_1_on_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.txt"
            p.write_text("MOTION FOR SUMMARY JUDGMENT\nShe testified the roof leaked.\n")
            code, out = run_cli("lint", str(p))
            self.assertEqual(code, 1)
            self.assertIn("uncited-testimony", out)

    def test_lint_clean_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.txt"
            p.write_text("A perfectly ordinary letter.\n")
            code, out = run_cli("lint", str(p))
            self.assertEqual(code, 0)

    @unittest.skipUnless(HAVE_EYECITE, "eyecite not installed")
    def test_verify_draft_offline_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.txt"
            p.write_text("See Puhl v. Puhl, 260 So. 3d 323 (Fla. 4th DCA 2018).")
            code, out = run_cli("verify-draft", str(p), "--offline")
            self.assertEqual(code, 2)
            self.assertIn("NOT verified", out)


if __name__ == "__main__":
    unittest.main()


class TestCliQol(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = str(Path(self.tmp.name) / "home")
        run_cli("--home", self.home, "init", "--host", "imap.x.com", "--user", "intake@f.com")

    def tearDown(self):
        self.tmp.cleanup()

    def _ingest_held(self):
        import json as _json
        from portico.config import load_home
        from portico.manifest import load_manifest
        from portico.envelope import parse_message
        from portico.pipeline import Runner
        from portico.store import Store, utcnow
        m = EmailMessage()
        m["From"] = "stranger@x.net"; m["To"] = "intake@f.com"; m["Subject"] = "inv"
        m.set_content("pay")
        cfg = load_home(self.home)
        # default manifest bounces strangers only if sender-scope configured;
        # use the litigation example for a guaranteed hold
        cfg.manifest_path.write_text(
            Path("examples/guardrails-litigation-team.toml").read_text())
        pipeline = load_manifest(cfg.manifest_path)
        store = Store(cfg.store_path)
        mid = store.ingest(parse_message(bytes(m), source="t", fetched_at=utcnow()),
                           first_stage="ingest")
        Runner(pipeline, store).enter(mid)
        store.close()
        return mid

    def test_stats_text_and_json(self):
        self._ingest_held()
        code, out = run_cli("--home", self.home, "stats")
        self.assertEqual(code, 0)
        self.assertIn("holds by gate", out)
        self.assertIn("sender-scope", out)
        code, out = run_cli("--home", self.home, "stats", "--json")
        import json as _json
        data = _json.loads(out)
        self.assertEqual(data["ingested"], 1)
        self.assertEqual(data["holds_by_gate"].get("sender-scope"), 1)

    def test_digest_lists_held(self):
        self._ingest_held()
        code, out = run_cli("--home", self.home, "digest")
        self.assertEqual(code, 0)
        self.assertIn("1 awaiting review", out)
        self.assertIn("sender-scope", out)

    def test_doctor_passes_on_fresh_home(self):
        code, out = run_cli("--home", self.home, "doctor")
        self.assertEqual(code, 0)
        self.assertIn("manifest: stages", out)
        self.assertIn("PORTICO_IMAP_PASSWORD", out)

    def test_doctor_fails_on_missing_home(self):
        code, out = run_cli("--home", str(Path(self.tmp.name) / "ghost"), "doctor")
        self.assertEqual(code, 1)

    def test_doctor_fails_on_broken_manifest(self):
        Path(self.home, "guardrails.toml").write_text(
            '[pipeline]\nstages=["ingest"]\n[[gate]]\nid="no-such"\nbinds_to=["ingest"]\n')
        code, out = run_cli("--home", self.home, "doctor")
        self.assertEqual(code, 1)
        self.assertIn("manifest refused", out)

    def test_approve_validates_gate_and_role(self):
        mid = self._ingest_held()
        code, out = run_cli("--home", self.home, "approve", str(mid),
                            "--gate", "no-such-gate", "--by", "Dana", "--role", "paralegal")
        self.assertNotEqual(code, 0)
        code, out = run_cli("--home", self.home, "approve", str(mid),
                            "--gate", "sender-scope", "--by", "Dana", "--role", "attorney")
        self.assertNotEqual(code, 0)
        code, out = run_cli("--home", self.home, "approve", str(mid),
                            "--gate", "sender-scope", "--by", "Dana", "--role", "paralegal")
        self.assertEqual(code, 0)
        self.assertIn("done", out)


class TestDemo(unittest.TestCase):
    def test_demo_seeds_and_serves(self):
        import threading
        import http.client
        from unittest import mock
        import portico.cli as cli_mod

        started = {}
        real_serve = None

        class Args:
            port = 0
            no_browser = True

        # capture the server instead of blocking forever
        from portico import webui as webui_mod
        real_make = webui_mod.make_server

        def capture_make(store_path, pipeline, host="127.0.0.1", port=0):
            server = real_make(store_path, pipeline, host=host, port=port)
            started["server"] = server
            raise KeyboardInterrupt  # unwind out of serve_forever path

        with mock.patch.object(cli_mod, "webbrowser") if hasattr(cli_mod, "webbrowser") else mock.patch("webbrowser.open"):
            with mock.patch("portico.cli.make_server", capture_make, create=True):
                with mock.patch("portico.webui.make_server", capture_make):
                    try:
                        run_cli("demo", "--no-browser")
                    except KeyboardInterrupt:
                        pass
        server = started.get("server")
        self.assertIsNotNone(server, "demo never built its server")
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        body = conn.getresponse().read().decode()
        server.shutdown()
        self.assertIn("Held for review", body)
        self.assertIn("sketchy.example", body)          # stranger held
        self.assertIn("did not extract", body)          # drifted template held
        self.assertIn("Hearing Scheduled", body.replace("&#x27;", "'")) if "Hearing" in body else None
        self.assertIn("service_notice", body)
