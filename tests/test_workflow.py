"""Stage gates: a matter moves when the record supports it, and not before."""
import tempfile
import unittest
from pathlib import Path

from docketry.tools.workflow import (
    Condition,
    MatterFacts,
    WorkflowError,
    available,
    check,
    load_workflow,
    simulate,
)

GENERIC = Path("examples/workflow-generic.toml")


def _wf(body: str) -> Path:
    d = Path(tempfile.mkdtemp()) / "w.toml"
    d.write_text(body)
    return d


class TestLoad(unittest.TestCase):
    def test_the_shipped_example_loads(self):
        wf = load_workflow(GENERIC)
        self.assertEqual(wf.matter_type, "generic")
        self.assertEqual(wf.first_stage, "intake")
        self.assertEqual(wf.as_of, "2026-08-29")

    def test_the_shipped_example_carries_no_practice_opinion(self):
        # If this ever fails, someone has put a firm's strategy in the box we
        # ship. The shape is ours; the practice is theirs.
        text = GENERIC.read_text().lower()
        for leaked in ("demand", "settlement", "deposition", "mediation",
                       "personal injury", "immigration", "plaintiff",
                       "defendant", "day 45", "statute"):
            self.assertNotIn(leaked, text, f"shipped workflow mentions {leaked!r}")

    def test_a_transition_to_an_undeclared_stage_is_refused(self):
        with self.assertRaises(WorkflowError) as ctx:
            load_workflow(_wf('[workflow]\nmatter_type="x"\nstages=["a","b"]\n'
                              '[[transition]]\nfrom="a"\nto="nowhere"\n'))
        self.assertIn("not one of the declared stages", str(ctx.exception))

    def test_a_dead_end_stage_is_refused(self):
        # A matter that reaches it could never move again.
        with self.assertRaises(WorkflowError) as ctx:
            load_workflow(_wf('[workflow]\nmatter_type="x"\n'
                              'stages=["a","b","c"]\n'
                              '[[transition]]\nfrom="a"\nto="b"\n'))
        self.assertIn("can never move again", str(ctx.exception))

    def test_one_stage_is_not_a_workflow(self):
        with self.assertRaises(WorkflowError):
            load_workflow(_wf('[workflow]\nmatter_type="x"\nstages=["a"]\n'))

    def test_a_duplicate_stage_is_refused(self):
        with self.assertRaises(WorkflowError):
            load_workflow(_wf('[workflow]\nmatter_type="x"\nstages=["a","a"]\n'))

    def test_a_nonsense_condition_says_what_the_shape_should_be(self):
        with self.assertRaises(WorkflowError) as ctx:
            Condition.parse("complaint filed")
        self.assertIn("document:complaint", str(ctx.exception))


class TestGates(unittest.TestCase):
    def setUp(self):
        self.wf = load_workflow(GENERIC)

    def test_a_missing_condition_blocks_the_move_and_says_why(self):
        blocked = check(self.wf, "intake", "open", MatterFacts())
        self.assertIsNotNone(blocked)
        self.assertIn("'case_number' is not filled in", blocked.reasons[0])

    def test_the_move_opens_once_the_record_supports_it(self):
        facts = MatterFacts(fields={"case_number"})
        self.assertIsNone(check(self.wf, "intake", "open", facts))

    def test_a_move_that_does_not_exist_is_refused(self):
        blocked = check(self.wf, "intake", "closed", MatterFacts())
        self.assertIn("does not lead to", blocked.reasons[0])

    def test_an_approval_is_itself_a_hold(self):
        # Conditions met, but a named role still has to release it.
        blocked = check(self.wf, "active", "closed", MatterFacts())
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked.needs_authority, "attorney")
        self.assertEqual(blocked.reasons, [])

    def test_available_lists_every_way_out_and_what_each_awaits(self):
        moves = available(self.wf, "intake", MatterFacts())
        self.assertEqual([m.target for m in moves], ["open"])
        self.assertTrue(moves[0].reasons)


