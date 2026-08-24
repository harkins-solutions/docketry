---
name: classify-document
description: Type a legal document (motion, order, notice, discovery, etc.) with Portico's deterministic classifier, or work the staged classification queue. Invoke for "what is this document", "classify these files", or "work the doc-type queue".
---

# Classify documents

Classification is deterministic and free — run the command, don't guess
from the filename yourself.

## Single files

```
portico classify <file>
```

Output is `label (tier)`. Tier meanings: `high` = title anchor, `medium` =
body anchor, `low` = fallback (correspondence). Report the label and tier;
if the user disagrees with a `low` or `medium` result, that's expected —
the deterministic tiers are conservative by design.

## The staged queue (a Portico home directory)

```
portico class-queue
portico class-apply <id> --by "<person>" --role <role>
```

## Hard rules

- NEVER run `class-apply` unless the human told you to apply and named who
  is approving. The `--by` value is the human's name, not yours — the
  approval record must reflect who actually decided.
- `class-apply` is fill-only: it never overwrites an existing doc type
  (it reports `kept-existing`). Don't try to work around that; if a type
  looks wrong, surface it to the human instead.
