---
name: intake-triage
description: Work a Docketry intake queue — sweep the intake mailbox, explain what the gates held and why, and prepare (never send) the approval commands. Invoke for "check the intake queue", "why is this message held", "sweep intake".
---

# Intake triage

## Steps

1. Sweep and inspect:

   ```
   docketry poll
   docketry queue
   docketry notices
   ```

2. For each held message, explain in plain language which gate held it and
   what the finding means (the queue output names the gate and its message).
   A `notice-parser` hold means a court system's email format may have
   changed — flag that prominently; it can mean service notices are being
   missed.

3. For holds the human decides to release, give them the exact command to
   run — filled in except for the approver:

   ```
   docketry approve <id> --gate <gate> --by "<their name>" --role <role>
   ```

## Hard rules

- You prepare approval commands; the HUMAN runs them (or explicitly tells
  you to run one with their name). An approval is a recorded authority
  decision — it must trace to a person.
- Never bypass a hold by any other means. If a gate seems miscalibrated,
  propose a guardrails.toml change and show the human the diff instead.
- Read-only otherwise: triage never deletes, moves, or edits messages.
