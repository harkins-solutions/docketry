---
name: manage-matter
description: Track a matter through the firm's own workflow stages and report what each one is waiting on. Invoke for "where is this case", "what's holding up X", "move this to discovery", or "what do we need before we can close this".
---

# Move a matter through its stages

The stages, and what must be true before a matter can leave one, are the
firm's workflow — not yours and not Docketry's. Read them; never assume the
usual ones.

```
docketry matters
docketry matter-status <case-number>
docketry matter-open <case-number> --type <matter-type> --name "<how they refer to it>"
```

`matter-status` is the one to reach for first. It says where the matter is and,
for every way out of that stage, either that it can move now or exactly what it
is still waiting on.

## Moving one

```
docketry matter-advance <case> <stage> --by "<person>" --role <role>
```

`--by` is the name of the **human** who decided, never yours. An unattributed
move is refused outright, which is the point: a matter's stage is a claim about
where the work stands, and someone has to own it.

## When it will not move

The command prints what the record is missing, in plain words. Pass that
through. Two things it might be waiting for:

- **A fact that is not in the record yet** — a document that has not arrived,
  a field nobody filled in. The answer is to get the thing, not to change the
  rule.
- **A person** — a role has to release it. Say which role, and stop.

## Hard rules

- NEVER run `matter-advance` unless a human told you to move it and named who
  is approving. Their name goes in `--by`.
- NEVER edit a file in `workflows/` to make a blocked matter pass. A gate that
  is in the way is doing its job; if the firm genuinely wants it changed, that
  is a decision they make deliberately, not a workaround you apply.
- NEVER invent a stage name. Read `matter-status`, which lists the real ones.
- NEVER describe a matter that has not moved as neglected. Standing still is
  visible on purpose; that is not the same as being a failure.
