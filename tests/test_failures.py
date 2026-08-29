"""Bad input gets a sentence, never a stack trace.

A traceback tells the person nothing they can act on and makes a tool look
broken when it is only being told something wrong. This is enforced rather
than reviewed, because it regresses the moment a new command forgets.

Genuinely unexpected errors are NOT covered here and should still raise: an
exception nobody predicted is a bug in Docketry, and dressing it up as a tidy
message is how bugs go unreported.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from docketry.cli import main

EXAMPLES = Path("examples")


def _home() -> str:
    """An initialised home with a workflow and a role registry."""
    home = Path(tempfile.mkdtemp()) / "h"
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--home", str(home), "init", "--host", "imap.example.com",
              "--user", "intake@example.com"])
    (home / "workflows").mkdir()
    (home / "workflows" / "generic.toml").write_text(
        (EXAMPLES / "workflow-generic.toml").read_text())
    (home / "roles.toml").write_text((EXAMPLES / "roles.toml").read_text())
    return str(home)


BAD_INPUT = [
    ["advance", "999"],
    ["approve", "999", "--by", "X", "--role", "attorney"],
    ["class-apply", "999", "--by", "X", "--role", "attorney"],
    ["lint", "/nonexistent.docx"],
    ["verify-draft", "/nonexistent.txt", "--offline"],
    ["redact-scan", "/nonexistent.pdf", "--term", "QUAGGA"],
    ["redact-apply", "/nonexistent.pdf", "/tmp/o.pdf", "--term", "QUAGGA"],
    ["redact-verify", "/nonexistent.pdf", "--term", "QUAGGA"],
    ["matter-status", "99-XX-1"],
    ["matter-advance", "99-XX-1", "nowhere", "--by", "X"],
    ["docket-reconcile", "99-XX-1", "/nonexistent.csv"],
    ["workflow-check", "/nonexistent.toml"],
    ["matter-open", "26-CA-1", "--type", "no-such-type"],
]


class TestBadInputIsExplained(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = _home()

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                main(["--home", self.home] + argv)
        return ctx.exception, out.getvalue() + err.getvalue()

    def test_every_bad_input_exits_with_a_message_not_a_trace(self):
        for argv in BAD_INPUT:
            with self.subTest(command=" ".join(argv)):
                exc, text = self._run(argv)
                self.assertNotIn("Traceback", text)
                # SystemExit carries the message, or it was printed first.
                message = str(exc.code) if isinstance(exc.code, str) else text
                self.assertTrue(message.strip(),
                                f"{argv[0]} failed silently")
                self.assertNotIn("Traceback", message)

    def test_classifying_a_name_with_no_file_is_allowed_but_labelled(self):
        """Naming a document you were told about is a real way people use
        this. It must work, and it must say the text was never read."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--home", self.home, "classify",
                  "Order Granting Motion to Compel.pdf"])
        text = out.getvalue()
        self.assertIn("order", text)
        self.assertIn("FILENAME only", text)

    def test_a_term_too_short_to_check_is_refused_not_passed(self):
        """The failure that matters most: a clean bill from a check that
        never ran. Every term here is below the token floor, so there was
        nothing to look for."""
        exc, _ = self._run(["redact-verify", __file__, "--term", "x"])
        self.assertIn("nothing to check", str(exc.code))

    def test_a_malformed_toml_says_where(self):
        bad = Path(tempfile.mkdtemp()) / "w.toml"
        bad.write_text("this is not = valid toml [[[\n")
        exc, _ = self._run(["workflow-check", str(bad)])
        self.assertIn("not valid TOML", str(exc.code))

    def test_a_directory_given_where_a_file_belongs(self):
        exc, _ = self._run(["workflow-check", tempfile.mkdtemp()])
        self.assertIn("directory", str(exc.code))

    def test_an_undeclared_role_names_the_declared_ones(self):
        wf = Path(self.home) / "workflows" / "bad.toml"
        wf.write_text((EXAMPLES / "workflow-generic.toml").read_text()
                      .replace('authority = "attorney"', 'authority = "wizard"'))
        exc, _ = self._run(["matter-open", "26-CA-2", "--type", "bad"])
        self.assertIn("wizard", str(exc.code))
        self.assertIn("attorney, paralegal", str(exc.code))


if __name__ == "__main__":
    unittest.main()


class TestStartsFast(unittest.TestCase):
    """The CLI must not import the heavy optional dependencies to start.

    Every command pays module import before it does anything, and a stray
    top-level `import eyecite` is invisible in review and very visible to
    whoever runs `docketry queue` fifty times a day.
    """

    HEAVY = {"pypdf", "eyecite", "openpyxl", "docx", "httpx", "PIL",
             "pytesseract", "reportlab"}

    def test_importing_the_cli_pulls_in_no_heavy_dependency(self):
        import subprocess
        import sys
        code = (
            "import sys;"
            "before=set(sys.modules);"
            "import docketry.cli;"
            "heavy=" + repr(sorted(self.HEAVY)) + ";"
            "print(','.join(sorted({m.split('.')[0] for m in set(sys.modules)-before}"
            " & set(heavy))))"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, check=True).stdout.strip()
        self.assertEqual(out, "", f"CLI import pulled in: {out}")
