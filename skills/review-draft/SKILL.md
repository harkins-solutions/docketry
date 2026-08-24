---
name: review-draft
description: Review a litigation draft with Portico's citation verifier and brief linter. Invoke when the user asks to check, review, cite-check, or lint a draft, motion, brief, or response — takes a .docx, .pdf, or .txt file.
---

# Review a draft

Run the checks through Portico's CLI — the same validated commands the
pipeline gates use. Never "check" a draft by reading it and reasoning about
citations in your head; the whole point of these tools is that verification
is a command, not an opinion.

## Steps

1. Run both commands on the file the user named:

   ```
   portico verify-draft <file>
   portico lint <file> [--rules <their rulepack, if the project has one>]
   ```

2. Report what the tools found, grouped: citation failures first (these are
   the sanctionable class), then lint errors, then warnings. Quote each
   finding's message; add your own explanation of WHY it matters only where
   the message isn't self-explanatory.

3. Suggest concrete fixes for each finding. The human decides; never edit
   the draft unless they ask.

## Hard rules

- If `verify-draft` exits in extraction-only mode (no COURTLISTENER_TOKEN or
  no network), say plainly that citations were FOUND but NOT VERIFIED, tell
  the user to set `COURTLISTENER_TOKEN` (free CourtListener account) for
  real verification, and do not soften this. Never present unverified
  citations as checked, and never "verify" a citation from your own
  knowledge — your training data is not a citator and this is exactly the
  failure mode the tool exists to catch.
- Never claim a citation is good law, binding, or current. The tools check
  that citations match the documents they point to — nothing more, and
  neither do you.
- A clean run means "no findings", not "the draft is correct". Say so.
