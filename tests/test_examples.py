"""Every shipped example must actually load — docs that drift fail CI."""
import unittest
from pathlib import Path

from docketry.lint import load_rulepack
from docketry.manifest import load_manifest
from docketry.notices import load_adapters_file

EXAMPLES = Path("examples")
SKILLS = Path("skills")


class TestExamples(unittest.TestCase):
    def test_all_example_manifests_load(self):
        manifests = sorted(EXAMPLES.glob("guardrails*.toml"))
        self.assertGreaterEqual(len(manifests), 3)
        for m in manifests:
            pipeline = load_manifest(m)
            self.assertTrue(pipeline.stages, m.name)

    def test_example_adapters_load(self):
        self.assertTrue(load_adapters_file(EXAMPLES / "adapters.toml"))

    def test_example_rulepack_loads(self):
        self.assertTrue(load_rulepack(EXAMPLES / "lint-rules.toml"))


class TestSkills(unittest.TestCase):
    def test_eval_suites_are_well_formed(self):
        prompts = sorted(SKILLS.glob("*/evals/*/prompt.md"))
        self.assertGreaterEqual(len(prompts), 4)
        for prompt in prompts:
            text = prompt.read_text()
            self.assertTrue(text.startswith("---\n"), f"{prompt}: missing frontmatter")
            self.assertIn("plugins:", text, f"{prompt}: plain skills need plugins: [\"../..\"]")
            graders = sorted((prompt.parent / "graders").glob("*.md"))
            self.assertTrue(graders, f"{prompt.parent}: no graders")
            types = []
            for g in graders:
                gt = g.read_text()
                self.assertIn("type:", gt, f"{g}: grader missing type")
                types.append(gt)
            joined = "\n".join(types)
            self.assertIn("type: tool_used", joined,
                          f"{prompt.parent}: every case must assert real tool use")

    def test_skills_have_frontmatter_and_no_prompt_only_paths(self):
        skill_files = sorted(SKILLS.glob("*/SKILL.md"))
        # Named rather than counted: adding a skill should be a deliberate
        # line in this test, while a stray directory still fails it.
        self.assertEqual(
            {f.parent.name for f in skill_files},
            {"classify-document", "intake-triage", "review-draft",
             "redact-document", "build-timeline", "reconcile-docket",
             "manage-matter", "pipeline-health", "assign-contacts"},
        )
        for f in skill_files:
            text = f.read_text()
            self.assertTrue(text.startswith("---\n"), f"{f}: missing frontmatter")
            self.assertIn("name:", text)
            self.assertIn("description:", text)
            self.assertIn("docketry ", text, f"{f}: a skill must call the real CLI")


class TestNothingShipsWithoutEvals(unittest.TestCase):
    """A skill with no eval suite is an untested tool with a nice description.

    The suite-shape test above validates the eval cases it FINDS, so a skill
    that ships with none passes it silently — the glob simply returns fewer
    results. This closes that, because "remember to add evals" is not a
    control.
    """

    def test_every_skill_has_at_least_one_eval_case(self):
        for skill in sorted(SKILLS.glob("*/SKILL.md")):
            cases = sorted((skill.parent / "evals").glob("*/prompt.md"))
            self.assertTrue(
                cases,
                f"{skill.parent.name} ships with no evals — a tool an agent can"
                " drive needs at least one case asserting it uses the tool"
                " rather than answering from its own knowledge")

    def test_every_eval_case_asserts_a_hard_rule_or_real_tool_use(self):
        for prompt in sorted(SKILLS.glob("*/evals/*/prompt.md")):
            graders = list((prompt.parent / "graders").glob("*.md"))
            kinds = "\n".join(g.read_text() for g in graders)
            self.assertIn("type: tool_used", kinds,
                          f"{prompt.parent.name}: no grader checks what the"
                          " agent actually ran")


class TestVersionDoesNotDrift(unittest.TestCase):
    """One version, two files. They have to agree or a release lies.

    pyproject drives what PyPI serves; __version__ drives what `--version`
    prints. When they diverge, the number a user reports in an issue is not
    the number that was published, and neither of you can tell.
    """

    def test_pyproject_and_dunder_version_match(self):
        import re
        import tomllib
        root = SKILLS.parent
        declared = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        src = (root / "docketry" / "__init__.py").read_text()
        dunder = re.search(r'__version__ = "([^"]+)"', src).group(1)
        self.assertEqual(declared, dunder)

    def test_every_shipped_feature_version_exists_by_the_current_release(self):
        """FEATURES.md labels features with the version that carries them.

        A feature marked SHIPPED v0.14 when the package says 0.1.0 means the
        docs describe software nobody can install.
        """
        import re
        import tomllib
        root = SKILLS.parent
        current = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        cur = tuple(int(x) for x in current.split("."))
        claimed = set(re.findall(r"SHIPPED v(\d+\.\d+)", (root / "FEATURES.md").read_text()))
        for v in sorted(claimed):
            parts = tuple(int(x) for x in v.split("."))
            self.assertLessEqual(
                parts, cur[:len(parts)],
                f"FEATURES.md claims v{v} shipped but the package is {current}")
