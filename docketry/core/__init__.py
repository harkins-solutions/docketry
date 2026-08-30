"""The port. Everything a message has to pass through, and nothing else.

An envelope, a pipeline of gates, a store, the manifest that declares them, and
the role registry that says who may release what. This is the base layer the
README describes: small enough to read in an afternoon, and dependent on
nothing above it.

That last part is enforced, not asserted. No module in this package imports
from `docketry.tools`, the CLI or the review UI — `tests/test_boundaries.py`
fails the build if one starts to. A tool plugs into the port; the port never
reaches back for a tool.
"""
