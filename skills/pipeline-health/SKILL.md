---
name: pipeline-health
description: Report on how the intake pipeline is running — where mail comes from, which checks are slow, and which configuration has quietly stopped doing anything. Invoke for "how's the queue doing", "what's holding things up", "are we missing anything", or any question about volume or throughput.
---

# Report on the pipeline

```
docketry report
docketry report --days 90
```

It answers two questions: is the machine working, and is the configuration
still right.

## Read the alerts first, not the volume

The counts are context. The findings are the reason to run it:

- **A gate configured months ago that has never fired.** It is not protecting
  anything, and nobody was going to notice. Worth asking whether it still
  matches how the firm works.
- **An adapter that matched notices last month and none since.** That source
  has almost certainly changed its template, and its mail is now landing
  unparsed. This is urgent in a way the number of messages never is.
- **Documents named in a notice with a link and no copy.** Things the firm was
  told about and cannot open.
- **Matters that have not moved.** Visibility, not a verdict.

## Announcements are not conversations

Volume is split into correspondence and notifications. E-service, NEFs and
court calendaring mail are one-way — nobody replies to them. Do not describe
them as unanswered, and do not average them together with mail a person has to
answer.

## Hard rules

- Turnaround is reported per GATE. Report it that way. Never convert it into a
  statement about how fast a person is.
- NEVER attribute a number to a named individual, even when asked directly.
  Docketry has no login: the only names it holds are free-text strings typed
  into an approval, so any per-person figure would be both wrong and
  unfalsifiable. Say that, and offer the per-gate view instead.
- Report what the command returned. Do not estimate a figure it did not give
  you, and say plainly when a period has too little data to mean anything.
