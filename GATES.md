# Writing a gate

A gate is a Python class with an `id` and a `check()` method. Docketry runs it
against every message entering a stage the manifest binds it to, and holds the
message if it returns a finding with severity `fail`.

Everything Docketry enforces is a gate, including the shipped ones. There is no
private interface.

Requires Python 3.11+, `pip install docketry`, and a home directory
(`docketry init`).

---

## Walkthrough

### 1. Write it

```console
$ docketry new-gate long-subject
wrote docketry-home/gates/long_subject.py
```

The file is a complete gate: it holds any message whose subject runs over five
words. Every part of it is commented.

### 2. Run it

```console
$ docketry try-gate long-subject --subject "this subject is quite a lot longer than five words"
gate:    long-subject (file:gates/long_subject.py)
message: 'this subject is quite a lot longer than five words' from someone@example.com, 0 attachment(s)
result:  [fail] subject is 10 words, over the 5 this pipeline accepts

$ docketry try-gate long-subject --subject "short one"
result:  no findings — this message passes
```

`try-gate` builds one message and calls your `check()`. No mailbox, no
pipeline, no database. Use `--attach NAME` to add an attachment, `--from` and
`--body` to set the sender and body, or `--eml FILE` to run against a saved
message.

### 3. Change it

Open `docketry-home/gates/long_subject.py` and replace the body of `check()`.
To hold zip archives instead:

```python
    def check(self, envelope, options: dict) -> list[Finding]:
        held = []
        for attachment in envelope.attachments:
            if attachment.filename.lower().endswith(".zip"):
                held.append(Finding(
                    self.id,
                    SEVERITY_FAIL,
                    f"{attachment.filename} is a zip archive; this pipeline"
                    " accepts documents",
                ))
        return held
```

```console
$ docketry try-gate long-subject --attach "discovery.zip"
result:  [fail] discovery.zip is a zip archive; this pipeline accepts documents
```

The summary string is what appears in `docketry queue` and in the review UI
next to the held message. Write it for whoever has to act on it.

### 4. Bind it

A gate that exists does not run until a manifest binds it. Add to
`guardrails.toml` — `new-gate` prints this block for you:

```toml
[[gate]]
id = "long-subject"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[gate.options]
max_words = 5
```

Confirm it is loaded:

```console
$ docketry gates
attachment-policy  built-in  [ingest only]
                   What file types and sizes this pipeline accepts. Hygiene, not AV.
doc-classifier     built-in (tools)
long-subject       file:gates/long_subject.py
                   Long subject.
name-screen        built-in
                   Hold any message whose content mentions a screened name.
notice-parser      built-in (tools)  [ingest only]
provenance-stamp   built-in
sender-scope       built-in  [ingest only]
```

It now runs on every message entering `ingest`. A `fail` sends the message to
`docketry queue` with status `pending_review`, and it stays there until a
paralegal approves it.

---

## Reference

### The class

```python
from docketry.core.gates import register
from docketry.core.pipeline import Finding, SEVERITY_FAIL, SEVERITY_WARN, SEVERITY_INFO


@register
class MyGate:
    id = "my-gate"                      # str, required; lowercase-hyphenated
    allowed_stages = None               # set[str] | None; None means any stage

    def check(self, envelope, options: dict) -> list[Finding]:
        ...                             # required

    def validate_options(self, options: dict) -> list[str]:
        ...                             # optional; returns problems, empty = ok
```

`register` takes the class, as a decorator or a call. `Finding` is a dataclass
of `(gate_id: str, severity: str, summary: str)`.

Gates are instantiated with no arguments, once per run. Do not keep state
between messages on the instance.

### Envelope fields

`check()` receives the parsed message. Encodings, HTML bodies and filename
escaping are already resolved.

| Field | Type | Notes |
|---|---|---|
| `message_id` | `str` | from the `Message-ID` header |
| `from_addr` | `str` | bare address, no display name |
| `to`, `cc` | `list[str]` | bare addresses |
| `subject` | `str` | decoded |
| `body_text` | `str` | text part, or HTML converted to text |
| `date` | `str` | as sent |
| `attachments` | `list[Attachment]` | see below |
| `raw_sha256` | `str` | digest of the raw message; the dedup key |
| `source` | `str` | which mailbox or command ingested it |
| `fetched_at` | `str` | ISO 8601 UTC |
| `in_reply_to` | `str` | threading |
| `references` | `list[str]` | threading |
| `auto_submitted`, `precedence`, `list_id` | `str` | bulk/auto-reply headers |

`Attachment`: `filename` (sanitized to a basename), `content_type`, `size`
(bytes), `sha256`, and `content` (`bytes` — the real bytes, on every run
including re-runs after an approval).

### Severity and on_fail

| Severity | Constant | Effect |
|---|---|---|
| `fail` | `SEVERITY_FAIL` | can hold the message, per `on_fail` |
| `warn` | `SEVERITY_WARN` | recorded; message continues |
| `info` | `SEVERITY_INFO` | recorded; message continues |

