"""GATES.md, executed.

A tutorial that has drifted from the tool is worse than no tutorial: the
reader concludes the project is broken, and they are not wrong to. So the
five-minute path runs here, in the order the document gives it, and every
`docketry` command the document shows has to parse.
"""
import contextlib
import io
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from docketry import cli
from docketry.core import gates

GATES_MD = Path(__file__).resolve().parent.parent / "GATES.md"


def run_cli(*argv):
    out = io.StringIO()
    code, message = 0, ""
    with contextlib.redirect_stdout(out):
        try:
            cli.main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            message = "" if isinstance(e.code, int) else str(e.code)
    return code, out.getvalue() + message


@contextlib.contextmanager
def clean_registry():
    before = set(gates._REGISTRY)
    try:
        yield
    finally:
        for gate_id in set(gates._REGISTRY) - before:
            gates._REGISTRY.pop(gate_id, None)
            gates._SOURCES.pop(gate_id, None)


class TestTheFiveMinutePath(unittest.TestCase):
    """Steps 1-4 of GATES.md, run as written."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = str(Path(self.tmp.name) / "docketry-home")
        run_cli("--home", self.home, "init", "--host", "imap.x.com",
                "--user", "intake@firm.example")

    def tearDown(self):
        self.tmp.cleanup()

    def test_step_1_new_gate_writes_a_file_and_says_how_to_bind_it(self):
        with clean_registry():
            code, out = run_cli("--home", self.home, "new-gate", "long-subject")
        self.assertEqual(code, 0, out)
        path = Path(self.home, "gates", "long_subject.py")
        self.assertTrue(path.exists())
        self.assertIn("wrote", out)
        # It prints the binding block, so step 4 is a paste, not a lookup.
        self.assertIn('id = "long-subject"', out)
        self.assertIn("try-gate long-subject", out)

    def test_step_2_try_gate_holds_the_long_subject_and_passes_the_short_one(self):
        with clean_registry():
            run_cli("--home", self.home, "new-gate", "long-subject")
            code, out = run_cli(
                "--home", self.home, "try-gate", "long-subject",
                "--subject", "this subject is quite a lot longer than five words")
            self.assertEqual(code, 0, out)
            self.assertIn("[fail]", out)
            self.assertIn("file:gates/long_subject.py", out)

            code, out = run_cli("--home", self.home, "try-gate", "long-subject",
                                "--subject", "short one")
            self.assertEqual(code, 0, out)
            self.assertIn("no findings", out)

    def test_step_3_the_edit_the_tutorial_suggests_works(self):
        # The zip-screen body from GATES.md, dropped into the scaffold.
        with clean_registry():
            run_cli("--home", self.home, "new-gate", "long-subject")
            path = Path(self.home, "gates", "long_subject.py")
            source = path.read_text()
            head = source[: source.index("        max_words = int(")]
            path.write_text(head + '''        held = []
        for attachment in envelope.attachments:
            if attachment.filename.lower().endswith(".zip"):
                held.append(Finding(
                    self.id,
                    SEVERITY_FAIL,
                    f"{attachment.filename} is a zip archive — this pipeline"
                    " takes documents, not archives",
                ))
        return held
''')
            code, out = run_cli("--home", self.home, "try-gate", "long-subject",
                                "--attach", "discovery.zip")
        self.assertEqual(code, 0, out)
        self.assertIn("[fail]", out)
        self.assertIn("discovery.zip is a zip archive", out)

    def test_step_4_binding_it_makes_it_run_on_a_real_message(self):
        with clean_registry():
            run_cli("--home", self.home, "new-gate", "long-subject")
            manifest = Path(self.home, "guardrails.toml")
            manifest.write_text(manifest.read_text() + '''
[[gate]]
id = "long-subject"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"
''')
            # `gates` lists it as the tutorial says it will.
            code, out = run_cli("--home", self.home, "gates")
            self.assertEqual(code, 0, out)
            self.assertIn("long-subject", out)
            self.assertIn("file:gates/long_subject.py", out)

            # And the pipeline actually holds a message on it, which is the
            # claim the whole page is making.
            from email.message import EmailMessage

            from docketry.core.config import load_home
            from docketry.core.envelope import parse_message
            from docketry.core.manifest import load_manifest
            from docketry.core.pipeline import Runner
            from docketry.core.store import Store, utcnow

            m = EmailMessage()
            m["From"] = "clerk@myflcourtaccess.com"
            m["To"] = "intake@firm.example"
            m["Subject"] = "this subject is quite a lot longer than five words"
            m.set_content("body")
            cfg = load_home(self.home)
            pipeline = load_manifest(cfg.manifest_path)
            store = Store(cfg.store_path)
            try:
                msg_id = store.ingest(
                    parse_message(bytes(m), source="t", fetched_at=utcnow()),
                    first_stage="ingest")
                self.assertEqual(Runner(pipeline, store).enter(msg_id),
                                 "pending_review")
                held_by = {f["gate_id"] for f in store.findings_for(msg_id)
                           if f["severity"] == "fail"}
                self.assertIn("long-subject", held_by)
            finally:
                store.close()

    def test_a_gate_that_does_not_exist_points_at_the_way_to_make_one(self):
        code, out = run_cli("--home", self.home, "try-gate", "no-such-gate")
        self.assertNotEqual(code, 0)
        self.assertIn("unknown gate", out)
        self.assertIn("new-gate", out)


class TestTheDocumentMatchesTheTool(unittest.TestCase):
    """Every `docketry ...` line in GATES.md has to be a real command."""

    def _documented_commands(self):
        text = GATES_MD.read_text()
        for line in re.findall(r"^\$ (docketry .+)$", text, re.M):
            # Continuations and inline comments are the document's business;
            # the command and its flags are ours.
            yield line.rstrip("\\").strip()

    def test_there_are_commands_to_check(self):
        self.assertGreaterEqual(len(list(self._documented_commands())), 5)

    def test_every_documented_command_parses(self):
        parser = cli.build_parser()
        for command in self._documented_commands():
            argv = shlex.split(command, comments=True)[1:]
            with self.subTest(command=command):
                try:
                    parser.parse_args(argv)
                except SystemExit:
                    self.fail(f"GATES.md shows `{command}`, which the CLI"
                              " does not accept")

    def test_the_reference_table_lists_the_sources_the_code_can_produce(self):
        text = GATES_MD.read_text()
        for label in ("built-in", "built-in (tools)", "file:", "package:"):
            self.assertIn(label, text)

    def test_the_architecture_page_is_linked_and_diagrammed(self):
        arch = GATES_MD.parent / "ARCHITECTURE.md"
        self.assertTrue(arch.exists())
        body = arch.read_text()
        # Four diagrams, and they have to be the renderable kind.
        self.assertEqual(body.count("```mermaid"), 4)
        self.assertIn("GATES.md", body)


if __name__ == "__main__":
    unittest.main()
