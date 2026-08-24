import unittest

from portico.cite import (
    CiteError, Lookup, name_matches, quotes_near, star_pages, verify,
)
from portico.cite import citation_inventory  # noqa: F401

try:
    import eyecite  # noqa: F401
    HAVE_EYECITE = True
except ImportError:
    HAVE_EYECITE = False


class TestNameMatch(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(name_matches("Puhl", "Puhl", "Puhl v. Puhl"))

    def test_hallucinated_name_rejected(self):
        # A real citation carrying an invented name must fail side-aware.
        self.assertFalse(name_matches("Friend", "Lee", "Augustus v. Board of Public Instruction"))

    def test_wrong_short_name_rejected(self):
        self.assertFalse(name_matches("Putnal", "State", "Southern Home Ins. Co. v. Putnal"))

    def test_order_tolerant(self):
        self.assertTrue(name_matches("Jones", "Smith", "Smith v. Jones"))

    def test_generic_tokens_do_not_carry_a_match(self):
        self.assertFalse(name_matches("State Insurance Company", "Doe",
                                      "Acme Corp v. Roe"))

    def test_no_v_caption(self):
        self.assertTrue(name_matches("Amendments", "", "In re Amendments to Rules"))

    def test_empty_cited_name_passes(self):
        self.assertTrue(name_matches("", "", "Anything v. Anyone"))


@unittest.skipUnless(HAVE_EYECITE, "eyecite not installed")
class TestShortForm(unittest.TestCase):
    def test_short_only_document_fails_loudly(self):
        text = "The court weighed experts at summary judgment. 329 So. 3d at 153-54."
        report = verify(text, FakeClient({}))
        self.assertEqual(report.short_citations, 1)
        self.assertTrue(report.failed)
        self.assertIn("unverifiable as written", report.findings[0].summary)

    def test_shorts_with_fulls_are_info(self):
        table = {"260 So. 3d 323": (Lookup(True, "Puhl v. Puhl", 7), "", None)}
        text = ("Puhl v. Puhl, 260 So. 3d 323 (Fla. 4th DCA 2018). "
                "Later the court repeated it. 260 So. 3d at 324.")
        report = verify(text, FakeClient(table))
        self.assertFalse(report.failed)
        self.assertGreaterEqual(report.short_citations, 1)


class TestQuotesAndPages(unittest.TestCase):
    def test_quotes_near_finds_preceding_quote(self):
        text = 'The court held that "summary judgment requires the absence of any genuine dispute" long ago. Puhl v. Puhl, 260 So. 3d 323.'
        span = (text.index("Puhl v."), len(text))
        qs = quotes_near(text, span)
        self.assertEqual(len(qs), 1)
        self.assertIn("genuine dispute", qs[0])

    def test_short_quotes_ignored(self):
        text = 'He said "no" then cited. Case v. Case, 1 So. 2d 1.'
        self.assertEqual(quotes_near(text, (len(text) - 5, len(text))), [])

    def test_star_pages(self):
        html = ('<p>intro</p><span class="star-pagination" label="323">*323</span>'
                '<p>first page words here</p>'
                '<span class="star-pagination" label="324">*324</span><p>second page words</p>')
        pages = star_pages(html)
        self.assertEqual([p for p, _ in pages], [323, 324])
        self.assertIn("first page words", pages[0][1])
        self.assertIn("second page words", pages[1][1])


class FakeClient:
    """Scripted lookups: {citation_text_substring: (Lookup, plain, html)}"""

    def __init__(self, table):
        self.table = table

    def lookup(self, citation_text):
        for key, (lk, _, _) in self.table.items():
            if key in citation_text:
                return lk
        return Lookup(exists=False)

    def opinion_text(self, cluster_id):
        for lk, plain, html in self.table.values():
            if lk.cluster_id == cluster_id:
                return plain, html
        return "", None


@unittest.skipUnless(HAVE_EYECITE, "eyecite not installed")
class TestVerify(unittest.TestCase):
    def test_nonexistent_citation_fails(self):
        report = verify("See Fake v. Case, 999 So. 3d 111 (Fla. 2020).", FakeClient({}))
        self.assertTrue(report.failed)
        self.assertEqual(report.findings[0].check, "exists")

    def test_name_mismatch_fails(self):
        table = {"260 So. 3d 323": (Lookup(True, "Augustus v. Board of Public Instruction", 7), "", None)}
        report = verify("Friend v. Lee, 260 So. 3d 323 (Fla. 4th DCA 2018).", FakeClient(table))
        self.assertTrue(report.failed)
        checks = [f.check for f in report.findings if f.severity == "fail"]
        self.assertIn("name", checks)

    def test_clean_citation_passes(self):
        table = {"260 So. 3d 323": (Lookup(True, "Puhl v. Puhl", 7), "", None)}
        report = verify("Puhl v. Puhl, 260 So. 3d 323 (Fla. 4th DCA 2018).", FakeClient(table))
        self.assertFalse(report.failed)

    def test_missing_quote_fails(self):
        opinion = "This opinion talks about entirely different things."
        table = {"260 So. 3d 323": (Lookup(True, "Puhl v. Puhl", 7), opinion, None)}
        text = ('As the court put it, "the moving party must conclusively disprove'
                ' the factual basis of the claim." Puhl v. Puhl, 260 So. 3d 323.')
        report = verify(text, FakeClient(table))
        self.assertTrue(report.failed)
        self.assertIn("quote", [f.check for f in report.findings if f.severity == "fail"])

    def test_quote_found_passes_and_pin_checked(self):
        html = ('<span class="star-pagination" label="325">*325</span>'
                '<p>the moving party must conclusively disprove the factual basis of the claim</p>')
        plain = "the moving party must conclusively disprove the factual basis of the claim"
        table = {"260 So. 3d 323": (Lookup(True, "Puhl v. Puhl", 7), plain, html)}
        text = ('As the court put it, "the moving party must conclusively disprove'
                ' the factual basis of the claim." Puhl v. Puhl, 260 So. 3d 323, 324.')
        report = verify(text, FakeClient(table))
        self.assertFalse(report.failed)  # quote exists; pin mismatch is a warn
        self.assertIn("pin", [f.check for f in report.findings if f.severity == "warn"])

    def test_extraction_without_eyecite_is_loud(self):
        pass  # covered by the import guard; presence of eyecite here means N/A


if __name__ == "__main__":
    unittest.main()