What a `fail` does is the binding's business, not the gate's:

| `on_fail` | Result |
|---|---|
| `warn` | recorded, message continues |
| `bounce` | status `pending_review`; shows in `docketry queue` |
| `block` | status `blocked` |

The same gate is advisory in one firm's manifest and blocking in another's with
no code change. A gate cannot release its own hold; that requires
`docketry approve` with a name and a role, which writes an audit row.

### allowed_stages

Set it to the stages the gate is meant for, and binding it elsewhere raises
`ManifestError` when the manifest loads:

```
gate 'notice-parser' is not meant for stage(s) ['review']; it belongs in: ['ingest']
```

`None` means any stage.

### Options

`[gate.options]` in the manifest arrives as the `options` dict, with TOML types
preserved. Implement `validate_options()` and whatever you return refuses the
manifest at load time with your text attached:

```
guardrails.toml refused: gate 'long-subject' options: max_words must be a whole number
```

`try-gate` reads options from the manifest binding if it exists, or takes
`--option key=value` (values parsed as TOML, so `--option max_words=5` is an
integer and `--option note=hello` is a string).

### Where gates load from

| Source label | Loaded from |
|---|---|
| `built-in` | `docketry/core/gates/` |
| `built-in (tools)` | `docketry/tools/`, registering the same way yours does |
| `file:gates/x.py` | `<home>/gates/*.py`, in filename order |
| `package:name` | an installed package's `docketry.gates` entry point |

Home gates load from `<home>/gates/` only. Files whose names start with `_` are
skipped, so `_helpers.py` is available for import without being treated as a
gate. Loading happens before the manifest is read, since a manifest binding an
unregistered gate is a load error.

`<home>/gates/*.py` is executed with the operator's permissions — the same
trust already given to `guardrails.toml`, which decides what the pipeline does.
`docketry gates` and `docketry doctor` print the source of every gate.

### Errors it will give you

| Situation | Message |
|---|---|
| file raises on import | `gate refused: broken.py failed to load: SyntaxError: ...` |
| file registers nothing | `gate refused: empty.py registered no gates — a gate file needs @register ...` |
| id already taken | `gate refused: gate id 'name-screen' is already registered by built-in ...` |
| no `id` attribute | `MyGate has no id — a gate needs id = "some-name"` |
| no `check` method | `gate 'my-gate' has no check(envelope, options) method` |
| id not lowercase-hyphenated | `gate id 'My_Gate' should be lowercase words joined by hyphens` |
| manifest binds an unknown gate | `guardrails.toml refused: unknown gate 'my-gate' (registered: ...)` |

A file that fails to load stops the command rather than being skipped, so a
gate is never silently absent. A duplicate id is refused rather than
overwriting, so a home file cannot replace a shipped gate.

### Rules a gate should follow

Docketry cannot enforce these on your code — it is your Python on your machine
— but the shipped gates follow them and the pipeline assumes them.

- **Deterministic.** `check()` is called again after every approval. If it
  answers differently on the same message, holds will clear at random.
- **Read-only.** Return findings; do not write to the store, send mail, or
  modify the message.
- **No network.** Docketry's stated property is that nothing it reads leaves
  the machine.
- **No model.** Gates decide, models propose elsewhere. `tests/test_llm.py`
  asserts no shipped gate imports the model client.

### Distributing one

For a gate that outgrows a single file, ship a package with an entry point:

```toml
# pyproject.toml
[project.entry-points."docketry.gates"]
conflict-check = "my_package.gates:ConflictCheck"
```

After `pip install my-package` it appears as `package:conflict-check`. The
entry point may point at the gate class or at a module that registers on
import.

### Testing one

`try-gate` is the edit-run-read loop. For a test suite, call the class
directly — there is nothing to mock:

```python
from docketry.core.envelope import parse_message
from my_gates.zip_screen import ZipScreen


def test_a_zip_is_held():
    envelope = parse_message(raw_bytes, source="test", fetched_at="now")
    findings = ZipScreen().check(envelope, {})
    assert [f.severity for f in findings] == ["fail"]
```

`docketry try-gate my-gate --eml saved.eml` runs against a real saved message,
which is the fastest way to check a gate against actual court e-service mail.

---

## Gate ideas

The shipped gates are the general cases. These are firm-specific by nature:

- **Conflict check against a live party list.** `name-screen` reads terms from
  the manifest; a gate reading your practice management system's party list
  instead needs no manual updating.
- **A notice parser for your state's system.** The shipped adapters cover
  Florida's portal and federal NEFs.
- **Matter-scoped confidentiality.** Hold anything naming a matter the
  recipient is walled off from, sourced from your own access rules.
- **Retention policy.** Flag mail that should not be in intake at all.

If you write one, open an issue — whether a stranger's gate is
indistinguishable from a shipped one is the thing this interface is trying to
get right.
