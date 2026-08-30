import tempfile
import unittest
from pathlib import Path

from docketry.tools.lint import RulepackError, lint, load_rulepack

SJ_DRAFT = """MOTION FOR SUMMARY JUDGMENT
Wife conveniently omitted her own allergy testing from the verified petition.
Husband testified that the dogs were prescribed as emotional support animals. Ex. B, p. 4.
Wife admitted at deposition that she cannot prove causation.
Discovery has closed on June 19, 2026.
See Puhl v. Puhl, 260 So.3d 323 (Fla. 4th DCA 2018).
CERTIFICATE OF SERVICE
I certify a copy was served this June 15, 2026.
"""


class TestBuiltinRules(unittest.TestCase):
    def setUp(self):
        self.findings = lint(SJ_DRAFT)

    def rule(self, rid):
        return [f for f in self.findings if f.rule == rid]

    def test_credibility_language_flagged_in_sj(self):
        hits = self.rule("credibility-language")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 2)

    def test_credibility_language_not_flagged_outside_sj(self):
        findings = lint("Defendant concealed the roof damage from the adjuster.")
        self.assertEqual([f for f in findings if f.rule == "credibility-language"], [])

    def test_uncited_testimony(self):
        hits = self.rule("uncited-testimony")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 4)   # line 3 has Ex. B pin cite, line 4 has none

    def test_date_contradiction(self):
        hits = self.rule("date-contradiction")
        self.assertEqual(len(hits), 1)
        self.assertIn("has not happened yet", hits[0].message)

    def test_reporter_spacing(self):
        hits = self.rule("reporter-spacing")
        self.assertEqual(len(hits), 1)
        self.assertIn("So. 3d", hits[0].message)

    def test_no_date_contradiction_when_cert_after_close(self):
        clean = SJ_DRAFT.replace("June 15, 2026", "June 20, 2026")
        self.assertEqual([f for f in lint(clean) if f.rule == "date-contradiction"], [])

    def test_headings_skipped(self):
        # The ALL-CAPS title mentions SUMMARY JUDGMENT but is not itself flagged.
        self.assertTrue(all(f.line != 1 for f in self.findings))


class TestRulepack(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rules.toml"
            p.write_text(text)
            return load_rulepack(p)

    def test_example_pack_loads_and_fires(self):
        pack = load_rulepack("examples/lint-rules.toml")
        findings = lint("This is clearly wrong — obviously.", pack)
        rules = {f.rule for f in findings}
        self.assertIn("no-em-dash", rules)
        self.assertIn("clearly-obviously", rules)

    def test_missing_message_refused(self):
        with self.assertRaises(RulepackError):
            self._load('[[rule]]\nid="x"\npattern="y"')

    def test_bad_severity_refused(self):
        with self.assertRaises(RulepackError):
            self._load('[[rule]]\nid="x"\npattern="y"\nmessage="m"\nseverity="fatal"')

    def test_bad_regex_refused(self):
        with self.assertRaises(RulepackError):
            self._load('[[rule]]\nid="x"\npattern="(unclosed"\nmessage="m"')


if __name__ == "__main__":
    unittest.main()
