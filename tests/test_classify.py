import tempfile
import unittest

from docketry.classify import classify
from docketry.envelope import Attachment, Envelope
from docketry.store import Store


class TestClassify(unittest.TestCase):
    def test_title_anchors(self):
        cases = [
            ("Defendants Motion for Summary Judgment", "motion_msj"),
            ("Amended Complaint", "amended_complaint"),
            ("Plaintiffs Motion to Compel Discovery", "motion_compel"),
            ("Motion to Dismiss Count II", "motion_dismiss"),
            ("Order Granting Motion to Compel", "order"),
            ("Notice of Hearing on MSJ", "notice_of_hearing"),
            ("Response to First Request for Production", "discovery_response"),
            ("First Set of Interrogatories to Defendant", "discovery_request"),
            ("Subpoena Duces Tecum", "subpoena"),
            ("Notice of Taking Deposition of Adjuster", "deposition"),
            ("Answer to Amended Complaint", "amended_complaint"),  # amended wins order
            ("Complaint for Damages", "complaint"),
            ("Notice of Unavailability", "notice"),
        ]
        for title, expected in cases:
            label, tier = classify(title)
            self.assertEqual(label, expected, f"{title!r} -> {label}")
            self.assertEqual(tier, "high")

    def test_underscored_filenames(self):
        label, tier = classify("2026_08_01_Motion_for_Summary_Judgment_final")
        self.assertEqual((label, tier), ("motion_msj", "high"))

    def test_body_anchor_medium(self):
        label, tier = classify("scan001", "IT IS HEREBY ORDERED AND ADJUDGED that the motion is DENIED.")
        self.assertEqual((label, tier), ("order", "medium"))

    def test_fallback_low(self):
        self.assertEqual(classify("FW letter re claim"), ("correspondence", "low"))


def env_with_attachment(name):
    a = Attachment(filename=name, content_type="application/pdf",
                   sha256="f" * 64, size=4, content=b"%PDF")
    return Envelope(message_id="m", from_addr="a@b.c", to=[], cc=[], date="",
                    subject="s", body_text="", attachments=[a],
                    raw_sha256="9" * 64, source="t", fetched_at="now")


class TestStagedApply(unittest.TestCase):
    def test_stage_dedupe_and_fill_only_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            msg_id = store.ingest(env_with_attachment("Motion for Summary Judgment.pdf"),
                                  first_stage="ingest")
            att = store.attachments_for(msg_id)[0]
            cid = store.stage_classification(att["id"], "motion_msj", "high")
            self.assertIsNotNone(cid)
            # duplicate proposal skipped while one is open
            self.assertIsNone(store.stage_classification(att["id"], "motion", "high"))
            self.assertEqual(len(store.open_classifications()), 1)

            outcome = store.apply_classification(cid, by="Dana", role="paralegal")
            self.assertEqual(outcome, "applied")
            self.assertEqual(store.attachments_for(msg_id)[0]["doc_type"], "motion_msj")
            self.assertEqual(store.open_classifications(), [])
            self.assertEqual(store.apply_classification(cid, by="Dana", role="paralegal"),
                             "already-applied")

    def test_apply_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            msg_id = store.ingest(env_with_attachment("order.pdf"), first_stage="ingest")
            att = store.attachments_for(msg_id)[0]
            with store.db:
                store.db.execute("UPDATE attachments SET doc_type='motion' WHERE id=?", (att["id"],))
            cid = store.stage_classification(att["id"], "order", "high")
            outcome = store.apply_classification(cid, by="Dana", role="paralegal")
            self.assertEqual(outcome, "kept-existing:motion")
            self.assertEqual(store.attachments_for(msg_id)[0]["doc_type"], "motion")


class TestGate(unittest.TestCase):
    def test_gate_proposes_only(self):
        from docketry.gates.classifier import DocClassifier
        findings = DocClassifier().check(env_with_attachment("Subpoena Duces Tecum.pdf"), {})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "info")
        self.assertIn("subpoena", findings[0].summary)


if __name__ == "__main__":
    unittest.main()
