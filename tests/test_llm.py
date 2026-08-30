"""The local-only property, enforced rather than documented."""
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from docketry.llm import (
    LLMConfig,
    LLMError,
    RemoteEndpointRefused,
    probe,
    propose,
    resolve,
    vet,
)

PKG = Path(__file__).resolve().parent.parent / "docketry"


class TestLocalOnly(unittest.TestCase):
    def test_loopback_is_allowed(self):
        self.assertEqual(resolve("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        self.assertEqual(resolve("http://localhost:8080"), "http://localhost:8080")

    def test_private_lan_is_allowed(self):
        # A model on a box in the next room has still not left the building.
        self.assertEqual(resolve("http://192.168.1.50:8000"), "http://192.168.1.50:8000")
        self.assertEqual(resolve("http://10.0.0.7:8000"), "http://10.0.0.7:8000")

    def test_a_public_endpoint_is_refused(self):
        for url in ("https://api.openai.com/v1",
                    "https://api.anthropic.com",
                    "http://8.8.8.8:8000"):
            with self.assertRaises(RemoteEndpointRefused, msg=url):
                resolve(url)

    def test_the_refusal_explains_itself(self):
        with self.assertRaises(RemoteEndpointRefused) as ctx:
            resolve("https://api.openai.com/v1")
        msg = str(ctx.exception)
        self.assertIn("not on your network", msg)
        self.assertIn("Ollama", msg)          # says what to do instead

    def test_a_nonsense_scheme_is_refused(self):
        with self.assertRaises(LLMError):
            resolve("file:///etc/passwd")
        with self.assertRaises(LLMError):
            resolve("not a url")

    def test_refusal_happens_before_any_request(self):
        # If the check ran after the body was built, a misconfigured install
        # would have already put a document on the wire.
        cfg = LLMConfig(base_url="https://api.openai.com/v1", model="gpt-4")
        with self.assertRaises(RemoteEndpointRefused):
            propose(cfg, "a client's privileged document")


# Set by a test to make the stub answer the way a given family of model does.
REPLY = {"content": None, "reasoning_content": None}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        message = {"content": REPLY["content"] if REPLY["content"] is not None
                   else "ready: " + body["messages"][-1]["content"][:12]}
        if REPLY["reasoning_content"] is not None:
            message["reasoning_content"] = REPLY["reasoning_content"]
        out = json.dumps({"model": "test-model",
                          "choices": [{"message": message}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestProposeAgainstALocalServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_a_proposal_carries_its_provenance(self):
        p = propose(LLMConfig(base_url=self.url, model="test-model"), "classify this")
        self.assertTrue(p.text.startswith("ready:"))
        self.assertEqual(p.model, "test-model")
        self.assertEqual(len(p.prompt_sha256), 64)
        self.assertIn("suggestion, not a finding", p.provenance)

    def test_probe_reports_ready(self):
        self.assertIn("ready", probe(LLMConfig(base_url=self.url, model="m")))

    def test_probe_never_raises_on_a_dead_endpoint(self):
        out = probe(LLMConfig(base_url="http://127.0.0.1:1", model="m", timeout=2))
        self.assertIn("unreachable", out)

    def test_probe_does_not_inherit_a_long_generation_timeout(self):
        # doctor is what you run when things are broken; it must not hang.
        import docketry.llm as llm
        seen = {}
        real = llm.propose

        def spy(cfg, prompt, **kw):
            seen["timeout"] = cfg.timeout
            return real(cfg, prompt, **kw)

        llm.propose = spy
        try:
            probe(LLMConfig(base_url=self.url, model="m", timeout=900))
        finally:
            llm.propose = real
        self.assertEqual(seen["timeout"], llm.PROBE_TIMEOUT)

    def test_probe_reports_a_public_endpoint_as_refused(self):
        self.assertIn("REFUSED", probe(LLMConfig(base_url="https://api.openai.com",
                                                 model="gpt-4")))


class TestWhereThePacketsActuallyGo(unittest.TestCase):
    """The check has to be about the address, and about THAT address."""

    def setUp(self):
        REPLY["content"] = None
        REPLY["reasoning_content"] = None

    def test_a_dot_local_name_is_not_trusted_on_its_face(self):
        # `models.local` is just a name. Any resolver is free to answer it
        # with a public address, and a check that short-circuits on the
        # suffix never looks.
        def public(host, port=None, *a, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     ("8.8.8.8", 0))]

        with mock.patch("docketry.llm.socket.getaddrinfo", public):
            with self.assertRaises(RemoteEndpointRefused):
                resolve("http://models.local:11434")
            with self.assertRaises(RemoteEndpointRefused):
                resolve("http://localhost:11434")

    def test_the_vetted_address_is_the_one_dialled(self):
        # The host here never resolves. If propose() looked the name up again
        # instead of using the address vet() approved, this could not connect
        # at all — which is the point: one lookup, checked, then used.
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_port
            loopback = [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                         ("127.0.0.1", port))]
            with mock.patch("docketry.llm.socket.getaddrinfo",
                            lambda *a, **kw: loopback):
                ep = vet(f"http://model.invalid:{port}")
                self.assertEqual(ep.ip, "127.0.0.1")
            # Resolution is no longer patched; a second lookup would fail.
            with self.assertRaises(socket.gaierror):
                socket.getaddrinfo("model.invalid", port)
            with mock.patch("docketry.llm.vet", return_value=ep):
                p = propose(LLMConfig(base_url=ep.url, model="m"), "classify")
            self.assertTrue(p.text.startswith("ready:"))
        finally:
            srv.shutdown()
            srv.server_close()


class _Redirector(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.send_response(302)
        self.send_header("Location", "https://api.openai.com/v1/chat/completions")
        self.send_header("Content-Length", "0")
        self.end_headers()


class TestRedirectsAreNotFollowed(unittest.TestCase):
    """A private endpoint that answers 3xx is a way off the network."""

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Redirector)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_a_redirect_off_the_lan_is_refused_not_followed(self):
        with self.assertRaises(RemoteEndpointRefused) as ctx:
            propose(LLMConfig(base_url=self.url, model="m"), "privileged text")
        msg = str(ctx.exception)
        self.assertIn("redirect", msg)
        self.assertIn("api.openai.com", msg)

    def test_probe_reports_it_rather_than_calling_it_ready(self):
        self.assertIn("REFUSED", probe(LLMConfig(base_url=self.url, model="m")))


class TestNoModelInsideAGate(unittest.TestCase):
    """A model may propose. The enforcement path stays deterministic.

    Mirrors the no-send property: asserted by grep so it cannot rot quietly.
    """

    def test_gates_do_not_import_the_llm(self):
        offenders = []
        for f in sorted((PKG / "gates").glob("*.py")):
            if "llm" in f.read_text():
                offenders.append(f.name)
        self.assertEqual(offenders, [],
                         "a gate must not consult a model: gates decide, models propose")

    def test_the_pipeline_runner_does_not_import_the_llm(self):
        self.assertNotIn("llm", (PKG / "pipeline.py").read_text())

    def test_redaction_does_not_consult_a_model(self):
        # What gets redacted is never a model's call.
        self.assertNotIn("llm", (PKG / "redact.py").read_text())


class TestReasoningModels(unittest.TestCase):
    """DeepSeek-R1, Qwen3 and friends narrate before answering."""

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        REPLY["content"] = None
        REPLY["reasoning_content"] = None

    def _ask(self):
        return propose(LLMConfig(base_url=self.url, model="m"), "classify this")

    def test_inline_think_blocks_do_not_become_the_answer(self):
        REPLY["content"] = ("<think>Could be a motion. Or an order. Let me "
                            "reconsider.</think>\nOrder Granting Motion to Compel")
        p = self._ask()
        self.assertEqual(p.text, "Order Granting Motion to Compel")
        self.assertIn("reconsider", p.reasoning)
        self.assertNotIn("<think>", p.text)

    def test_a_separate_reasoning_field_is_kept_apart(self):
        REPLY["content"] = "Notice of Hearing"
        REPLY["reasoning_content"] = "The caption says NOTICE OF HEARING."
        p = self._ask()
        self.assertEqual(p.text, "Notice of Hearing")
        self.assertIn("caption", p.reasoning)

    def test_reasoning_only_is_an_error_not_an_empty_answer(self):
        # Truncated mid-thought. Returning "" would look like a confident
        # empty classification.
        REPLY["content"] = "<think>Hmm, the caption is ambiguous</think>"
        with self.assertRaises(LLMError) as ctx:
            self._ask()
        self.assertIn("only reasoning", str(ctx.exception))

    def test_a_plain_model_is_unaffected(self):
        REPLY["content"] = "Motion to Dismiss"
        p = self._ask()
        self.assertEqual(p.text, "Motion to Dismiss")
        self.assertEqual(p.reasoning, "")


if __name__ == "__main__":
    unittest.main()
