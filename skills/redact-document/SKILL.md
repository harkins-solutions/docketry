---
name: redact-document
description: Remove text from a PDF so it is gone rather than covered, and prove it afterwards. Invoke for "redact this", "black out the SSNs", "take the client's name out of these exhibits", or any request to hide something in a document before it leaves the firm.
---

# Redact a document

A black rectangle drawn over text is not a redaction — the glyphs stay in the
file and any extractor lifts them back out. Docketry rasterises the page,
destroys the pixels, rebuilds a searchable text layer, and marks the gap with
`[REDACTED]` so an extractor reports a redaction instead of silence.

You do not decide what is sensitive. A human names the terms or draws the
boxes; you run the tool and report honestly what it did.

## Always preview first

```
docketry redact-scan <file.pdf> --term "<term>" --term "<term>"
```

Writes nothing. Report every hit with its page, and the count. If the scan
finds occurrences the human did not expect, stop and show them — an
unexpected hit usually means the term appears somewhere they had not
considered, which is a decision for them, not a detail for you.

## Then, only when told to

```
docketry redact-apply <file.pdf> <out.pdf> --term "<term>"
docketry redact-apply <file.pdf> <out.pdf> --box "1:0.10,0.22,0.61,0.26"
```

Boxes are `page:x0,y0,x1,y1` as fractions of the page, top-left origin. The
source file is never modified.

## Reporting the result — three states, not two

The command distinguishes three outcomes, and you must carry all three
through to the human rather than collapsing them into "done":

- **verified** — the words that sat under a bar are not readable inside it.
- **still readable** — a leak. Exit code is non-zero. Say so plainly and do
  not describe the file as redacted.
- **unverifiable** — the box covered no readable text (a signature, a photo,
  a chart). The content is destroyed, but nothing could be checked. Report
  these individually and tell the human to review them by eye. Never let an
  unverifiable box be counted as verified.

There is also a non-blocking advisory: a redacted phrase still standing
elsewhere in the same document. That is usually a second occurrence nobody
marked. Surface it; it is not a failure.

## Checking a file someone else produced

```
docketry redact-verify <file.pdf> --term "<term>"
```

## Hard rules

- NEVER choose the terms yourself. If asked to "find anything sensitive",
  propose a list and get it confirmed before scanning — and never run
  `redact-apply` off your own list.
- NEVER report a redaction as verified when any box came back unverifiable.
- NEVER re-run with a bigger box to make a leak finding go away. A leak means
  the human needs to look, not that the tool needs persuading.
- A redacted page becomes an image with a rebuilt text layer, so its text is
  only as good as the OCR. When the command warns that confidence is low, pass
  that warning on — the words are gone either way, but what remains may not be
  reliably searchable.
