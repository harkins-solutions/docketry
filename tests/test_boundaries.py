"""The tree has to say what the README says.

The claim is that Docketry is a base layer other small tools plug into. A
reviewer who opens the package and finds the port importing the family has
been told something untrue, and no amount of prose fixes that. So the
direction of dependency is a test:

    core  ->  nothing above it
    tools ->  core
    gates ->  either, because a gate is the plug

If a future change needs the port to reach for a tool, this fails, and the
right answer is almost always to move the thing being reached for.
"""
import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "docketry"
APP = {"cli", "webui", "wizard"}


def imported_names(path: Path) -> set[str]:
    """Every docketry module this file imports, relative or absolute.

    Relative imports are resolved against the file's own package, so
    `from ..core.store import Store` inside tools/ reads as `core.store`.
    """
    tree = ast.parse(path.read_text())
    package = path.relative_to(PKG).parent.parts     # e.g. ("tools",)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package[: len(package) - (node.level - 1)])
                parts = base + ((node.module or "").split(".") if node.module else [])
            elif (node.module or "").startswith("docketry"):
                parts = node.module.split(".")[1:]
            else:
                continue
            found.add(".".join(p for p in parts if p))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("docketry."):
                    found.add(alias.name.split(".", 1)[1])
    return found


def modules_under(*parts) -> list[Path]:
    root = PKG.joinpath(*parts)
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


class TestTheDependencyDirection(unittest.TestCase):
    def test_the_port_imports_nothing_above_it(self):
        offenders = []
        for path in modules_under("core"):
            for name in imported_names(path):
                head = name.split(".")[0]
                if head == "tools" or head in APP:
                    offenders.append(f"{path.relative_to(PKG)} imports {name}")
        self.assertEqual(offenders, [], "the port must not depend on the family")

    def test_the_family_does_not_depend_on_the_app(self):
        # A tool is usable from a script, a gate, or another program. Reaching
        # for the CLI or the review UI would make that untrue.
        offenders = []
        for path in modules_under("tools"):
            for name in imported_names(path):
                if name.split(".")[0] in APP:
                    offenders.append(f"{path.relative_to(PKG)} imports {name}")
        self.assertEqual(offenders, [])

    def test_the_port_stays_small_enough_to_read(self):
        # Not a style rule: "small enough to audit in an afternoon" is the
        # credibility claim, and claims that nothing measures drift.
        core = [p for p in modules_under("core") if p.name != "__init__.py"]
        lines = sum(len(p.read_text().splitlines()) for p in core)
        self.assertLessEqual(len(core), 12, [p.name for p in core])
        self.assertLess(lines, 2500, f"the port is now {lines} lines")


class TestGatesAreThePlug(unittest.TestCase):
    def test_the_tool_backed_gates_register_the_same_way_a_plugin_would(self):
        from docketry.core.gates import all_ids, get

        # Registered, though nothing in core/ mentions them.
        for gate_id in ("notice-parser", "doc-classifier"):
            self.assertIn(gate_id, all_ids())
            self.assertTrue(get(gate_id))
        core_text = "\n".join(p.read_text() for p in modules_under("core"))
        self.assertNotIn("gates_notice", core_text)
        self.assertNotIn("gates_classifier", core_text)

    def test_the_port_ships_the_hygiene_gates_itself(self):
        from docketry.core.gates import all_ids

        for gate_id in ("attachment-policy", "sender-scope", "name-screen",
                        "provenance-stamp"):
            self.assertIn(gate_id, all_ids())


if __name__ == "__main__":
    unittest.main()
