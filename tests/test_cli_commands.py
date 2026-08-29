"""The command surface, exercised the way a person uses it.

The libraries underneath are well covered; these are the commands themselves —
what they print, what they refuse, and what they record.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from docketry.cli import main
from docketry.envelope import parse_message
from docketry.store import Store

EXAMPLES = Path("examples")


def _msg(msgid, frm, subject="Activity in Case", headers=""):
    raw = (f"From: {frm}\r\nTo: firm@ourfirm.com\r\nSubject: {subject}\r\n"
           f"Message-ID: <{msgid}>\r\n{headers}\r\n\r\nbody").encode()
    return parse_message(raw, source="intake", fetched_at="now")


class CliCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()) / "h"
        self._run("init", "--host", "imap.example.com", "--user", "in@example.com")
        (self.home / "workflows").mkdir()
        (self.home / "workflows" / "generic.toml").write_text(
            (EXAMPLES / "workflow-generic.toml").read_text())
        (self.home / "roles.toml").write_text((EXAMPLES / "roles.toml").read_text())
        with (self.home / "config.toml").open("a") as fh:
            fh.write('\n[firm]\ndomains = ["ourfirm.com"]\n')

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--home", str(self.home), *argv])
        return out.getvalue()

    def _fails(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                main(["--home", str(self.home), *argv])
        return str(ctx.exception.code), out.getvalue()

    def _seed(self):
        st = Store(self.home / "store")
        mid = st.ingest(_msg("a@x", "clerk@uscourts.gov"), first_stage="ingest")
        st.add_notice(mid, "pacer-nef", "service_notice",
                      {"case_number": "8:26-cv-01234", "docket_text": "Complaint",
                       "document_number": "1"}, [])
        st.ingest(_msg("b@x", "noreply@stlucieclerk.com"), first_stage="ingest")
        st.ingest(_msg("c@x", "oc@otherfirm.com", "Re: extension"),
                  first_stage="ingest")
        st.add_finding(mid, "ingest", "attachment-policy", "fail", "too large")
        st.close()
        return mid


class TestReportCommand(CliCase):
    def test_it_separates_correspondence_from_notifications(self):
        self._seed()
        out = self._run("report", "--days", "30")
        self.assertIn("correspondence", out)
        self.assertIn("otherfirm.com", out)
        self.assertIn("notifications", out)
        self.assertIn("uscourts.gov", out)

    def test_it_names_gates_that_never_fired(self):
        self._seed()
        out = self._run("report")
        self.assertIn("never fired", out)

    def test_it_says_it_does_not_measure_people(self):
        self._seed()
        self.assertIn("does not measure people", self._run("report"))

    def test_it_reports_what_held_things_up(self):
        self._seed()
        self.assertIn("too large", self._run("report"))


class TestMatterCommands(CliCase):
    def test_open_status_and_advance(self):
        out = self._run("matter-open", "8:26-cv-01234", "--type", "generic",
                        "--name", "Acme v. Widget")
        self.assertIn("intake", out)

        status = self._run("matter-status", "8:26-cv-01234")
        self.assertIn("Acme v. Widget", status)
        self.assertIn("can move to 'open'", status)

        moved = self._run("matter-advance", "8:26-cv-01234", "open",
                          "--by", "Dana Reyes", "--role", "paralegal")
        self.assertIn("intake -> open", moved)
        self.assertIn("Dana Reyes", moved)

        self.assertIn("open", self._run("matters"))

    def test_the_wrong_role_cannot_release_an_attorney_gate(self):
        self._run("matter-open", "26-CA-9", "--type", "generic")
        self._run("matter-advance", "26-CA-9", "open", "--by", "D",
                  "--role", "paralegal")
        self._run("matter-advance", "26-CA-9", "active", "--by", "D",
                  "--role", "paralegal")
        # Several reasons can apply, so this path prints them and exits 1
        # rather than cramming a list into the exit message.
        code, out = self._fails("matter-advance", "26-CA-9", "closed",
                                "--by", "D", "--role", "paralegal")
        self.assertEqual(code, "1")
        self.assertIn("released by an attorney", out)

    def test_matters_lists_nothing_before_any_exist(self):
        self.assertIn("no matters yet", self._run("matters"))


class TestRolesCommand(CliCase):
    def test_it_lists_what_each_role_may_release(self):
        out = self._run("roles")
        self.assertIn("attorney", out)
        self.assertIn("paralegal", out)
        self.assertIn("catches mistakes, not lies", out)

    def test_without_a_registry_it_says_authority_is_unchecked(self):
        (self.home / "roles.toml").unlink()
        self.assertIn("NOT checked", self._run("roles"))


class TestTimelineCommands(CliCase):
    def test_timeline_shows_the_disclaimer_with_the_entries(self):
        self._seed()
        out = self._run("timeline", "8:26-cv-01234")
        self.assertIn("Complaint", out)
        self.assertIn("NOT the court's docket", out)

    def test_export_refuses_an_unknown_extension(self):
        self._seed()
        code, _ = self._fails("timeline-export", "8:26-cv-01234", "/tmp/x.rtf")
        self.assertIn(".xlsx or .docx", code)

    def test_export_writes_a_workbook(self):
        self._seed()
        out_path = self.home / "t.xlsx"
        self._run("timeline-export", "8:26-cv-01234", str(out_path))
        self.assertTrue(out_path.exists())


class TestWorkflowCheck(CliCase):
    def test_it_shows_where_a_bare_matter_holds(self):
        out = self._run("workflow-check",
                        str(self.home / "workflows" / "generic.toml"))
        self.assertIn("holds before 'open'", out)

    def test_supplying_a_fact_walks_it_further(self):
        out = self._run("workflow-check",
                        str(self.home / "workflows" / "generic.toml"),
                        "--field", "case_number")
        self.assertIn("intake -> open -> active", out)


class TestStatusAndNotices(CliCase):
    def test_status_counts_messages(self):
        self._seed()
        self.assertIn("ok", self._run("status"))

    def test_notices_lists_what_was_parsed(self):
        self._seed()
        self.assertIn("pacer-nef", self._run("notices"))

    def test_notices_says_so_when_there_are_none(self):
        self.assertIn("no notices", self._run("notices"))


if __name__ == "__main__":
    unittest.main()
