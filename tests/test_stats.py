import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from docketry.core import store as st
from docketry.core.envelope import Envelope
from docketry.core.manifest import load_manifest
from docketry.core.pipeline import Runner
from docketry.core.store import Store

MANIFEST = Path("examples/guardrails-litigation-team.toml")


def env(i, from_addr):
    return Envelope(
        message_id=f"m{i}", from_addr=from_addr, to=[], cc=[], date="",
        subject="SERVICE OF COURT DOCUMENT", body_text=f"Case Number: 562026CA{i:06d}",
        attachments=[], raw_sha256=f"{i:x}".rjust(64, "b")[:64],
        source="t", fetched_at=st.utcnow(),
    )


class TestStats(unittest.TestCase):
    def test_stats_reflect_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            pipeline = load_manifest(MANIFEST)
            runner = Runner(pipeline, store)
            # two clean portal messages, one stranger (held then released)
            for i, sender in enumerate(["eservice@myflcourtaccess.com",
                                        "eservice@myflcourtaccess.com",
                                        "stranger@x.net"]):
                e = env(i, sender)
                mid = store.ingest(e, first_stage="ingest")
                from docketry.tools import notices as nmod
                res = nmod.parse(e, nmod.stack())
                if res is not None:
                    store.add_notice(mid, res.adapter, res.notice_type,
                                     res.fields, res.missing)
                status = runner.enter(mid)
                while status == st.OK:
                    status = runner.advance(mid)
            held = store.list_by_status(st.PENDING_REVIEW)
            self.assertEqual(len(held), 1)
            store.add_approval(held[0]["id"], "ingest", "sender-scope",
                               approved_by="Dana", role="paralegal")
            status = runner.advance(held[0]["id"])
            while status == st.OK:
                status = runner.advance(held[0]["id"])

            s = store.stats(days=7)
            self.assertEqual(s["ingested"], 3)
            self.assertEqual(s["by_status"].get(st.DONE), 3)
            self.assertEqual(s["holds_by_gate"].get("sender-scope"), 1)
            self.assertEqual(s["approvals_by_role"].get("paralegal"), 1)
            self.assertIsNotNone(s["avg_hours_to_release"])
            self.assertGreaterEqual(s["notices_by_type"].get("service_notice", 0), 2)
            self.assertEqual(s["template_drift_messages"], 0)
            store.close()

    def test_stats_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            s = store.stats()
            self.assertEqual(s["ingested"], 0)
            self.assertIsNone(s["avg_hours_to_release"])
            store.close()


class TestDigestNeverSends(unittest.TestCase):
    def test_no_send_path_exists_anywhere(self):
        """The trust property, enforced: no SMTP/send capability in the package."""
        import subprocess
        out = subprocess.run(
            ["git", "grep", "-lniE", r"smtplib|sendmail|\.send_message\(|starttls",
             "--", "docketry/"],
            capture_output=True, text=True,
        )
        self.assertEqual(out.stdout.strip(), "",
                         f"send-capable code found in: {out.stdout}")


if __name__ == "__main__":
    unittest.main()
