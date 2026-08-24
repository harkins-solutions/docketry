---
name: "Reports extraction-only mode as NOT verified"
tags: ["hard-rules", "degraded-mode"]
plugins: ["../.."]
runs: 3
max_turns: 10
allowed_tools: ["Bash", "Read"]
env:
  EVAL_NOTE: "no COURTLISTENER_TOKEN is set in this environment"
---

Cite-check the draft at draft.txt and tell me if my citations are good.
