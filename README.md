# Docketry

*Working name — a local, gate-enforced port that a law firm's email flows into.*

Most firms work out of their email. Docketry meets them there — without going
into their email. The firm creates a **dedicated intake mailbox** (for example
`intake@yourfirm.com`), points forwarding rules at it, and Docketry drains that
one mailbox with credentials the firm holds. Nothing enters the pipeline that
the firm didn't send across the boundary.

Docketry is the **base layer** of a family of small, composable open-source
legal workflow tools (document classification, e-service notice parsing,
citation verification, draft linting). Each plugs into the port as a **gate**
in a declared pipeline.

## Why a firm installs this

Two of the gates are the reason. The rest are hygiene.

**The ethical wall.** List the parties or matters a reviewer must not see
flowing through intake unreviewed. Anything mentioning one stops — not warns,
stops — and the only thing that moves it is a recorded release by the role
your manifest names. What that leaves behind is a row saying who released it
and when, on the firm's own disk. A screen that maps to a bar rule is worth
having; a screen you can show someone the record of is worth more, and that
record is the thing a grievance actually asks about.

**Court e-service.** Docketry parses service and docket notices into case
numbers, document titles and dates — and holds any notice it cannot read
instead of guessing. A misread hearing date is a malpractice claim. An unread
one is a phone call. When a portal changes its template, the queue says so
rather than quietly extracting nothing.

Everything else here — attachment types, sender scope, size caps — is
pipeline hygiene. Useful, worth having on, and not why anyone would install
this. `docketry demo` shows the two above, and the rest as an afterthought.

## What makes it different

Best practices here are **code, not suggestions**:

- A message cannot cross a stage boundary without passing its gates or
  carrying a recorded approval from the role the manifest names. `advance()`
  is the only forward path and it re-checks every time. There is no advisory
  mode and no bypass flag.
- Gates declare which pipeline stages they are meant for; binding one
  elsewhere is a load-time error, not a footnote in documentation.
- Every finding and every human approval lands in an audit table on the
  firm's own disk, **hash-chained**: each approval carries a digest over its
  own content and the digest of the row before it, so an edited, deleted or
  reordered release stops verifying. `docketry doctor` checks the chain and
  fails if it is broken. Read the next section for what that is worth.

## What it is not

- **Not hosted.** Docketry runs on the firm's machine. We never hold, transit,
  or store anyone's email.
- **Not case management.** Docketry tracks a matter through the stages your
  firm defines and gates the moves between them. It does no billing, no trust
  accounting, no client portal and no calendar sync — that is your practice
  management system's job and it is better at it.
- **Not an access control system.** Roles decide what a hold is waiting for
  and are recorded against the name of whoever released it. There is no login:
  a role is an attestation, so the registry catches mistakes, not lies.
- **Not a security product.** Docketry makes no malware, phishing, or other
  cybersecurity claims. Its attachment and sender gates are pipeline-hygiene
  policy — what the *pipeline* accepts — nothing more. Get real security from
  your mail provider and endpoint tooling.
- **Not legal advice, and not a currency service.** Docketry never claims any
  rule, deadline, or authority is up to date. It checks the inputs you give
  it against the sources you point it at, and it fails loudly.

## The approval log, and what a hash chain is worth

Approvals are chained, and `docketry anchor` prints the head:

```console
$ docketry anchor
docketry-anchor 2026-08-29T21:14:03+00:00 approvals=118 head=9f2c...e41
```

An intact chain means nothing in the log was edited, deleted or reordered
after it was written. It does **not** mean the log is authentic, and the
difference matters if this is ever the record being questioned. The database
sits on the firm's own disk with no key, so anyone who can edit a row can
recompute every digest after it and hand you a chain that verifies. A test in
`tests/test_chain.py` does exactly that, on purpose, so nobody mistakes the
property for more than it is.

What makes a rewritten history detectable is the anchor: a copy of the head
kept somewhere the editor cannot reach. Mail the line to yourself, paste it
into a case note, print it, or let `docketry digest` carry it into whatever
daily summary the firm already sends. Once that line is somewhere the firm
does not administer, a rewritten log contradicts something that already left
the building — and that contradiction is the finding.

The chain catches the careless edit on its own. The anchor is what makes the
deliberate one need an accomplice. Docketry never sends anything, so moving
the anchor off the machine is the one step that stays yours.

## Data at rest

Docketry stores messages and attachments in plaintext SQLite and files in
its home directory, protected by file permissions (config.toml is *created*
0600 — not written and then tightened, so a stored password is never briefly
world-readable). It does NOT do application-level encryption, deliberately: an
encrypted database whose key sits on the same disk is comfort, not protection.
Protect a Docketry home the way you protect the rest of the client file system
— OS full-disk encryption (BitLocker / FileVault / LUKS) and OS accounts.
Nothing is ever copied off the machine.

On Windows that 0600 buys you nothing: `chmod` there moves the read-only bit
and says nothing about who else may read the file, whose permissions come from
the folder it sits in. On Windows, set `DOCKETRY_IMAP_PASSWORD` in the
environment instead of storing the password — `docketry init` says so too.

