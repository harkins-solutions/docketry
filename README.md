# Docketry

Docketry drains a dedicated intake mailbox over IMAP, normalizes each message,
runs a declared set of checks against it, and stops anything that fails one
until a named person approves it in an audit log. It runs on one machine, in
one directory, with no server component and no outbound network traffic.

A Python 3.11+ CLI plus a local-only web queue. Core is stdlib-only.

```console
$ pip install docketry
$ docketry demo          # sample traffic + dashboard, no mailbox needed
```

---

## Contents

[What it does](#what-it-does) ·
[Install](#install) ·
[Quickstart](#quickstart) ·
[How a message moves](#how-a-message-moves) ·
[Gates that ship](#gates-that-ship) ·
[Configuration](#configuration) ·
[Commands](#commands) ·
[Writing a gate](#writing-a-gate) ·
[Audit log](#audit-log) ·
[Local model](#local-model-optional) ·
[What it does not do](#what-it-does-not-do)

## What it does

A firm points forwarding rules at a mailbox nobody works out of —
`intake@yourfirm.com`. Docketry reads that mailbox and no other. For each
message it:

1. Parses it into a normalized envelope: addresses, subject, text body,
   attachments with SHA-256 digests, and threading headers.
2. Stores it in SQLite on local disk, attachment bytes in a content-addressed
   directory. Deduplicated on the SHA-256 of the raw message.
3. Runs the gates bound to the pipeline's first stage.
4. Sets the message's status from the results: `ok`, `pending_review` or
   `blocked`.
5. Holds anything not `ok` until `docketry approve` records a name and a role.
   The gates then run again; the message advances only if they pass.

Two of the shipped gates do work a firm would otherwise do by hand.

**`name-screen`** matches a list of party or matter names against subject, body
and attachment filenames, case-insensitively on word boundaries. Bound with
`on_fail = "block"`, a message naming a screened party stops, and releasing it
writes a row containing the approver's name, role, gate and timestamp.

**`notice-parser`** extracts case numbers, document titles and dates from court
e-service mail. Five adapters ship — `fl-eportal-service`, `pacer-nef`,
`jacs-hearing`, `jaws-hearing`, `efile-receipt` — producing one of three notice
types: `service_notice`, `filing_receipt`, `hearing_notice`. Mail the adapters
cannot read is held rather than half-parsed, and `docketry stats` counts
template drift separately so a portal changing its format is visible.

## Install

```console
$ pip install docketry                                # core, stdlib-only
$ pip install "docketry[pdf,ocr,docx,cite,export]"    # optional features
```

| Extra | Pulls in | Enables |
|---|---|---|
| `pdf` | `pypdf` | PDF text extraction, redaction |
| `ocr` | `pytesseract`, `Pillow` | scanned PDFs; also needs the `tesseract` binary |
| `docx` | `python-docx` | Word extraction, Word timeline export |
| `cite` | `eyecite`, `httpx` | citation extraction, CourtListener verification |
| `export` | `openpyxl`, `python-docx` | Excel and Word exports |

Single-file executables for Windows, macOS and Linux are attached to every
[release](https://github.com/harkins-solutions/docketry/releases); run one with
no arguments and it opens the demo. `docketry doctor` reports which extras are
installed and what is unavailable without them.

## Quickstart

```console
$ docketry init
Intake mailbox address (e.g. intake@yourfirm.com): intake@smallfirm.com
IMAP host for that mailbox [imap.smallfirm.com]:
Folder to read [INBOX]:
Store the mailbox password in config.toml? (no = read it from DOCKETRY_IMAP_PASSWORD) [y/N]: n
Your firm's own email domains [smallfirm.com]:
Who reviews intake day to day [paralegal]: legal assistant
Who can release anything, including a conflict hold [attorney]:
Screened names or matters (comma separated): Acme Insurance, Roberta Vance
Addresses or domains court e-service arrives from [@myflcourtaccess.com, @uscourts.gov]:
Hold attachments larger than (MB) [25]:

Written to docketry-home:
  config.toml
  guardrails.toml
  roles.toml
```

Nine questions, three files. The IMAP host is guessed from the address domain.
The manifest and the role registry are built and validated in memory first, so
answers that would not load fail before anything is written. `docketry init
--host H --user U` skips the questions for scripted installs.

```console
$ export DOCKETRY_IMAP_PASSWORD='app-password-here'
$ docketry poll                      # one read-only sweep
$ docketry queue                     # what is held, and by which gate
$ docketry approve 3 --gate name-screen --by "A. Vance" --role attorney
$ docketry ui                        # same queue at http://127.0.0.1:8642
```

Polling never marks, moves or deletes anything in the mailbox, and Docketry
never sends mail. Position is tracked by the mailbox's UIDVALIDITY and
last-seen UID; if the server resets UIDVALIDITY, the cursor resets rather than
skipping messages.

## How a message moves

Stages are declared in `guardrails.toml`; the default is `["ingest", "review"]`.

```
poll → parse → store → gates(stage 1) → status
                                          ├── ok             → advance
                                          ├── pending_review → queue
                                          └── blocked        → held
                                                   ↓
                                          approve (name + role, recorded)
                                                   ↓
                                          gates run again → advance → next stage → done
```

`advance()` is the only code path that moves a message forward. An approval
does not set a status; it inserts an audit row, after which that stage's gates
run again against the stored message. A hold clears when the gates clear it.

Statuses: `ok`, `pending_review`, `blocked`, `done`.

Gates return findings with a severity. Only `fail` can hold a message. What a
`fail` does is the binding's `on_fail`:

| `on_fail` | Effect of a `fail` finding |
|---|---|
| `warn` | recorded; message continues |
| `bounce` | status `pending_review`; appears in `docketry queue` |
| `block` | status `blocked` |

Both held states clear the same way: an approval by a role the registry says
may release that gate.

## Gates that ship

| id | Checks | Options | Stages |
|---|---|---|---|
| `name-screen` | subject, body, attachment filenames against a term list; case-insensitive, word-boundary | `terms` (list, required), `note` (str) | any |
| `notice-parser` | that court e-service mail parses to a known notice type | `adapters_file` (path) | `ingest` |
| `sender-scope` | sender against allow/deny lists of addresses or `@domain` suffixes | `allow` (list), `deny` (list) | `ingest` |
| `attachment-policy` | attachment extension and size | `deny_extensions` (list), `max_size_mb` (number) | `ingest` |
| `doc-classifier` | proposes a document type per attachment; never applies one | — | any |
| `provenance-stamp` | records source and hashes; informational, never holds | — | any |

`allowed_stages` is checked when the manifest loads: binding `notice-parser` to
a stage other than `ingest` is a load error, not a runtime surprise.

A binding:

```toml
[pipeline]
stages = ["ingest", "review"]

[[gate]]
id = "name-screen"
binds_to = ["ingest"]
on_fail = "block"
authority = "attorney"

[gate.options]
terms = ["Acme Insurance", "Roberta Vance"]
note = "ethical wall"
```

Three worked manifests are in [`examples/`](examples/) — default, solo-strict,
litigation-team. A test loads all three on every build.

## Configuration

A Docketry home is one directory; nothing is stored outside it.

| Path | Contains |
|---|---|
| `config.toml` | mailbox connection, firm domains, optional model. Created mode 0600 |
| `guardrails.toml` | stages and gate bindings |
| `roles.toml` | role names and what each may release (optional) |
| `adapters.toml` | firm-specific e-service parsers (optional) |
| `contacts.toml` | who an address belongs to (optional) |
| `gates/*.py` | the firm's own gates (optional) |
| `store/docketry.db` | SQLite: messages, findings, approvals, notices, matters |
| `store/attachments/` | attachment bytes, content-addressed by SHA-256 |
| `anchors.log` | audit-chain heads written by `docketry anchor` |

```toml
# config.toml
[mailbox]
host = "imap.gmail.com"
user = "intake@yourfirm.com"
folder = "INBOX"
port = 993                 # default
# password = "..."         # or set DOCKETRY_IMAP_PASSWORD, which wins

[firm]
domains = ["yourfirm.com"]

[llm]                      # optional; see Local model
base_url = "http://127.0.0.1:11434"
model = "qwen2.5"
timeout = 120
```

`roles.toml` names the jobs the firm actually has and what each may release.
`may_release` takes gate ids or `"*"`:

```toml
[[role]]
name = "paralegal"
may_release = ["sender-scope", "attachment-policy", "notice-parser"]

[[role]]
name = "attorney"
may_release = ["*"]

[[person]]                 # optional
name = "Dana Reyes"
roles = ["paralegal"]
```

Without `roles.toml`, an approval's role must equal the gate's `authority`
exactly. With it, a role whose `may_release` covers the gate can release it, so
an attorney can clear a hold marked for a paralegal. Where `[[person]]` entries
exist, a listed person cannot approve under a role they do not hold.

There is no login. A role is a name typed at approval time and checked against
the registry: it catches mistakes, not lies.

## Commands

`docketry --home PATH <command>`; `--home` defaults to `./docketry-home`.

**Intake and review**

| Command | Does |
|---|---|
| `init` | ask nine questions, write the home. `--host/--user` to skip; `--store-password` to store it |
| `poll` | one read-only IMAP sweep: ingest, run gates, parse notices |
| `watch --every N` | `poll` on a loop, N seconds apart |
| `queue` | messages held, and the gates holding them |
| `approve ID --gate G --by NAME --role R [--note N]` | record an approval, re-run the gates |
| `advance ID` | re-run gates and move one stage forward |
| `status` · `stats [--days N] [--json]` | counts by status; volume, holds by gate, time-to-release |
| `digest` | one-screen summary to paste into mail |
| `ui [--port N]` | review queue on 127.0.0.1; refuses any other bind address |
| `doctor` | config, gates, manifest, roles, extras, model reachability, audit chain |
| `demo [--port N]` | disposable home with sample traffic |

**Gates**

| Command | Does |
|---|---|
| `gates [--quiet]` | every bindable gate, its source, its allowed stages |
| `new-gate ID [--title T] [--force]` | write a working gate into `<home>/gates/` |
| `try-gate ID [--subject S] [--from A] [--body B] [--attach F] [--eml FILE] [--option K=V]` | run one gate against one message |

**Notices, matters, documents**

| Command | Does |
|---|---|
| `notices [--type T]` | parsed notices and their extracted fields |
| `matters` · `matter-open` · `matter-status` · `matter-advance` | matters through firm-defined workflow stages |
| `classify FILE` · `class-queue` · `class-apply ID --by --role` | document-type proposals and their approval |
| `timeline CASE` · `timeline-export CASE OUT` · `docket-reconcile CASE DOCKET` | case chronology, Word/Excel export, diff against a pulled docket |
| `redact-scan` · `redact-apply` · `redact-verify FILE` | find terms, burn boxes into a PDF, confirm they are gone |
| `verify-draft FILE [--offline]` · `lint FILE [--rules F]` | citation checking, draft rules |
| `report [--days N]` · `contacts` · `roles` · `workflow-check FILE` | pipeline health, config inspection |
| `anchor` | verify the approval chain, print its head |

`doctor` exits non-zero if anything is misconfigured. Commands exit with a
one-line message rather than a traceback when the cause is config or input.

## Writing a gate

A gate is a class with an `id` and a `check()` returning findings.

```console
$ docketry new-gate zip-screen
wrote docketry-home/gates/zip_screen.py

$ docketry try-gate zip-screen --attach "discovery.zip"
gate:    zip-screen (file:gates/zip_screen.py)
message: 'Test message' from someone@example.com, 1 attachment(s)
result:  [fail] discovery.zip is a zip archive
```

Add four lines to `guardrails.toml` and it runs on every message, held and
released like any shipped gate. Gates load from `<home>/gates/*.py` or from an
installed package declaring a `docketry.gates` entry point.

**[GATES.md](GATES.md)** — walkthrough and reference.
**[ARCHITECTURE.md](ARCHITECTURE.md)** — diagrams, data model, module map.

## Audit log

Every finding and every approval is a row in `store/docketry.db`. Approvals are
hash-chained: each row stores a SHA-256 over its own fields plus the previous
row's digest, so an edited, deleted, reordered or inserted approval stops
verifying. `doctor` checks the chain; `anchor` verifies it and prints the head:

```console
$ docketry anchor
docketry-anchor 2026-08-30T02:27:55+00:00 approvals=118 head=9f2c...e41
```

An intact chain means no row changed in place. It does not mean the log is
authentic: the database is on the firm's disk with no key, so anyone who can
edit a row can recompute every digest after it.
[`tests/test_chain.py`](tests/test_chain.py) does exactly that and asserts the
rewritten chain verifies. Detecting a rewrite requires a copy of the head kept
where it cannot be edited — mail it, print it, or let `docketry digest` carry
it into a daily summary.

Approvals written before v0.16 carry no digest. They are counted and reported
as unchained rather than back-filled.

## Local model (optional)

Docketry works fully without a model and calls one only if `[llm]` is set. The
hostname is resolved and every returned address checked before a request is
built; unless all of them are loopback, private-range or link-local, the
request is refused. The vetted address is the one dialled, and a 3xx response
is refused rather than followed.

Any OpenAI-compatible server works: Ollama, llama.cpp, vLLM, LM Studio.
Reasoning models' `<think>` narration is separated from the answer rather than
returned as it.

A model proposes. It never releases a hold, applies a classification, chooses
what to redact, or advances a stage. `tests/test_llm.py` asserts that no gate,
the pipeline runner, and the redaction path import the model client.

## What it does not do

- **Not hosted.** No server, no telemetry, no account. Nothing leaves the machine.
- **Not case management.** No billing, trust accounting, client portal or calendar sync.
- **Not access control.** No login; roles are attestations.
- **Not a security product.** No malware or phishing claims. `attachment-policy`
  is pipeline policy, not antivirus.
- **Not encrypted at rest.** Messages and attachments are plaintext on disk,
  protected by file permissions. Use full-disk encryption.
- **No currency guarantee.** Docketry never claims a rule, deadline or citation
  is current. It checks inputs against sources you point it at, and fails
  loudly when it cannot.

## Skills

`skills/` holds nine agent skills for Claude Code or any Agent
Skills-compatible harness: `intake-triage`, `classify-document`,
`review-draft`, `redact-document`, `build-timeline`, `reconcile-docket`,
`manage-matter`, `pipeline-health`, `assign-contacts`. Install with
`cp -r skills/* .claude/skills/`.

Each drives the same CLI the pipeline uses and carries explicit limits: no
citation verified from model knowledge, no classification applied without a
named approver, no hold released by the agent. Each ships an eval suite in
`claude plugin eval` format, and a test fails the build if one does not.

## Development

```console
$ python -m unittest discover -s tests          # 437 tests
```

`tests/test_boundaries.py` asserts `docketry/core/` imports nothing from
`tools/`, the CLI or the UI, and caps the port at 12 files and 2,500 lines.
`tests/test_first_gate.py` executes GATES.md step by step. `tests/test_examples.py`
loads every shipped example and checks the skills' eval suites.

## License

Apache-2.0. [SECURITY.md](SECURITY.md) covers what is in scope for a report and
how to send one without pasting client data into a public tracker.