class TestSandbox(unittest.TestCase):
    """Watch a workflow run before saving it, rather than being told it is valid."""

    def test_it_reports_where_an_empty_matter_stops(self):
        wf = load_workflow(GENERIC)
        path, blocked = simulate(wf, MatterFacts())
        self.assertEqual(path, ["intake"])
        self.assertEqual(blocked.target, "open")

    def test_it_walks_further_as_the_record_fills_in(self):
        wf = load_workflow(GENERIC)
        path, blocked = simulate(wf, MatterFacts(fields={"case_number"}))
        self.assertEqual(path, ["intake", "open", "active"])
        self.assertEqual(blocked.needs_authority, "attorney")

    def test_a_workflow_with_no_gates_runs_straight_through(self):
        wf = load_workflow(_wf('[workflow]\nmatter_type="x"\nstages=["a","b"]\n'
                               '[[transition]]\nfrom="a"\nto="b"\n'))
        path, blocked = simulate(wf, MatterFacts())
        self.assertEqual(path, ["a", "b"])
        self.assertIsNone(blocked)

    def test_a_looping_workflow_is_caught_rather_than_hanging(self):
        wf = load_workflow(_wf('[workflow]\nmatter_type="x"\nstages=["a","b"]\n'
                               '[[transition]]\nfrom="a"\nto="b"\n'
                               '[[transition]]\nfrom="b"\nto="a"\n'))
        path, blocked = simulate(wf, MatterFacts())
        self.assertIn("loops", blocked.reasons[0])


if __name__ == "__main__":
    unittest.main()


class TestFactsFromTheRecord(unittest.TestCase):
    """Gates read what is actually filed, and never assume."""

    def setUp(self):
        from docketry.core.store import Store
        self.tmp = tempfile.mkdtemp()
        self.store = Store(self.tmp)

    def _notice(self, msgid, case, ntype="service_notice"):
        from docketry.core.envelope import parse_message
        raw = (f"From: clerk@uscourts.gov\r\nTo: f@x.com\r\nSubject: s\r\n"
               f"Message-ID: <{msgid}>\r\n\r\nbody").encode()
        mid = self.store.ingest(parse_message(raw, source="t", fetched_at="now"),
                                first_stage="ingest")
        self.store.add_notice(mid, "pacer-nef", ntype, {"case_number": case}, [])
        return mid

    def test_an_empty_record_satisfies_nothing(self):
        from docketry.tools.workflow import facts_from_store
        facts = facts_from_store(self.store, "826CV01234")
        self.assertEqual((facts.documents, facts.notices, facts.fields),
                         (set(), set(), set()))

    def test_notices_on_this_case_become_facts(self):
        from docketry.tools.workflow import facts_from_store
        self._notice("a@x", "8:26-cv-01234", "service_notice")
        self._notice("b@x", "8:26-cv-01234", "hearing_notice")
        facts = facts_from_store(self.store, "826CV01234")
        self.assertEqual(facts.notices, {"service_notice", "hearing_notice"})

    def test_another_case_does_not_leak_in(self):
        from docketry.tools.workflow import facts_from_store
        self._notice("c@x", "9:26-cv-99999")
        self.assertEqual(facts_from_store(self.store, "826CV01234").notices, set())

    def test_case_numbers_match_across_formatting(self):
        from docketry.tools.workflow import facts_from_store
        self._notice("d@x", "8:26-cv-01234")
        self.assertTrue(facts_from_store(self.store, "826CV01234").notices)

    def test_opening_a_matter_fills_the_case_number_field(self):
        from docketry.tools.workflow import facts_from_store
        self.store.open_matter("826CV01234", stage="intake")
        self.assertIn("case_number", facts_from_store(self.store, "826CV01234").fields)


class TestMatterRecord(unittest.TestCase):
    def setUp(self):
        from docketry.core.store import Store
        self.store = Store(tempfile.mkdtemp())
        self.mid = self.store.open_matter("826CV01234", stage="intake")

    def test_opening_the_same_case_twice_returns_the_same_matter(self):
        self.assertEqual(self.store.open_matter("826CV01234", stage="intake"),
                         self.mid)

    def test_a_move_is_recorded_with_who_made_it(self):
        self.store.move_matter(self.mid, "open", by="Dana", role="paralegal")
        events = self.store.matter_events(self.mid)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["from_stage"], events[0]["to_stage"],
                          events[0]["moved_by"]), ("intake", "open", "Dana"))

    def test_an_unattributed_move_is_refused(self):
        # A matter's stage is a claim about where the work stands.
        with self.assertRaises(ValueError):
            self.store.move_matter(self.mid, "open", by="   ")
        self.assertEqual(self.store.get_matter("826CV01234")["stage"], "intake")

    def test_history_survives_later_moves(self):
        self.store.move_matter(self.mid, "open", by="Dana")
        self.store.move_matter(self.mid, "active", by="Sam")
        self.assertEqual([e["to_stage"] for e in self.store.matter_events(self.mid)],
                         ["open", "active"])
