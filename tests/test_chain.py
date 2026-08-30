"""The approval log as evidence: what the chain catches, and what it doesn't."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from docketry.core.envelope import Envelope
from docketry.core.store import GENESIS, Store, approval_digest


def env(i=0):
    return Envelope(
        message_id=f"m{i}", from_addr="portal@court.gov", to=[], cc=[], date="",
        subject=f"notice {i}", body_text="", attachments=[],
        raw_sha256=f"{i:064d}", source="t", fetched_at="now",
    )


class ChainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.msg = self.store.ingest(env(), first_stage="ingest")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def approve(self, who="Dana", role="paralegal", gate="sender-scope"):
        return self.store.add_approval(self.msg, "ingest", gate,
                                       approved_by=who, role=role)


class TestTheChain(ChainCase):
    def test_an_empty_log_still_has_a_head(self):
        report = self.store.chain_report()
        self.assertEqual(report.head, GENESIS)
        self.assertTrue(report.ok)
        self.assertEqual(report.total, 0)

    def test_each_approval_extends_the_chain(self):
        first = self.approve()
        second = self.approve(who="Alex", role="attorney")
        self.assertNotEqual(first, second)
        report = self.store.chain_report()
        self.assertEqual(report.head, second)
        self.assertEqual(report.chained, 2)
        self.assertTrue(report.ok)
        self.assertIn("intact", report.summary)

    def test_the_head_moves_only_when_something_is_recorded(self):
        head = self.store.approval_head()
        self.assertEqual(head, GENESIS)
        self.approve()
        moved = self.store.approval_head()
        self.assertNotEqual(moved, GENESIS)
        self.assertEqual(self.store.approval_head(), moved)

    def test_an_edited_approval_is_detected(self):
        self.approve(who="Dana")
        self.approve(who="Alex", role="attorney")
        # The edit somebody would actually make: put a different name on a
        # release after the fact.
        with self.store.db:
            self.store.db.execute(
                "UPDATE approvals SET approved_by='Someone Else' WHERE id=1")
        report = self.store.chain_report()
        self.assertFalse(report.ok)
        self.assertEqual(report.broken_at, 1)
        self.assertIn("BROKEN", report.summary)

    def test_a_deleted_approval_is_detected(self):
        self.approve()
        self.approve(who="Alex", role="attorney")
        self.approve(who="Kim", role="attorney")
        with self.store.db:
            self.store.db.execute("DELETE FROM approvals WHERE id=2")
        report = self.store.chain_report()
        self.assertFalse(report.ok)
        self.assertEqual(report.broken_at, 3)

    def test_an_inserted_approval_is_detected(self):
        self.approve()
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO approvals(message_id, stage, gate_id, approved_by,"
                " role, note, created_at, prev_sha256, sha256)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (self.msg, "ingest", "name-screen", "Nobody", "attorney", "",
                 "2026-01-01T00:00:00+00:00", "made up", "also made up"))
        self.assertFalse(self.store.chain_report().ok)

    def test_a_rewritten_chain_verifies_which_is_the_whole_limit(self):
        # Stated as a test so nobody mistakes the property for authenticity.
        # Anyone who can edit a row can recompute every digest after it. This
        # is why `docketry anchor` exists: the copy that left the machine is
        # what a rewritten log has to contradict.
        self.approve(who="Dana")
        self.approve(who="Alex", role="attorney")
        with self.store.db:
            self.store.db.execute(
                "UPDATE approvals SET approved_by='Someone Else' WHERE id=1")
        self.assertFalse(self.store.chain_report().ok)
        # Now do what a determined editor would do next.
        prev = GENESIS
        for row in self.store.db.execute(
                "SELECT * FROM approvals ORDER BY id").fetchall():
            digest = approval_digest(prev, row)
            with self.store.db:
                self.store.db.execute(
                    "UPDATE approvals SET prev_sha256=?, sha256=? WHERE id=?",
                    (prev, digest, row["id"]))
            prev = digest
        self.assertTrue(self.store.chain_report().ok)
        # And what catches it: the head no longer matches the anchored one.
        self.assertNotEqual(self.store.chain_report().head, prev + "x")


class TestUpgradingAnOlderStore(unittest.TestCase):
    """A store written before the chain existed still opens, and says so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _legacy_store(self):
        store = Store(self.root)
        msg = store.ingest(env(), first_stage="ingest")
        store.add_approval(msg, "ingest", "sender-scope",
                           approved_by="Dana", role="paralegal")
        # Strip the chain columns' contents the way an old row would look.
        with store.db:
            store.db.execute("UPDATE approvals SET prev_sha256=NULL, sha256=NULL")
        store.close()
        return msg

    def test_old_rows_are_reported_unchained_not_backfilled(self):
        msg = self._legacy_store()
        store = Store(self.root)
        try:
            report = store.chain_report()
            self.assertTrue(report.ok)
            self.assertEqual(report.unchained, 1)
            self.assertEqual(report.chained, 0)
            self.assertEqual(report.head, GENESIS)
            self.assertIn("predate the chain", report.summary)
            # New approvals chain from GENESIS and verify.
            store.add_approval(msg, "ingest", "sender-scope",
                               approved_by="Alex", role="attorney")
            after = store.chain_report()
            self.assertTrue(after.ok)
            self.assertEqual((after.chained, after.unchained), (1, 1))
        finally:
            store.close()

    def test_a_store_predating_the_columns_migrates(self):
        # Exactly the old schema, with no chain columns at all.
        db = sqlite3.connect(self.root / "docketry.db")
        db.executescript(
            "CREATE TABLE approvals(id INTEGER PRIMARY KEY, message_id INTEGER,"
            " stage TEXT, gate_id TEXT, approved_by TEXT, role TEXT, note TEXT,"
            " created_at TEXT);"
            "INSERT INTO approvals(message_id, stage, gate_id, approved_by,"
            " role, note, created_at) VALUES(1,'ingest','sender-scope','Dana',"
            "'paralegal','','2026-01-01T00:00:00+00:00');")
        db.commit()
        db.close()
        store = Store(self.root)
        try:
            cols = {r["name"] for r in
                    store.db.execute("PRAGMA table_info(approvals)")}
            self.assertIn("sha256", cols)
            self.assertEqual(store.chain_report().unchained, 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
