---
name: assign-contacts
description: Look up who an email address belongs to, and help the firm record it. Invoke for "who is this from", "is this the client or the other side", "add them to contacts", or when mail needs sorting by who wrote it.
---

# Who an address belongs to

```
docketry contacts
```

Lists every contact the firm has recorded: their **kind** (what they are to the
firm) and, for staff, the **roles** they may release.

Kinds are `staff`, `client`, `opposing_counsel`, `court`, `expert`, `vendor`
and `other`. An entry can name one address or claim a whole domain with a
leading `@`, which is how a firm says "everyone there is the other side"
without listing them.

## Why kind matters more than it looks

Kind decides where someone's mail lands. **Client correspondence is privileged
and is kept in its own timeline layer**, apart from correspondence with the
other side. So `kind = "client"` is not a label; it is the thing that keeps
privileged mail out of a list somebody hands over.

Without a contacts file, everything falls to correspondence. That is the safe
direction and the tool does it deliberately — but it means client mail is
sitting alongside the other side's until someone records who the client is.

## Hard rules

- NEVER decide someone's kind yourself. Who the client is, and who counts as
  opposing counsel, is a question for the firm — and getting it wrong in the
  `client` direction misfiles privileged mail, while getting it wrong the other
  way puts it in a list that leaves the building.
- NEVER add, edit, or remove a contact without being told to, and without the
  human saying which kind. Read the file, report what it says, and ask.
- NEVER infer who someone is from the record — that a person emailed about a
  case does not make them the client, and a familiar-looking domain is not a
  fact.
- Only `staff` contacts hold roles, and a role must be one declared in
  `roles.toml`. Do not work around a refusal by changing someone's kind to
  staff.
