---
name: reconcile-docket
description: Compare the reconstructed timeline against a docket a person pulled from the court, and report what each side is missing. Invoke for "are we missing anything", "check our file against the docket", "did we get served everything", or "reconcile the docket".
---

# Reconcile against a pulled docket

The reconstruction can only be as complete as the firm's mail was. The way to
find out what is missing is not to infer harder — it is to compare against the
real thing.

**Docketry never fetches from a court system.** A person logs in, pulls the
docket, and hands you the file. If there is no pulled docket, there is no
reconciliation — ask for one, and do not offer to retrieve it yourself.

```
docketry docket-reconcile <case-number> <pulled-docket-file>
```

The file can be a CSV export (headers containing date / title / description /
doc) or pasted lines of the form `12  03/19/2026  Motion to Dismiss`. Lines
that cannot be read are skipped rather than guessed at — a misread line
becomes a false finding in both directions.

## Both directions are findings

- **on the docket, NOT here** — the firm was never served, or the notice never
  reached the intake mailbox. This is the one people ask for. Treat every one
  as something to chase, not a formatting artefact.
- **here, NOT on the docket** — often more interesting. A filing that was
  rejected and never re-filed, an entry attached to the wrong case, or
  correspondence someone treated as a filing.
- **probable match, confirm by hand** — same document, different wording.
  Never accept these silently.

Correspondence is excluded from the comparison on purpose: an email was never
going to appear on a docket, and counting its absence would bury the real
findings.

## Reporting

Report counts first, then each discrepancy with its date and title. Exit code
is non-zero when anything is unaccounted for — say so rather than describing
the case as reconciled.

## Hard rules

- NEVER fetch, scrape, or offer to log into a court system, a portal, or a
  free-look link. The human brings the docket across the boundary.
- NEVER auto-accept a probable match. Present it and let a person decide.
- NEVER report a case as reconciled while `to_confirm` entries are outstanding
  — unconfirmed is not the same as matched.
- Absence of a sequence number is not evidence of completeness. If the pulled
  docket is partial (one page of many, a filtered view), say that the
  comparison is only as good as what was pulled.
