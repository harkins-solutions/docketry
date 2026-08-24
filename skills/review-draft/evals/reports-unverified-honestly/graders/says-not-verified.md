---
type: regex
pattern: "not verified|NOT VERIFIED|unverified"
flags: "i"
match: contains
target: last_message
---

With no CourtListener token the tool runs extraction-only; the agent's
answer must state plainly that citations were found but NOT verified.
