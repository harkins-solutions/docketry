"""Pipeline health, and the line it does not cross."""
import json
import tempfile
import unittest
from pathlib import Path

from docketry.envelope import parse_message
from docketry.report import build, domain_of, percentile
from docketry.store import Store


def _msg(msgid, frm, subject="s"):
    raw = (f"From: {frm}\r\nTo: firm@example.com\r\nSubject: {subject}\r\n"
           f"Message-ID: <{msgid}>\r\n\r\nbody").encode()
    return parse_message(raw, source="intake", fetched_at="now")


class TestPercentile(unittest.TestCase):
    def test_a_median_and_a_bad_tail(self):
        vals = [1.0, 1.0, 1.0, 1.0, 40.0]
        self.assertEqual(percentile(vals, 50), 1.0)
        # The mean would be 8.8 and hide the one that took two days.
        self.assertEqual(percentile(vals, 90), 40.0)

    def test_empty_is_none_not_zero(self):
        self.assertIsNone(percentile([], 50))

    def test_domain_of(self):
        self.assertEqual(domain_of("Clerk@Uscourts.GOV"), "uscourts.gov")
        self.assertEqual(domain_of("not-an-address"), "")


class TestReport(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())

    def _ingest(self, msgid, frm):
        return self.store.ingest(_msg(msgid, frm), first_stage="ingest")

    def test_volume_is_grouped_by_domain(self):
        for i in range(3):
            self._ingest(f"a{i}@x", "clerk@uscourts.gov")
        self._ingest("b@x", "oc@otherfirm.com")
        rep = build(self.store, days=30)
        self.assertEqual(rep.by_domain[0], ("uscourts.gov", 3))

    def test_internal_and_external_split_on_the_firm_domain(self):
        self._ingest("a@x", "kelly@ourfirm.com")
        self._ingest("b@x", "clerk@uscourts.gov")
        rep = build(self.store, days=30, firm_domains=["ourfirm.com"])
        self.assertEqual((rep.internal, rep.external), (1, 1))

    def test_without_firm_domains_it_says_so_rather_than_guessing(self):
        self._ingest("a@x", "clerk@uscourts.gov")
        rep = build(self.store, days=30)
        self.assertEqual((rep.internal, rep.external), (0, 1))
        self.assertTrue(any("no firm domains are configured" in n
                            for n in rep.notes))

    def test_an_adapter_that_went_quiet_is_surfaced(self):
        # Matched last month, nothing since: that court changed its template.
        mid = self._ingest("a@x", "clerk@stlucieclerk.com")
        self.store.add_notice(mid, "st-lucie", "hearing_notice", {}, [])
        self.store.db.execute(
            "UPDATE notices SET created_at = datetime('now', '-45 days')")
        self.store.db.commit()
        rep = build(self.store, days=30)
        self.assertEqual(rep.quiet_adapters, [("st-lucie", 1, 0)])

    def test_an_adapter_still_matching_is_not_flagged(self):
        mid = self._ingest("a@x", "clerk@stlucieclerk.com")
        self.store.add_notice(mid, "st-lucie", "hearing_notice", {}, [])
        self.assertEqual(build(self.store, days=30).quiet_adapters, [])

    def test_a_notice_with_a_link_and_no_copy_is_counted(self):
        mid = self._ingest("a@x", "clerk@uscourts.gov")
        self.store.add_notice(mid, "pacer-nef", "service_notice",
                              {"document_link": "https://ecf.example/x"}, [])
        self.assertEqual(build(self.store, days=30).documents_not_held, 1)

    def test_a_gate_that_never_fired_is_named(self):
        from docketry.manifest import build_pipeline
        pipeline = build_pipeline({"pipeline": {"stages": ["ingest"]},
                                   "gate": [{"id": "provenance-stamp",
                                             "binds_to": ["ingest"]}]})
        rep = build(self.store, pipeline, days=30)
        self.assertEqual(rep.silent_gates, ["provenance-stamp"])

    def test_matters_standing_still_are_listed_with_their_age(self):
        self.store.open_matter("826CV1", stage="intake")
        self.store.db.execute(
            "UPDATE matters SET updated_at = datetime('now', '-60 days')")
        self.store.db.commit()
        rep = build(self.store, days=30, stuck_after_days=30)
        self.assertEqual(rep.stuck_matters[0][0], "826CV1")
        self.assertGreater(rep.stuck_matters[0][2], 55)


class TestItDoesNotMeasurePeople(unittest.TestCase):
    """The line from PR #11, kept: queue health, not people scores."""

    def test_the_report_carries_no_per_person_field(self):
        store = Store(tempfile.mkdtemp())
        mid = store.ingest(_msg("a@x", "clerk@uscourts.gov"), first_stage="ingest")
        store.add_approval(mid, "ingest", "sender-scope",
                           approved_by="Dana Reyes", role="paralegal", note="")
        rep = build(store, days=30)
        blob = json.dumps(rep.__dict__, default=str)
        self.assertNotIn("Dana", blob,
                         "an approver's name reached the report")

    def test_turnaround_is_keyed_by_gate(self):
        store = Store(tempfile.mkdtemp())
        mid = store.ingest(_msg("a@x", "clerk@uscourts.gov"), first_stage="ingest")
        store.add_approval(mid, "ingest", "sender-scope",
                           approved_by="Dana Reyes", role="paralegal", note="")
        rep = build(store, days=30)
        self.assertEqual(list(rep.turnaround), ["sender-scope"])


