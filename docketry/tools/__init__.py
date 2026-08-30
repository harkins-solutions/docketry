"""The composable family: each one removable without touching the port.

Document classification, e-service notice parsing, citation extraction and
verification, redaction, timelines, exports, docket reconciliation, draft
linting, matter workflow, contacts, pipeline reporting, and an optional local
model. They import from `docketry.core`; the reverse never happens.

Two of them plug in as gates — `gates_notice` and `gates_classifier` register
themselves with the port's gate registry when the package is imported, by the
same route a third-party gate would take. That arrangement is the whole claim
the README makes about this being a base layer, so it is worth keeping true.
"""
