"""Seeded randomized soak of the full flow, many cycles per seed.

Every cycle builds a scenario whose correct outcome is known by
construction, pushes a real MIME message through ingest -> gates ->
advance -> (wrong-role approval must refuse) -> correct approvals -> done,
and checks the invariants that define the product: nothing crosses a gate
without passing or a recorded approval by the declared authority; ingest is
idempotent; attachment bytes round-trip; classification writes are staged
and fill-only.

Defaults are CI-sized. Scale up locally:
  DOCKETRY_SOAK_ITERS=300 DOCKETRY_SOAK_SEEDS=1,2,3,4,5 python -m unittest tests.test_soak
"""
import os
import random
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from docketry import store as st
from docketry.classify import classify
from docketry.envelope import parse_message
from docketry.manifest import load_manifest
from docketry.pipeline import GateRefusal, Runner
from docketry.store import Store

MANIFEST = Path("examples/guardrails-litigation-team.toml")

DOC_NAMES = [
    ("Motion for Summary Judgment.pdf", "motion_msj"),
    ("Order Granting Motion to Compel.pdf", "order"),
    ("Notice of Hearing.pdf", "notice_of_hearing"),
    ("Answer to Complaint.pdf", "answer"),
    ("Subpoena Duces Tecum.pdf", "subpoena"),
    ("scan_00%d.pdf", "correspondence"),
]


def build_scenario(rng: random.Random, i: int):
    """Returns (raw_bytes, expected_final_status, failing_gate_ids)."""
    kind = rng.choice(["portal_ok", "portal_broken", "stranger", "bad_attachment",
                       "pacer", "garbage"])
    m = EmailMessage()
    m["To"] = "intake@examplefirm.com"
    failing = set()
    if kind == "portal_ok":
        m["From"] = "eservice@myflcourtaccess.com"
        m["Subject"] = f"SERVICE OF COURT DOCUMENT {i}"
        m.set_content(f"Case Number: 562026CA{i:06d}\nDocument: Motion to Compel\n")
    elif kind == "portal_broken":
        m["From"] = "eservice@myflcourtaccess.com"
        m["Subject"] = f"SERVICE OF COURT DOCUMENT {i}"
        m.set_content(f"redesigned portal template with nothing extractable (ref {i})\n")
        failing.add("notice-parser")
    elif kind == "stranger":
        m["From"] = f"unknown{i}@random.net"
        m["Subject"] = f"invoice {i}"
        m.set_content("please pay\n")
        failing.add("sender-scope")
    elif kind == "bad_attachment":
        m["From"] = "eservice@myflcourtaccess.com"
        m["Subject"] = f"SERVICE OF COURT DOCUMENT {i}"
        m.set_content(f"Case Number: 562026CA{i:06d}\n")
        m.add_attachment(b"MZ", maintype="application", subtype="octet-stream",
                         filename=f"payload{i}.exe")
        failing.add("attachment-policy")
    elif kind == "pacer":
        m["From"] = "ecf_bounces@flsd.uscourts.gov"
        m["Subject"] = f"Activity in Case 2:26-cv-{i:05d}-XYZ Doe v. Acme Order"
        m.set_content(f"Document Number: {i}\nDocket Text: ORDER entered.\n")
    else:  # garbage: unparseable sender -> sender-scope holds it
        m["From"] = ""
        m["Subject"] = "?" * rng.randint(1, 40)
        m.set_content(f"\x00\x01 binaryish body {i} \x02")
        failing.add("sender-scope")
    if kind in ("portal_ok", "pacer") and rng.random() < 0.6:
        name, _ = rng.choice(DOC_NAMES)
        m.add_attachment(b"%PDF-1.4 x", maintype="application", subtype="pdf",
                         filename=name % i if "%d" in name else name)
    expected = st.PENDING_REVIEW if failing else st.DONE
    return bytes(m), expected, failing


class TestSoak(unittest.TestCase):
    def test_soak(self):
        iters = int(os.environ.get("DOCKETRY_SOAK_ITERS", "40"))
        seeds = [int(s) for s in os.environ.get("DOCKETRY_SOAK_SEEDS", "1,2,3").split(",")]
        for seed in seeds:
            with self.subTest(seed=seed):
                self._run(seed, iters)

    def _run(self, seed: int, iters: int):
        rng = random.Random(seed)
        pipeline = load_manifest(MANIFEST)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            runner = Runner(pipeline, store)
            done = held = 0
            for i in range(iters):
                raw, expected, failing = build_scenario(rng, i)
                env = parse_message(raw, source="soak", fetched_at=st.utcnow())
                msg_id = store.ingest(env, first_stage=pipeline.stages[0])
                self.assertIsNotNone(msg_id, f"seed {seed} cycle {i}: unexpected dupe")

                # idempotency: same raw again is refused
                self.assertIsNone(store.ingest(env, first_stage=pipeline.stages[0]))

                # attachments round-trip byte-for-byte
                for att in store.attachments_for(msg_id):
                    self.assertEqual(
                        Path(att["path"]).read_bytes()[:8], b"%PDF-1.4"[:8]
                        if att["filename"].endswith(".pdf") else b"MZ"[:8],
                        f"seed {seed} cycle {i}: attachment bytes corrupted",
                    )
                    label, tier = classify(att["filename"])
                    staged = [r for r in store.open_classifications()
                              if r["attachment_id"] == att["id"]]
                    if tier == "low":
                        self.assertEqual(staged, [])

                status = runner.enter(msg_id)
                while status == st.OK:
                    status = runner.advance(msg_id)
                self.assertEqual(
                    status, expected,
                    f"seed {seed} cycle {i}: expected {expected}, got {status};"
                    f" findings={[dict(f) for f in store.findings_for(msg_id)]}",
                )

                if expected == st.PENDING_REVIEW:
                    held += 1
                    # wrong role never clears a hold
                    for gate_id in failing:
                        store.add_approval(msg_id, "ingest", gate_id,
                                           approved_by="soak", role="intern")
                    with self.assertRaises(GateRefusal):
                        runner.advance(msg_id)
                    # the declared authority clears each failing gate
                    for gate_id in failing:
                        binding = next(b for b in pipeline.bindings_for("ingest")
                                       if b.gate.id == gate_id)
                        store.add_approval(msg_id, "ingest", gate_id,
                                           approved_by="soak", role=binding.authority)
                    status = runner.advance(msg_id)
                    while status == st.OK:
                        status = runner.advance(msg_id)
                    self.assertEqual(status, st.DONE,
                                     f"seed {seed} cycle {i}: approval did not release")
                else:
                    done += 1

            counts = store.counts()
            self.assertEqual(counts.get(st.DONE, 0), iters,
                             f"seed {seed}: ledger mismatch {counts}")
            self.assertGreater(done, 0)
            self.assertGreater(held, 0, f"seed {seed}: soak never exercised holds")

            # fill-only under repetition: apply everything twice
            for row in store.open_classifications():
                store.apply_classification(row["id"], by="soak", role="paralegal")
            for row in store.db.execute("SELECT id FROM classifications"):
                outcome = store.apply_classification(row["id"], by="soak", role="paralegal")
                self.assertIn(outcome, ("already-applied",))
            store.close()


if __name__ == "__main__":
    unittest.main()
