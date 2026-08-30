"""Deriving an adapter from a real email, and proving it before it is saved."""
import tempfile
import unittest
from pathlib import Path

from docketry.tools.adapter_builder import (
    suggest_type,
    Candidate,
    build,
    scan,
    scan_email,
    suggest_match,
    to_toml,
)
from docketry.tools.notices import AdapterError, load_adapters_file, parse

SAMPLE = b"""From: noreply@stlucieclerk.com\r
To: firm@example.com\r
Subject: Notice of Hearing - Case 2026-CA-000123\r
Date: Mon, 3 Aug 2026 09:00:00 -0400\r
Message-ID: <abc@stlucieclerk.com>\r
\r
NOTICE OF HEARING

Case Number: 2026-CA-000123
Case Style: SMITH v. JONES
Judge: Hon. A. Rivera
Hearing Date: 09/14/2026
Time: 10:30 AM
Location: Courtroom 4B

Please do not reply to this message: it is unmonitored.
"""


class TestScan(unittest.TestCase):
    def test_labelled_lines_become_candidates(self):
        cands = {c.field: c.value for c in scan(SAMPLE.decode())}
        self.assertEqual(cands["case_number"], "2026-CA-000123")
        self.assertEqual(cands["judge"], "Hon. A. Rivera")
        self.assertEqual(cands["hearing_date"], "09/14/2026")

    def test_known_labels_get_the_conventional_field_name(self):
        by_label = {c.label: c for c in scan(SAMPLE.decode())}
        self.assertEqual(by_label["Case Number"].field, "case_number")
        self.assertEqual(by_label["Time"].field, "hearing_time")
        self.assertTrue(by_label["Judge"].known)

    def test_an_unknown_label_still_gets_a_usable_name(self):
        c = scan("Panel Assignment: 4B")[0]
        self.assertEqual(c.field, "panel_assignment")
        self.assertFalse(c.known)

    def test_prose_with_a_colon_is_not_a_field(self):
        # "Please do not reply to this message: it is unmonitored."
        labels = {c.label.lower() for c in scan(SAMPLE.decode())}
        self.assertNotIn("please do not reply to this message", labels)

    def test_email_plumbing_is_not_a_field(self):
        labels = {c.label.lower() for c in scan(SAMPLE.decode())}
        for junk in ("from", "to", "subject", "date"):
            self.assertNotIn(junk, labels)


class TestSuggestMatch(unittest.TestCase):
    def test_match_is_the_domain_not_the_sender(self):
        env, _ = scan_email(SAMPLE)
        m = suggest_match(env)
        # Pinning to noreply@ would match one email ever.
        self.assertEqual(m["from"], "@stlucieclerk.com")
        self.assertIn("notice", m["subject_contains"])


class TestBuildAndProve(unittest.TestCase):
    def _fields(self):
        return {c.field: c.pattern for c in scan(SAMPLE.decode())}

    def test_the_derived_adapter_actually_parses_its_own_sample(self):
        env, _ = scan_email(SAMPLE)
        a = build("st-lucie-hearing", "hearing_notice", suggest_match(env),
                  self._fields(), ["hearing_date"])
        self.assertTrue(a.match(env))
        result = a.extract(env)
        self.assertEqual(result.fields["case_number"], "2026-CA-000123")
        self.assertEqual(result.fields["hearing_date"], "09/14/2026")
        self.assertEqual(result.missing, [])

    def test_a_required_field_nobody_extracts_is_refused(self):
        # Otherwise every message bounces for a value the adapter never seeks.
        with self.assertRaises(AdapterError) as ctx:
            build("x", "hearing_notice", {"from": "@x.gov"}, self._fields(),
                  ["bond_amount"])
        self.assertIn("never looks for", str(ctx.exception))

    def test_a_bad_notice_type_is_refused(self):
        with self.assertRaises(AdapterError):
            build("x", "hearing", {"from": "@x.gov"}, self._fields(), [])

    def test_an_adapter_with_no_fields_is_refused(self):
        with self.assertRaises(AdapterError):
            build("x", "hearing_notice", {"from": "@x.gov"}, {}, [])

    def test_an_adapter_with_no_match_rules_is_refused(self):
        # It would match every message that ever arrives.
        with self.assertRaises(AdapterError):
            build("x", "hearing_notice", {}, self._fields(), [])

    def test_the_toml_round_trips_through_the_real_loader(self):
        env, _ = scan_email(SAMPLE)
        toml = to_toml("st-lucie-hearing", "hearing_notice", suggest_match(env),
                       self._fields(), ["hearing_date"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "adapters.toml"
            p.write_text(toml)
            loaded = load_adapters_file(p)     # the real validator
        self.assertEqual(len(loaded), 1)
        got = parse(env, loaded)
        self.assertEqual(got.adapter, "st-lucie-hearing")
        self.assertEqual(got.fields["judge"], "Hon. A. Rivera")

    def test_a_second_adapter_appends_without_breaking_the_first(self):
        env, _ = scan_email(SAMPLE)
        toml = (to_toml("a", "hearing_notice", suggest_match(env),
                        self._fields(), ["hearing_date"])
                + to_toml("b", "service_notice", {"from": "@example.gov"},
                          {"case_number": self._fields()["case_number"]}, []))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "adapters.toml"
            p.write_text(toml)
            self.assertEqual(len(load_adapters_file(p)), 2)


if __name__ == "__main__":
    unittest.main()


class TestSuggestType(unittest.TestCase):
    def _env(self, subject, body):
        raw = (f"From: a@b.gov\r\nTo: c@d.com\r\nSubject: {subject}\r\n"
               f"Message-ID: <x@b.gov>\r\n\r\n{body}").encode()
        from docketry.core.envelope import parse_message
        return parse_message(raw, source="t", fetched_at="")

    def test_a_hearing_notice_is_not_filed_as_service(self):
        env = self._env("Notice of Hearing - Case 2026-CA-1",
                        "Hearing Date: 09/14/2026")
        self.assertEqual(suggest_type(env), "hearing_notice")

    def test_a_filing_receipt_is_recognised(self):
        env = self._env("Filing Accepted", "Envelope Number: 118402")
        self.assertEqual(suggest_type(env), "filing_receipt")

    def test_anything_else_defaults_to_the_broadest_type(self):
        env = self._env("Service of Court Document", "Documents: Answer")
        self.assertEqual(suggest_type(env), "service_notice")
