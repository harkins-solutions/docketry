import unittest

try:
    import httpx
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False

if HAVE_HTTPX:
    from docketry.cite import CiteError
    from docketry.cite_client import CourtListenerClient

    def client_with(handler):
        c = CourtListenerClient(token="tkn")
        c._http = httpx.Client(
            base_url="https://cl.test/api/rest/v4",
            transport=httpx.MockTransport(handler),
        )
        return c


@unittest.skipUnless(HAVE_HTTPX, "httpx not installed")
class TestCourtListenerClient(unittest.TestCase):
    def test_lookup_found(self):
        def handler(request):
            assert request.url.path.endswith("/citation-lookup/")
            return httpx.Response(200, json=[{
                "status": 200,
                "clusters": [{"id": 42, "case_name": "Puhl v. Puhl"}],
            }])
        lk = client_with(handler).lookup("260 So. 3d 323")
        self.assertTrue(lk.exists)
        self.assertEqual(lk.case_name, "Puhl v. Puhl")
        self.assertEqual(lk.cluster_id, 42)

    def test_lookup_not_found(self):
        def handler(request):
            return httpx.Response(200, json=[{"status": 404, "clusters": []}])
        self.assertFalse(client_with(handler).lookup("999 So. 3d 111").exists)

    def test_http_error_is_loud(self):
        def handler(request):
            return httpx.Response(401)
        with self.assertRaises(CiteError):
            client_with(handler).lookup("1 U.S. 1")

    def test_opinion_text_prefers_plain(self):
        def handler(request):
            return httpx.Response(200, json={"results": [{
                "plain_text": "opinion words",
                "xml_harvard": "<p>html words</p>",
            }]})
        plain, html = client_with(handler).opinion_text(42)
        self.assertEqual(plain, "opinion words")
        self.assertIn("html words", html)

    def test_opinion_text_strips_html_fallback(self):
        def handler(request):
            return httpx.Response(200, json={"results": [{
                "plain_text": "",
                "xml_harvard": "<p>only html</p>",
            }]})
        plain, _ = client_with(handler).opinion_text(42)
        self.assertIn("only html", plain)

    def test_no_results(self):
        def handler(request):
            return httpx.Response(200, json={"results": []})
        self.assertEqual(client_with(handler).opinion_text(7), ("", None))


if __name__ == "__main__":
    unittest.main()
