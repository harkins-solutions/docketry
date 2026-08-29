"""The role registry: catches mistakes at load, and lets seniority work."""
import tempfile
import unittest
from pathlib import Path

from docketry.roles import RoleError, load_if_present, load_roles

EXAMPLE = Path("examples/roles.toml")


def _roles(body: str) -> Path:
    p = Path(tempfile.mkdtemp()) / "roles.toml"
    p.write_text(body)
    return p


class TestLoad(unittest.TestCase):
    def test_the_shipped_example_loads_and_lists_no_real_people(self):
        reg = load_roles(EXAMPLE)
        self.assertEqual(reg.names(), ["attorney", "paralegal"])
        # Roles are job titles, which are generic. PEOPLE are not, so the
        # shipped file must never arrive with anyone in it.
        self.assertEqual(reg.people, {})

    def test_an_empty_file_is_refused_rather_than_silently_permissive(self):
        with self.assertRaises(RoleError) as ctx:
            load_roles(_roles("# nothing here\n"))
        self.assertIn("delete it or fill it in", str(ctx.exception))

    def test_a_duplicate_role_is_refused(self):
        with self.assertRaises(RoleError):
            load_roles(_roles('[[role]]\nname="a"\n[[role]]\nname="a"\n'))

    def test_a_person_holding_an_undeclared_role_is_refused(self):
        with self.assertRaises(RoleError) as ctx:
            load_roles(_roles('[[role]]\nname="staff"\n'
                              '[[person]]\nname="Dana"\nroles=["wizard"]\n'))
        self.assertIn("not a declared role", str(ctx.exception))

    def test_absent_file_means_no_registry_not_an_error(self):
        # Every installation that predates roles.toml keeps working.
        self.assertIsNone(load_if_present(tempfile.mkdtemp()))


class TestAuthorityChecks(unittest.TestCase):
    def setUp(self):
        self.reg = load_roles(EXAMPLE)

    def test_an_undeclared_authority_is_named_with_the_alternatives(self):
        with self.assertRaises(RoleError) as ctx:
            self.reg.check_authority("gate 'x'", "attorny")
        msg = str(ctx.exception)
        self.assertIn("attorny", msg)
        self.assertIn("attorney, paralegal", msg)

    def test_no_authority_at_all_is_fine(self):
        self.reg.check_authority("transition a -> b", "")

    def test_seniority_releases_a_junior_hold(self):
        # The defect this fixes: comparing strings meant a supervisor could
        # not clear a hold marked for staff.
        self.assertTrue(self.reg.can_release("attorney", "notice-parser", "paralegal"))

    def test_a_junior_role_cannot_reach_past_its_list(self):
        self.assertFalse(self.reg.can_release("paralegal", "cite-gate", "attorney"))

    def test_holding_the_named_role_is_always_enough(self):
        self.assertTrue(self.reg.can_release("paralegal", "anything", "paralegal"))

    def test_an_unlisted_person_may_still_claim_a_role(self):
        # A firm should not have to enumerate its staff before approving.
        self.assertTrue(self.reg.person_may_claim("Someone New", "paralegal"))

    def test_a_listed_person_is_held_to_their_roles(self):
        reg = load_roles(_roles('[[role]]\nname="staff"\n[[role]]\nname="boss"\n'
                                '[[person]]\nname="Dana Reyes"\nroles=["staff"]\n'))
        self.assertTrue(reg.person_may_claim("dana reyes", "staff"))
        self.assertFalse(reg.person_may_claim("Dana Reyes", "boss"))


class TestShippedExamplesAgree(unittest.TestCase):
    """Copying the shipped roles file in must not break the shipped manifests."""

    def test_every_example_manifest_loads_against_the_example_roles(self):
        from docketry.manifest import load_manifest
        reg = load_roles(EXAMPLE)
        for m in sorted(Path("examples").glob("guardrails*.toml")):
            load_manifest(m, reg)      # raises if a role is undeclared

    def test_the_built_in_default_manifest_loads_too(self):
        import tempfile as tf
        from docketry.manifest import DEFAULT_MANIFEST, load_manifest
        p = Path(tf.mkdtemp()) / "guardrails.toml"
        p.write_text(DEFAULT_MANIFEST)
        load_manifest(p, load_roles(EXAMPLE))


class TestConfigValidation(unittest.TestCase):
    def test_a_gate_naming_an_undeclared_role_is_refused_at_load(self):
        from docketry.manifest import ManifestError, build_pipeline
        reg = load_roles(EXAMPLE)
        data = {"pipeline": {"stages": ["ingest"]},
                "gate": [{"id": "provenance-stamp", "binds_to": ["ingest"],
                          "authority": "wizard"}]}
        with self.assertRaises((RoleError, ManifestError)) as ctx:
            build_pipeline(data, reg)
        self.assertIn("wizard", str(ctx.exception))

    def test_a_workflow_naming_an_undeclared_role_is_refused_at_load(self):
        from docketry.workflow import load_workflow
        reg = load_roles(EXAMPLE)
        wf = _roles('[workflow]\nmatter_type="x"\nstages=["a","b"]\n'
                    '[[transition]]\nfrom="a"\nto="b"\nauthority="wizard"\n')
        with self.assertRaises(RoleError):
            load_workflow(wf, reg)

    def test_without_a_registry_nothing_is_validated(self):
        from docketry.workflow import load_workflow
        wf = _roles('[workflow]\nmatter_type="x"\nstages=["a","b"]\n'
                    '[[transition]]\nfrom="a"\nto="b"\nauthority="wizard"\n')
        self.assertEqual(load_workflow(wf).transitions[0].authority, "wizard")


if __name__ == "__main__":
    unittest.main()
