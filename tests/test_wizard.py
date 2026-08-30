"""Setup as questions: the config surface nobody should have to author."""
import tempfile
import unittest
from pathlib import Path

from docketry.core.config import load_home
from docketry.core.manifest import load_manifest
from docketry.core.roles import load_roles
from docketry.wizard import (
    Answers,
    Asker,
    WizardAborted,
    guardrails_toml,
    imap_guess,
    interview,
    roles_toml,
    run,
    validate,
    write,
)


def scripted(*answers):
    """An Asker that reads a fixed script and swallows what it prints."""
    queue = list(answers)
    said = []

    def read(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return Asker(input_fn=read, out=said.append, getpass_fn=lambda p: "hunter2"), said


# The answers a small firm would actually give, in order.
FIRM = [
    "intake@smallfirm.com",   # address
    "",                       # imap host — accept the guess
    "",                       # folder
    "n",                      # store the password? no
    "",                       # firm domains — accept the guess
    "legal assistant",        # who reviews intake
    "attorney",               # who releases anything
    "Acme Insurance, Roberta Vance",   # the wall
    "",                       # e-service senders — accept the defaults
    "",                       # attachment cap
]


class TestQuestions(unittest.TestCase):
    def test_the_imap_host_is_guessed_from_the_address(self):
        self.assertEqual(imap_guess("intake@gmail.com"), "imap.gmail.com")
        self.assertEqual(imap_guess("intake@outlook.com"), "outlook.office365.com")
        # An unknown domain still gets a sensible default to correct.
        self.assertEqual(imap_guess("intake@smallfirm.com"), "imap.smallfirm.com")
        self.assertEqual(imap_guess("nonsense"), "")

    def test_the_answers_are_read_in_order(self):
        ask, _ = scripted(*FIRM)
        a = interview(ask)
        self.assertEqual(a.address, "intake@smallfirm.com")
        self.assertEqual(a.host, "imap.smallfirm.com")
        self.assertEqual(a.folder, "INBOX")
        self.assertFalse(a.store_password)
        self.assertEqual(a.firm_domains, ("smallfirm.com",))
        self.assertEqual(a.reviewer, "legal assistant")
        self.assertEqual(a.screened, ("Acme Insurance", "Roberta Vance"))
        self.assertIn("@myflcourtaccess.com", a.eservice)
        self.assertEqual(a.max_size_mb, 25.0)

    def test_a_stored_password_is_read_without_echoing(self):
        answers = list(FIRM)
        answers[3] = "y"
        ask, _ = scripted(*answers)
        a = interview(ask)
        self.assertTrue(a.store_password)
        self.assertEqual(a.password, "hunter2")

    def test_typing_none_clears_a_populated_default(self):
        answers = list(FIRM)
        answers[8] = "none"
        ask, _ = scripted(*answers)
        self.assertEqual(interview(ask).eservice, ())

    def test_walking_away_writes_nothing(self):
        ask, _ = scripted("intake@smallfirm.com")   # then EOF
        with self.assertRaises(WizardAborted) as ctx:
            interview(ask)
        self.assertIn("nothing was written", str(ctx.exception))

    def test_a_required_answer_is_asked_again(self):
        ask, said = scripted("", "intake@smallfirm.com", *FIRM[1:])
        self.assertEqual(interview(ask).address, "intake@smallfirm.com")
        self.assertIn("  (this one is needed)", said)


class TestWhatItWrites(unittest.TestCase):
    def _answers(self, script=None):
        ask, _ = scripted(*(script or FIRM))
        return interview(ask)

    def test_the_files_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            written = write(home, self._answers())
            self.assertEqual([p.name for p in written],
                             ["config.toml", "guardrails.toml", "roles.toml"])
            registry = load_roles(home / "roles.toml")
            pipeline = load_manifest(home / "guardrails.toml", registry)
            cfg = load_home(home)
            self.assertEqual(cfg.mailbox.host, "imap.smallfirm.com")
            self.assertEqual(cfg.firm_domains, ("smallfirm.com",))
            self.assertEqual(pipeline.stages, ["ingest", "review"])

    def test_the_roles_are_the_words_the_firm_used(self):
        registry = None
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            write(home, self._answers())
            registry = load_roles(home / "roles.toml")
        self.assertEqual(registry.names(), ["attorney", "legal assistant"])
        # Seniority, without which the wizard would have written a trap.
        self.assertTrue(registry.can_release("attorney", "sender-scope",
                                             "legal assistant"))

    def test_the_wall_blocks_and_only_the_senior_role_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            write(home, self._answers())
            pipeline = load_manifest(home / "guardrails.toml",
                                     load_roles(home / "roles.toml"))
        wall = [b for b in pipeline.bindings if b.gate.id == "name-screen"]
        self.assertEqual(len(wall), 1)
        self.assertEqual(wall[0].on_fail, "block")
        self.assertEqual(wall[0].authority, "attorney")
        self.assertEqual(wall[0].options["terms"],
                         ["Acme Insurance", "Roberta Vance"])

    def test_no_wall_means_no_wall_gate_rather_than_an_empty_one(self):
        answers = list(FIRM)
        answers[7] = ""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            write(home, self._answers(answers))
            pipeline = load_manifest(home / "guardrails.toml",
                                     load_roles(home / "roles.toml"))
        self.assertEqual([b.gate.id for b in pipeline.bindings
                          if b.gate.id == "name-screen"], [])

    def test_the_gates_are_written_in_the_order_that_matters(self):
        # A firm reading its own manifest should meet the wall and the court
        # notices first, not the file-extension checker.
        text = guardrails_toml(self._answers())
        order = [line.split('"')[1] for line in text.splitlines()
                 if line.startswith("id = ")]
        self.assertEqual(order[:2], ["name-screen", "notice-parser"])

    def test_what_it_writes_is_commented_for_the_person_who_edits_it_next(self):
        answers = self._answers()
        self.assertIn("ethical wall", guardrails_toml(answers))
        self.assertIn("may_release lets seniority work", roles_toml(answers))
        self.assertIn("does not authenticate anyone", roles_toml(answers))

    def test_the_firms_own_domain_is_allowed_through_sender_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            write(home, self._answers())
            pipeline = load_manifest(home / "guardrails.toml",
                                     load_roles(home / "roles.toml"))
        scope = [b for b in pipeline.bindings if b.gate.id == "sender-scope"][0]
        self.assertIn("@smallfirm.com", scope.options["allow"])
        self.assertIn("@myflcourtaccess.com", scope.options["allow"])

    def test_a_home_that_would_not_load_is_never_written(self):
        # The failure mode this exists to prevent: answers that produce a
        # manifest naming a role the registry does not declare, discovered
        # after the files are on disk.
        bad = Answers(address="a@b.com", host="h", reviewer="", releaser="")
        with self.assertRaises(Exception):
            validate(bad)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with self.assertRaises(Exception):
                write(home, bad)
            self.assertFalse((home / "guardrails.toml").exists())

    def test_run_reports_what_it_wrote(self):
        ask, said = scripted(*FIRM)
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            run(home, ask)
        out = "\n".join(said)
        self.assertIn("guardrails.toml", out)
        self.assertIn("roles.toml", out)
        self.assertIn("DOCKETRY_IMAP_PASSWORD", out)
        self.assertIn("2 screened", out.replace("screened name(s)", "screened"))


if __name__ == "__main__":
    unittest.main()
