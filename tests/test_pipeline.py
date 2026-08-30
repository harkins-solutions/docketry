import tempfile
import unittest
from pathlib import Path

from docketry import store as st
from docketry.envelope import Attachment, Envelope
from docketry.pipeline import Finding, GateBinding, GateRefusal, Pipeline, Runner, SEVERITY_FAIL
from docketry.store import Store, StoreIntegrityError


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


class ContentGate:
    """A gate of the kind third parties are invited to write: it reads bytes."""
    id = "reads-content"
    allowed_stages = None

    def __init__(self):
        self.seen = []

    def check(self, envelope, options):
        self.seen.append([a.content for a in envelope.attachments])
        if any(b"CONTRABAND" in a.content for a in envelope.attachments):
            return [Finding(self.id, SEVERITY_FAIL, "found it")]
        return []


class TestAttachmentBytesSurviveARerun(unittest.TestCase):
    """A gate that inspects bytes must see the same bytes on every run.

    The runner rebuilt attachments with empty content, so a plugin gate would
    work at ingest — where the parsed message is still in hand — and silently
    find nothing when advance() re-ran it. The shipped gates only read
    filenames, which is why nothing caught it.
    """

    def _fixture(self, tmp, payload):
        import hashlib
        store = Store(tmp)
        gate = ContentGate()
        pipeline = Pipeline(
            stages=["ingest", "review"],
            bindings=[GateBinding(gate=gate, binds_to=["ingest", "review"],
                                  on_fail="bounce", authority="attorney")],
        )
        a = Attachment(filename="doc.pdf", content_type="application/pdf",
                       sha256=hashlib.sha256(payload).hexdigest(),
                       size=len(payload), content=payload)
        msg_id = store.ingest(env(attachments=[a]), first_stage="ingest")
        return store, gate, Runner(pipeline, store), msg_id

    def test_advance_hands_the_gate_the_real_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gate, runner, msg_id = self._fixture(tmp, b"%PDF harmless")
            runner.enter(msg_id)
            runner.advance(msg_id)
            self.assertEqual(len(gate.seen), 2)     # ingest, then review
            for seen in gate.seen:
                self.assertEqual(seen, [b"%PDF harmless"])

    def test_a_hold_is_not_released_by_re_running_a_blind_gate(self):
        # The trap this closes: the gate fails at ingest on the bytes, then
        # passes on the re-run because it was handed nothing to look at.
        with tempfile.TemporaryDirectory() as tmp:
            store, gate, runner, msg_id = self._fixture(tmp, b"CONTRABAND")
            self.assertEqual(runner.enter(msg_id), st.PENDING_REVIEW)
            with self.assertRaises(GateRefusal):
                runner.advance(msg_id)

    def test_bytes_that_no_longer_match_their_digest_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gate, runner, msg_id = self._fixture(tmp, b"%PDF harmless")
            runner.enter(msg_id)
            row = store.attachments_for(msg_id)[0]
            Path(row["path"]).write_bytes(b"something else entirely")
            with self.assertRaises(StoreIntegrityError):
                runner.advance(msg_id)


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
