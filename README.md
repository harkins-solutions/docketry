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

## What makes it different

Best practices here are **code, not suggestions**:

- A message cannot cross a stage boundary without passing its gates or
  carrying a recorded approval from the role the manifest names. `advance()`
  is the only forward path and it re-checks every time. There is no advisory
  mode and no bypass flag.
- Gates declare which pipeline stages they are meant for; binding one
  elsewhere is a load-time error, not a footnote in documentation.
- Every finding and every human approval lands in an audit table on the
  firm's own disk.

## What it is not

- **Not hosted.** Docketry runs on the firm's machine. We never hold, transit,
  or store anyone's email.
- **Not a security product.** Docketry makes no malware, phishing, or other
  cybersecurity claims. Its attachment and sender gates are pipeline-hygiene
  policy — what the *pipeline* accepts — nothing more. Get real security from
  your mail provider and endpoint tooling.
- **Not legal advice, and not a currency service.** Docketry never claims any
  rule, deadline, or authority is up to date. It checks the inputs you give
  it against the sources you point it at, and it fails loudly.

## Data at rest

Docketry stores messages and attachments in plaintext SQLite and files in
its home directory, protected by file permissions (config is written 0600).
It does NOT do application-level encryption, deliberately: an encrypted
database whose key sits on the same disk is comfort, not protection. Protect
a Docketry home the way you protect the rest of the client file system —
OS full-disk encryption (BitLocker / FileVault / LUKS) and OS accounts.
Nothing is ever copied off the machine.

## Try it in sixty seconds

```console
$ pip install docketry   # or grab a one-file executable from Releases
$ docketry demo
```

`demo` seeds a disposable home with sample traffic — a clean service notice,
a federal NEF, a hearing notice, a drifted portal template, and an unknown
sender — and opens the local dashboard so you can watch the gates hold the
right three and release them yourself. No mailbox, no configuration, nothing
saved. (Downloadable one-file executables for Windows/macOS/Linux attach to
tagged releases — double-clicking one opens this same demo.)

## Quickstart

```console
$ pip install docketry
$ docketry init --host imap.gmail.com --user intake@yourfirm.com
$ export DOCKETRY_IMAP_PASSWORD='app-password-here'
$ docketry poll        # sweep the intake mailbox once
$ docketry queue       # see anything a gate held for review
$ docketry approve 3 --gate sender-scope --by "Dana" --role paralegal
$ docketry status
```

The pipeline is declared in `guardrails.toml` in your Docketry home directory
(see `examples/guardrails.toml`). Reading the intake mailbox is strictly
read-only: messages are never marked, moved, or deleted.

## Skills starter pack

`skills/` ships three agent skills for Claude Code (or any Agent
Skills-compatible harness): `review-draft` (runs verify-draft + lint on a
draft), `classify-document`, and `intake-triage`. Install by copying into
your project:

```console
$ cp -r skills/* .claude/skills/
```

The skills are a front door, not a bypass: each one drives the same CLI
commands and gates the pipeline uses, and each carries hard rules — a skill
never verifies a citation from model knowledge, never applies a
classification without a named human approver, and never releases a hold
itself. `examples/` includes three guardrail manifests to start from
(default, solo-strict, litigation-team).

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