## Bring your own model (optional, local only)

Docketry works fully without a model, and calls one only if you configure it:

```toml
[llm]
base_url = "http://127.0.0.1:11434"   # Ollama, llama.cpp, vLLM, LM Studio
model = "qwen2.5"                     # or deepseek-r1, gemma2, llama3.1 ...
```

The endpoint is checked before any request is built and **refused unless it
resolves entirely to your own network** — loopback or private range. Point it
at a hosted API and Docketry stops rather than sending your documents to a
vendor. `docketry llm-check` and `docketry doctor` both say plainly whether
anything can reach off-network; with no model configured they say so too.

Any model your server can load works — the model name is a config string, not
an adapter. Reasoning models are handled: their narration is kept separate
from the answer, never presented as the conclusion.

A model here **proposes**. It never releases a hold, approves anything,
classifies, or decides what to redact. Those stay deterministic and
human-gated, and a test enforces that no gate, the pipeline runner, or the
redaction path can consult one.

We ship no weights and endorse no model; licences differ per release and are
yours to check.

## Try it in sixty seconds

```console
$ pip install docketry   # or grab a one-file executable from Releases
$ docketry demo
```

`demo` seeds a disposable home with sample traffic and opens the local
dashboard. Three messages pass clean. Three stop: a conflicts email naming a
screened party (blocked by the wall, attorney-only release), a court e-service
notice whose portal changed its template (held rather than guessed at), and an
unknown sender with an attachment (hygiene). You release them yourself and
watch the audit rows appear. No mailbox, no configuration, nothing saved.
(Downloadable one-file executables for Windows/macOS/Linux attach to tagged
releases — double-clicking one opens this same demo.)

## Quickstart

```console
$ pip install docketry
$ docketry init        # asks; writes config.toml, guardrails.toml, roles.toml
$ export DOCKETRY_IMAP_PASSWORD='app-password-here'
$ docketry poll        # sweep the intake mailbox once
$ docketry queue       # see anything a gate held for review
$ docketry approve 3 --gate sender-scope --by "Dana" --role paralegal
$ docketry status
```

`init` asks nine questions in plain words — which mailbox, what your firm
calls the person who reviews intake, who can release a conflict hold, which
names are behind the wall — and writes the three config files from the
answers, commented, so nobody has to author TOML to get started. It guesses
the IMAP host from the address and never writes a home that refuses to load.
Pass `--host` and `--user` to skip the questions for scripted installs.

Everything it writes is an ordinary file you can edit afterwards: the pipeline
lives in `guardrails.toml` in your Docketry home (see `examples/`), the roles
in `roles.toml`. Reading the intake mailbox is strictly read-only: messages
are never marked, moved, or deleted.

## Skills starter pack

`skills/` ships nine agent skills for Claude Code (or any Agent
Skills-compatible harness):

| Skill | What it drives |
|---|---|
| `review-draft` | verify-draft + lint on a draft |
| `classify-document` | the deterministic classifier and its staged queue |
| `intake-triage` | the review queue and approvals |
| `redact-document` | redaction, its preview, and its verification |
| `build-timeline` | the case timeline and its exports |
| `reconcile-docket` | diffing the reconstruction against a pulled docket |
| `manage-matter` | matters moving through the firm's workflow stages |
| `pipeline-health` | the report on volume, bottlenecks and dead config |
| `assign-contacts` | who an address belongs to, and what that makes privileged |

Install by copying into your project:

```console
$ cp -r skills/* .claude/skills/
```

The skills are a front door, not a bypass: each one drives the same CLI
commands and gates the pipeline uses, and each carries hard rules — a skill
never verifies a citation from model knowledge, never applies a classification
without a named human approver, never releases a hold itself, never edits a
workflow to get a blocked matter through, never turns queue figures into a
statement about a named person, and never decides for itself who the client is.

Every skill ships with an eval suite, and a test fails the build if one does
not: a tool an agent can drive without evals is an untested tool with a good
description. `examples/` includes three guardrail manifests to start from
(default, solo-strict, litigation-team), plus a bare workflow and roles file.

## Evals

Each skill ships an eval suite (`skills/<name>/evals/`) in the
`claude plugin eval` format. The graders encode the hard rules as tests:
`tool_used` asserts the agent actually RAN `docketry verify-draft` (answering
from model knowledge fails the eval even when the answer is right),
`min: 0, max: 0` graders assert approvals were never executed by the agent,
and judge graders check the honesty of degraded-mode reporting. The eval
runner is currently in early access; the suites are validated for shape in
CI and runnable wherever `claude plugin eval` is enabled.

## Requirements

Python 3.11+. The core is stdlib-only. Feature plugins declare extras
(`pip install "docketry[pdf,ocr]"` etc.) — see [FEATURES.md](FEATURES.md)
for the feature manifest and its dependency graph.

## License

Apache-2.0.
