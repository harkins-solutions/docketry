import unittest

from docketry.core.manifest import ManifestError, build_pipeline, DEFAULT_MANIFEST
import tomllib


class TestManifest(unittest.TestCase):
    def test_default_manifest_loads(self):
        p = build_pipeline(tomllib.loads(DEFAULT_MANIFEST))
        self.assertEqual(p.stages, ["ingest", "review"])
        self.assertEqual([b.gate.id for b in p.bindings],
                         ["notice-parser", "attachment-policy",
                          "provenance-stamp"])

    def test_the_starter_manifest_offers_the_wall_without_inventing_terms(self):
        # A name-screen with no terms would refuse to load, so the starter
        # ships it commented — but it ships it, rather than leaving the one
        # gate a firm would actually want out of the file entirely.
        self.assertIn('# id = "name-screen"', DEFAULT_MANIFEST)
        self.assertIn("ethical wall", DEFAULT_MANIFEST)

    def test_unknown_gate_refused(self):
        with self.assertRaises(ManifestError):
            build_pipeline({"pipeline": {"stages": ["ingest"]},
                            "gate": [{"id": "no-such-gate", "binds_to": ["ingest"]}]})

    def test_unknown_stage_refused(self):
        with self.assertRaises(ManifestError):
            build_pipeline({"pipeline": {"stages": ["ingest"]},
                            "gate": [{"id": "attachment-policy", "binds_to": ["send"]}]})

    def test_out_of_scope_binding_refused(self):
        # attachment-policy declares allowed_stages={"ingest"}; binding it to
        # review must refuse to load.
        with self.assertRaises(ManifestError) as ctx:
            build_pipeline({"pipeline": {"stages": ["ingest", "review"]},
                            "gate": [{"id": "attachment-policy", "binds_to": ["review"]}]})
        self.assertIn("not meant for", str(ctx.exception))

    def test_bad_on_fail_refused(self):
        with self.assertRaises(ManifestError):
            build_pipeline({"pipeline": {"stages": ["ingest"]},
                            "gate": [{"id": "attachment-policy", "binds_to": ["ingest"],
                                      "on_fail": "ignore"}]})

    def test_empty_stages_refused(self):
        with self.assertRaises(ManifestError):
            build_pipeline({"pipeline": {"stages": []}})


if __name__ == "__main__":
    unittest.main()