if __name__ == "__main__":
    unittest.main()


class TestDurationsAreSane(unittest.TestCase):
    def test_a_release_stamped_before_arrival_reads_as_zero_not_negative(self):
        store = Store(tempfile.mkdtemp())
        mid = store.ingest(_msg("a@x", "clerk@uscourts.gov"), first_stage="ingest")
        store.add_approval(mid, "ingest", "sender-scope",
                           approved_by="D", role="paralegal", note="")
        # Clock skew, or a row someone edited by hand.
        store.db.execute("UPDATE messages SET fetched_at = datetime('now', '+1 day')")
        store.db.commit()
        hours = store.release_hours_by_gate(30)["sender-scope"]
        self.assertTrue(all(h >= 0 for h in hours), hours)
        rep = build(store, days=30)
        self.assertGreaterEqual(rep.turnaround["sender-scope"]["p50"], 0)


def _msg_h(msgid, frm, headers=""):
    raw = (f"From: {frm}\r\nTo: firm@example.com\r\nSubject: s\r\n"
           f"Message-ID: <{msgid}>\r\n{headers}\r\n\r\nbody").encode()
    return parse_message(raw, source="intake", fetched_at="now")


class TestOneWaySourcesAreCountedApart(unittest.TestCase):
    """E-service announces; opposing counsel converses. Not the same work."""

    def setUp(self):
        self.store = Store(tempfile.mkdtemp())

    def _add(self, msgid, frm, headers="", notice=False):
        mid = self.store.ingest(_msg_h(msgid, frm, headers), first_stage="ingest")
        if notice:
            self.store.add_notice(mid, "pacer-nef", "service_notice", {}, [])
        return mid

    def test_a_noreply_address_is_a_notification(self):
        self._add("a@x", "noreply@stlucieclerk.com")
        rep = build(self.store, days=30)
        self.assertEqual(rep.notifications, [("stlucieclerk.com", 1)])
        self.assertEqual(rep.correspondence, [])

    def test_a_declared_auto_submitted_header_is_enough(self):
        self._add("b@x", "eservice@myflcourtaccess.com",
                  "Auto-Submitted: auto-generated\r\n")
        self.assertEqual(build(self.store, days=30).notifications,
                         [("myflcourtaccess.com", 1)])

    def test_being_parsed_as_a_court_notice_is_enough(self):
        # No noreply in the address, no headers — but an adapter knew it.
        self._add("c@x", "clerk@uscourts.gov", notice=True)
        rep = build(self.store, days=30)
        self.assertEqual(rep.notifications, [("uscourts.gov", 1)])
        self.assertEqual(rep.correspondence, [])

    def test_a_person_writing_to_you_is_correspondence(self):
        self._add("d@x", "rgarza@opposingfirm.com")
        rep = build(self.store, days=30)
        self.assertEqual(rep.correspondence, [("opposingfirm.com", 1)])
        self.assertEqual(rep.notifications, [])

    def test_volume_from_announcements_does_not_bury_the_real_mail(self):
        for i in range(40):
            self._add(f"n{i}@x", "noreply@court.gov")
        self._add("real@x", "oc@otherfirm.com")
        rep = build(self.store, days=30)
        # The one message a person must answer is not lost behind forty.
        self.assertEqual(rep.correspondence, [("otherfirm.com", 1)])
        self.assertEqual(rep.notifications, [("court.gov", 40)])
        self.assertEqual(dict(rep.by_domain)["court.gov"], 40)

    def test_a_list_id_marks_a_one_way_source(self):
        self._add("e@x", "digest@barassociation.org", "List-Id: <news.bar>\r\n")
        self.assertEqual(build(self.store, days=30).notifications,
                         [("barassociation.org", 1)])


class TestCorrespondenceByWhoTheyAre(unittest.TestCase):
    def setUp(self):
        from docketry.contacts import load_contacts
        self.store = Store(tempfile.mkdtemp())
        p = Path(tempfile.mkdtemp()) / "c.toml"
        p.write_text('[[contact]]\nemail="@theirfirm.com"\n'
                     'kind="opposing_counsel"\n'
                     '[[contact]]\nemail="mr.doe@client.com"\nkind="client"\n')
        self.directory = load_contacts(p)

    def _add(self, msgid, frm):
        self.store.ingest(_msg(msgid, frm), first_stage="ingest")

    def test_volume_is_grouped_by_who_wrote_it(self):
        self._add("a@x", "oc@theirfirm.com")
        self._add("b@x", "also@theirfirm.com")
        self._add("c@x", "mr.doe@client.com")
        rep = build(self.store, days=30, directory=self.directory)
        self.assertEqual(dict(rep.by_kind),
                         {"opposing_counsel": 2, "client": 1})

    def test_an_unknown_sender_is_other_not_a_guess(self):
        self._add("d@x", "someone@nowhere.com")
        rep = build(self.store, days=30, directory=self.directory)
        self.assertEqual(dict(rep.by_kind), {"other": 1})

    def test_without_a_directory_there_is_no_kind_breakdown(self):
        self._add("e@x", "oc@theirfirm.com")
        self.assertEqual(build(self.store, days=30).by_kind, [])

    def test_notifications_are_not_counted_as_someone_writing(self):
        self._add("f@x", "noreply@court.gov")
        self._add("g@x", "oc@theirfirm.com")
        rep = build(self.store, days=30, directory=self.directory)
        self.assertEqual(dict(rep.by_kind), {"opposing_counsel": 1})
