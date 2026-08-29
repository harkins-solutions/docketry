---
name: build-timeline
description: Reconstruct a case timeline from the notices, receipts and correspondence the firm received, and export it to Word or Excel. Invoke for "what's happened on this case", "build the timeline", "give me a chronology", or "export the docket to Excel".
---

# Build a case timeline

The timeline is assembled from what crossed the firm's intake boundary. It is
**not the court's docket** and never claims to be complete. Say that when you
report it; the exports say it on their own face for the same reason.

```
docketry timeline <case-number>
docketry timeline <case-number> --layer record
docketry timeline <case-number> --thread <thread-key>
```

## Four layers, and why they never merge

- **record** — served, filed, court events. Of record.
- **correspondence** — threads with counsel or third parties. Context.
- **client** — communications with the client. Privileged, different in kind.
- **derived** — an inference of ours (a gap, a computed date). Not a record.

Keep them distinct in anything you write. A service notice and an email from
opposing counsel carry different legal weight, and a summary that renders them
identically eventually gets one cited as the other.

## Attaching correspondence

Correspondence carries no case number, so it is never guessed into a case. A
thread joins the timeline only when a human points at it:

```
docketry timeline <case-number> --thread <thread-key>
```

If the user wants "the emails from opposing counsel on this case too", ask
which thread — do not infer it from a subject line.

## Export

```
docketry timeline-export <case-number> out.xlsx
docketry timeline-export <case-number> out.docx --layer record
```

Excel gets a frozen header, a filter on every column and real date cells.
Word gets a real table, landscape, with the header repeating across pages.

## Gaps

Gaps come in two classes and must never be reported as one:

- **proven** — a hole in a federal document-number sequence. That is a fact.
- **suspected** — everything else. State notices carry no sequence, so absence
  proves nothing on its own.

When there is no sequence to reason from, the tool says nothing rather than
guessing, and so should you. To actually find out what is missing, use
`reconcile-docket` against a docket a person pulls.

## Hard rules

- NEVER describe the timeline as the docket, or as complete.
- NEVER present a `derived` entry as something that happened of record.
- Every entry names the message it came from. Keep that provenance in what you
  report — a chronology nobody can trace back is not evidence of anything.
