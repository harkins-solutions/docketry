"""Third-party gates: the extension point, exercised the way a stranger would.

The registry is process-wide, so every test here registers under its own id
and cleans up after itself.
"""
import contextlib
import tempfile
import textwrap
import unittest
from pathlib import Path

from docketry.core import gates
from docketry.core.gates import GateLoadError
from docketry.scaffold import gate_source


@contextlib.contextmanager
def clean_registry():
    """Whatever registers inside this block is forgotten afterwards."""
    before = set(gates._REGISTRY)
    try:
        yield
    finally:
        for gate_id in set(gates._REGISTRY) - before:
            gates._REGISTRY.pop(gate_id, None)
            gates._SOURCES.pop(gate_id, None)


def write_gate(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(textwrap.dedent(body))
    return path


A_GATE = '''
    from docketry.core.gates import register
    from docketry.core.pipeline import Finding, SEVERITY_FAIL


    @register
    class {cls}:
        """{doc}"""

        id = "{gate_id}"
        allowed_stages = None

        def check(self, envelope, options):
            if "{needle}" in envelope.subject:
                return [Finding(self.id, SEVERITY_FAIL, "found it")]
            return []
'''


class TestLoadingAGateFromTheHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_file_in_the_gates_directory_becomes_bindable(self):
        write_gate(self.home / "gates", "mine", A_GATE.format(
            cls="Mine", gate_id="tst-mine", doc="Mine.", needle="urgent"))
        with clean_registry():
            loaded = gates.load_home(self.home)
            self.assertEqual(loaded, ["tst-mine"])
            self.assertIn("tst-mine", gates.all_ids())
            self.assertTrue(gates.source_of("tst-mine").startswith("file:"))
            # And a manifest can now bind it, which is the actual point.
            from docketry.core.manifest import build_pipeline
            pipeline = build_pipeline({
                "pipeline": {"stages": ["ingest"]},
                "gate": [{"id": "tst-mine", "binds_to": ["ingest"]}],
            })
            self.assertEqual([b.gate.id for b in pipeline.bindings], ["tst-mine"])

    def test_a_home_with_no_gates_directory_is_not_an_error(self):
        with clean_registry():
            self.assertEqual(gates.load_home(self.home), [])

    def test_files_starting_with_underscore_are_helpers_not_gates(self):
        write_gate(self.home / "gates", "_shared", "X = 1\n")
        with clean_registry():
            self.assertEqual(gates.load_home(self.home), [])

    def test_a_broken_gate_file_fails_loudly_and_names_the_file(self):
        # The failure this prevents: a gate the operator believes is running,
        # skipped because it had a typo in it.
        write_gate(self.home / "gates", "broken", "this is not python\n")
        with clean_registry():
            with self.assertRaises(GateLoadError) as ctx:
                gates.load_home(self.home)
        self.assertIn("broken.py", str(ctx.exception))

    def test_a_file_that_registers_nothing_is_an_error_not_a_no_op(self):
        write_gate(self.home / "gates", "empty", "GATE = 'coming soon'\n")
        with clean_registry():
            with self.assertRaises(GateLoadError) as ctx:
                gates.load_home(self.home)
        self.assertIn("registered no gates", str(ctx.exception))
        self.assertIn("GATES.md", str(ctx.exception))


class TestTheRegistryRefusesTheDangerousThings(unittest.TestCase):
    def test_a_second_gate_cannot_take_an_id_that_is_taken(self):
        # Otherwise a home file could replace name-screen with a gate that
        # returns no findings, and the screen would stop with no error.
        with clean_registry():
            with self.assertRaises(GateLoadError) as ctx:
                @gates.register
                class Impostor:
                    id = "name-screen"

                    def check(self, envelope, options):
                        return []
        message = str(ctx.exception)
        self.assertIn("already registered", message)
        self.assertIn("built-in", message)          # names the current holder
        self.assertIn("Pick another id", message)

    def test_the_shipped_wall_is_still_the_shipped_wall(self):
        from docketry.core.gates.builtin import NameScreen
        self.assertIs(gates.get("name-screen"), NameScreen)

    def test_a_gate_with_no_id_says_what_is_missing(self):
        with clean_registry():
            with self.assertRaises(GateLoadError) as ctx:
                @gates.register
                class Nameless:
                    def check(self, envelope, options):
                        return []
        self.assertIn("has no id", str(ctx.exception))

    def test_a_gate_with_no_check_says_what_is_missing(self):
        with clean_registry():
            with self.assertRaises(GateLoadError) as ctx:
                @gates.register
                class Inert:
                    id = "tst-inert"
        self.assertIn("check(envelope, options)", str(ctx.exception))

    def test_ids_keep_one_shape_so_manifests_stay_readable(self):
        with clean_registry():
            for bad in ("Tst_Underscore", "tst upper", "-leading", "tst--double"):
                with self.assertRaises(GateLoadError, msg=bad):
                    @gates.register
                    class Odd:
                        id = bad

                        def check(self, envelope, options):
                            return []


class TestTheScaffoldIsRealCode(unittest.TestCase):
    def test_what_new_gate_writes_loads_and_holds_something(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "gates").mkdir()
            (home / "gates" / "tst_scaffold.py").write_text(
                gate_source("tst-scaffold"))
            with clean_registry():
                self.assertEqual(gates.load_home(home), ["tst-scaffold"])
                gate = gates.get("tst-scaffold")()
                from docketry.core.envelope import parse_message
                from email.message import EmailMessage

                def envelope(subject):
                    m = EmailMessage()
                    m["From"] = "a@b.com"
                    m["To"] = "c@d.com"
                    m["Subject"] = subject
                    m.set_content("")
                    return parse_message(bytes(m), source="t", fetched_at="now")

                held = gate.check(envelope("one two three four five six"), {})
                self.assertEqual([f.severity for f in held], ["fail"])
                self.assertEqual(gate.check(envelope("short"), {}), [])
                # And its optional validator refuses nonsense at load time.
                self.assertTrue(gate.validate_options({"max_words": "lots"}))
                self.assertEqual(gate.validate_options({"max_words": 3}), [])


class TestWhereGatesCameFrom(unittest.TestCase):
    def test_every_gate_says_which_route_it_arrived_by(self):
        sources = dict((g, s) for g, s, _, _ in gates.described())
        self.assertEqual(sources["name-screen"], "built-in")
        # The two that ship with Docketry but plug in from tools/ say so —
        # if that route were second-class, these would be the ones to notice.
        self.assertEqual(sources["notice-parser"], "built-in (tools)")
        self.assertEqual(sources["doc-classifier"], "built-in (tools)")

    def test_installed_packages_are_discovered_through_entry_points(self):
        from importlib.metadata import EntryPoint
        from unittest import mock

        class Vendored:
            id = "tst-vendored"
            allowed_stages = None

            def check(self, envelope, options):
                return []

        ep = mock.Mock(spec=EntryPoint)
        ep.name = "tst-vendored"
        ep.value = "vendor.gates:Vendored"
        ep.load.return_value = Vendored
        with clean_registry():
            with mock.patch("importlib.metadata.entry_points", return_value=[ep]):
                self.assertEqual(gates.load_installed(), ["tst-vendored"])
            self.assertEqual(gates.source_of("tst-vendored"), "package:tst-vendored")

    def test_a_broken_entry_point_names_itself(self):
        from importlib.metadata import EntryPoint
        from unittest import mock

        ep = mock.Mock(spec=EntryPoint)
        ep.name = "tst-broken"
        ep.value = "nope:Nope"
        ep.load.side_effect = ImportError("no module named nope")
        with clean_registry():
            with mock.patch("importlib.metadata.entry_points", return_value=[ep]):
                with self.assertRaises(GateLoadError) as ctx:
                    gates.load_installed()
        self.assertIn("tst-broken", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
