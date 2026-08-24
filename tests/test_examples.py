"""Every shipped example must actually load — docs that drift fail CI."""
import unittest
from pathlib import Path

from portico.lint import load_rulepack
from portico.manifest import load_manifest
from portico.notices import load_adapters_file

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
        self.assertEqual(len(skill_files), 3)
        for f in skill_files:
            text = f.read_text()
            self.assertTrue(text.startswith("---\n"), f"{f}: missing frontmatter")
            self.assertIn("name:", text)
            self.assertIn("description:", text)
            self.assertIn("portico ", text, f"{f}: a skill must call the real CLI")
