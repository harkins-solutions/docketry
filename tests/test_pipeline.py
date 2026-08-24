import tempfile
import unittest

from docketry import store as st
from docketry.envelope import Attachment, Envelope
from docketry.pipeline import Finding, GateBinding, GateRefusal, Pipeline, Runner, SEVERITY_FAIL
from docketry.store import Store


def env(attachments=None, from_addr="portal@court.gov"):
    return Envelope(
        message_id="m1", from_addr=from_addr, to=["intake@firm.com"], cc=[],
        date="", subject="test", body_text="body",
        attachments=attachments or [], raw_sha256="a" * 64,
        source="test", fetched_at="now",
    )


class FailingGate:
    id = "always-fail"
    allowed_stages = None

    def check(self, envelope, options):
        return [Finding(self.id, SEVERITY_FAIL, "nope")]


class CleanGate:
    id = "always-clean"
    allowed_stages = None

    def check(self, envelope, options):
        return []


def build(tmp, on_fail):
    store = Store(tmp)
    pipeline = Pipeline(
        stages=["ingest", "review"],
        bindings=[GateBinding(gate=FailingGate(), binds_to=["ingest"], on_fail=on_fail, authority="attorney")],
    )
    return store, Runner(pipeline, store)


class TestRunner(unittest.TestCase):
    def test_bounce_holds_until_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, runner = build(tmp, "bounce")
            msg_id = store.ingest(env(), first_stage="ingest")
            self.assertEqual(runner.enter(msg_id), st.PENDING_REVIEW)
            with self.assertRaises(GateRefusal):
                runner.advance(msg_id)
            # wrong role does not clear it
            store.add_approval(msg_id, "ingest", "always-fail", approved_by="x", role="paralegal")
            with self.assertRaises(GateRefusal):
                runner.advance(msg_id)
            # declared authority clears it
            store.add_approval(msg_id, "ingest", "always-fail", approved_by="x", role="attorney")
            self.assertEqual(runner.advance(msg_id), st.DONE)

    def test_block_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, runner = build(tmp, "block")
            msg_id = store.ingest(env(), first_stage="ingest")
            self.assertEqual(runner.enter(msg_id), st.BLOCKED)
            with self.assertRaises(GateRefusal):
                runner.advance(msg_id)

    def test_warn_proceeds_but_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, runner = build(tmp, "warn")
            msg_id = store.ingest(env(), first_stage="ingest")
            self.assertEqual(runner.enter(msg_id), st.OK)
            self.assertEqual(runner.advance(msg_id), st.DONE)
            severities = {f["severity"] for f in store.findings_for(msg_id)}
            self.assertIn("fail", severities)

    def test_clean_pipeline_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pipeline = Pipeline(stages=["ingest"], bindings=[GateBinding(gate=CleanGate(), binds_to=["ingest"])])
            runner = Runner(pipeline, store)
            msg_id = store.ingest(env(), first_stage="ingest")
            self.assertEqual(runner.enter(msg_id), st.DONE)

    def test_ingest_dedupes_on_raw_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertIsNotNone(store.ingest(env(), first_stage="ingest"))
            self.assertIsNone(store.ingest(env(), first_stage="ingest"))

    def test_attachment_bytes_hit_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            a = Attachment(filename="x.pdf", content_type="application/pdf",
                           sha256="b" * 64, size=4, content=b"%PDF")
            msg_id = store.ingest(env(attachments=[a]), first_stage="ingest")
            row = store.db.execute("SELECT * FROM attachments WHERE message_id=?", (msg_id,)).fetchone()
            with open(row["path"], "rb") as fh:
                self.assertEqual(fh.read(), b"%PDF")


if __name__ == "__main__":
    unittest.main()
