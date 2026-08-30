# Security

## If it involves real client data, do not open an issue

The issue tracker is public and permanent. If reporting something would mean
posting a client's name, a case number, a document or an address, email
**Joshua@HarkinsSolutionsSystemsGroup.com** instead.

You do not need a GitHub account to do that. If you would rather stay on
GitHub, [private vulnerability reporting][pvr] opens a report only the
maintainers can read.

[pvr]: https://github.com/harkins-solutions/docketry/security/advisories/new

If you need to send a document, redact it with Docketry first — `redact-apply`
removes the text rather than covering it, then re-reads the output and reports
anything still extractable. A box that comes back **unverifiable** covered no
readable text: the content is gone, but nothing could be confirmed, so check
that page yourself before it leaves your machine.

That advice stops where the tool is the problem. If Docketry is what mishandled
something — a redaction that did not hold, data written somewhere it should not
have been — do not redact with it and send it. Describe what happened and we
will work out how to reproduce it without the original.

## Reporting a vulnerability

Email **Joshua@HarkinsSolutionsSystemsGroup.com**, or use [private
vulnerability reporting][pvr]. Include what you did, what happened, and the
version (`docketry --version`). We will confirm receipt, tell you what we find,
and credit you in the release notes unless you would rather we did not.

Do not open a public issue for a vulnerability, and please do not post a
working exploit while it is unfixed.

## Supported versions

The latest release only. Fixes go forward rather than into patches of older
tags.

## Scope

Five properties are deliberate. A report that one of them is true is
describing the design; the right-hand column is what would be a real finding.

| Design | Real finding |
|---|---|
| **No login.** The review UI binds to 127.0.0.1 and refuses any other address. A role is a name typed at approval time, checked against `roles.toml`. | The UI reachable from another machine; an approval accepted without passing the role check. |
| **No encryption at rest.** Messages and attachments are plaintext SQLite and files, protected by file permissions. `config.toml` is created mode 0600 on POSIX; on Windows it inherits the directory ACL, and `init` says so when a password is stored. | The password written at looser permissions than 0600 on POSIX; secrets written anywhere outside the home directory. |
| **Home gates execute.** `<home>/gates/*.py` is imported and run with the operator's permissions — the same trust `guardrails.toml` already has. | A gate loaded from outside `<home>/gates/`; a home gate silently replacing a shipped gate id; a load failure being skipped rather than stopping the command. |
| **The approval chain detects edits, it does not prevent them.** Anyone who can write to the database can recompute every digest after the row they changed. | `chain_report()` missing an edit it should catch; `anchor` printing a head over a chain that does not verify. |
| **Not a security product.** No malware, phishing or antivirus claims. `sender-scope` and `attachment-policy` decide what the pipeline accepts, nothing more. | — |

**Local operation is not in that list.** Docketry sends nothing. A configured
model endpoint is refused unless every address it resolves to is loopback,
private-range or link-local. If you find any path that sends data off the
machine without the operator configuring it, that is a serious bug — report it
privately.
