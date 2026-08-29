"""Local review dashboard: the queue/approve flow in a browser.

Same store, same validated paths as the CLI — approvals go through the same
binding/authority checks, so the UI is a window, never a bypass. Serves on
127.0.0.1 ONLY: this is a single-user local tool, not a web app; there is no
auth layer and it must never be bound to a public interface. POSTs carry a
per-process random token so a stray cross-site request can't approve
anything.
"""
from __future__ import annotations

import html
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import store as st
from .pipeline import Runner
from .store import Store

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>Docketry</title><style>
body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem;color:#1a1a1a;background:#fafaf7}}
h1{{font-size:1.3rem}} h2{{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}} td,th{{text-align:left;padding:.35rem .5rem;border-bottom:1px solid #eee;vertical-align:top}}
.f{{color:#a11;font-weight:600}} .w{{color:#a60}} .i{{color:#567}}
.badge{{display:inline-block;padding:.1rem .45rem;border-radius:.6rem;font-size:.75rem;background:#eee}}
.badge.held{{background:#fde3e3}} .badge.done{{background:#e3f2e3}}
form.inline{{display:inline}} input[type=text]{{width:7rem}} button{{cursor:pointer}}
.counts span{{margin-right:1.2rem}}
</style></head><body>
<h1>Docketry — local review</h1>
<p><a href="/adapters">Court adapters</a></p>
<p class="counts">{counts}</p>
<h2>Held for review</h2>{queue}
<h2>Doc-type proposals</h2>{classq}
<h2>Recent notices</h2>{notices}
<p style="color:#888;font-size:.8rem">127.0.0.1 only · refreshes every 60s ·
approvals are recorded with the name and role you enter</p>
</body></html>"""


def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _render(store: Store, pipeline, token: str) -> str:
    counts = store.counts() or {}
    counts_html = "".join(
        f'<span class="badge {"held" if k in (st.PENDING_REVIEW, st.BLOCKED) else "done" if k == st.DONE else ""}">{_esc(k)}: {v}</span>'
        for k, v in sorted(counts.items())
    ) or "no messages yet"

    rows = []
    for row in store.list_by_status(st.PENDING_REVIEW) + store.list_by_status(st.BLOCKED):
        env = json.loads(row["envelope_json"])
        findings = "<br>".join(
            f'<span class="{"f" if f["severity"] == "fail" else "w"}">[{_esc(f["gate_id"])}] {_esc(f["summary"])}</span>'
            for f in store.findings_for(row["id"]) if f["severity"] != "info"
        )
        gates = sorted({f["gate_id"] for f in store.findings_for(row["id"])
                        if f["severity"] == "fail"})
        auth = {b.gate.id: b.authority for b in pipeline.bindings_for(row["stage"])}
        forms = "".join(
            f'<form class="inline" method="post" action="/approve">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<input type="hidden" name="message" value="{row["id"]}">'
            f'<input type="hidden" name="gate" value="{_esc(g)}">'
            f'<input type="text" name="by" placeholder="your name" required> '
            f'<button>approve {_esc(g)} as {_esc(auth.get(g, "?"))}</button>'
            f'<input type="hidden" name="role" value="{_esc(auth.get(g, ""))}">'
            f'</form> '
            for g in gates
        )
        rows.append(
            f'<tr><td>{row["id"]}</td><td>{_esc(env["subject"])}<br>'
            f'<small>from {_esc(env["from_addr"])}</small></td>'
            f'<td>{findings}</td><td>{forms}</td></tr>'
        )
    queue_html = (
        "<table><tr><th>#</th><th>message</th><th>held by</th><th>release</th></tr>"
        + "".join(rows) + "</table>"
    ) if rows else "<p>queue empty</p>"

    crows = []
    for r in store.open_classifications():
        current = f' <small>(current: {_esc(r["doc_type"])})</small>' if r["doc_type"] else ""
        crows.append(
            f'<tr><td>{r["id"]}</td><td>{_esc(r["filename"])}</td>'
            f'<td>{_esc(r["label"])} ({_esc(r["tier"])}){current}</td>'
            f'<td><form class="inline" method="post" action="/class-apply">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<input type="hidden" name="id" value="{r["id"]}">'
            f'<input type="text" name="by" placeholder="your name" required> '
            f'<input type="text" name="role" placeholder="role" required> '
            f'<button>apply (fill-only)</button></form></td></tr>'
        )
    class_html = (
        "<table><tr><th>#</th><th>file</th><th>proposal</th><th></th></tr>"
        + "".join(crows) + "</table>"
    ) if crows else "<p>none staged</p>"

    nrows = []
    for r in store.list_notices()[-25:][::-1]:
        fields = json.loads(r["fields_json"])
        missing = json.loads(r["missing_json"])
        detail = ", ".join(f"{_esc(k)}={_esc(v)}" for k, v in fields.items())
        if missing:
            detail += f' <span class="f">MISSING: {_esc(", ".join(missing))}</span>'
        nrows.append(
            f'<tr><td>{r["message_id"]}</td><td>{_esc(r["notice_type"])}</td>'
            f'<td>{_esc(r["adapter"])}</td><td>{detail}</td></tr>'
        )
    notices_html = (
        "<table><tr><th>msg</th><th>type</th><th>adapter</th><th>fields</th></tr>"
        + "".join(nrows) + "</table>"
    ) if nrows else "<p>none parsed yet</p>"

    return _PAGE.format(counts=counts_html, queue=queue_html,
                        classq=class_html, notices=notices_html)




_ADAPTERS = """<!doctype html><html><head><meta charset="utf-8">
<title>Docketry — court adapters</title><style>
body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;padding:0 1rem;color:#1a1a1a;background:#fafaf7}}
h1{{font-size:1.3rem}} h2{{font-size:1.05rem;margin-top:1.6rem;border-bottom:1px solid #ddd;padding-bottom:.3rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}} td,th{{text-align:left;padding:.35rem .5rem;border-bottom:1px solid #eee;vertical-align:top}}
textarea{{width:100%;height:14rem;font-family:ui-monospace,monospace;font-size:.8rem}}
input[type=text]{{font-family:ui-monospace,monospace;font-size:.85rem;padding:.15rem .3rem}}
.f{{color:#a11;font-weight:600}} .ok{{color:#161}} .hint{{color:#666;font-size:.85rem}}
code{{background:#f0efe9;padding:.05rem .25rem;border-radius:.2rem;font-size:.8rem}}
button{{cursor:pointer;padding:.35rem .8rem}}
</style></head><body>
<h1>Court adapters</h1>
<p class="hint">Docketry reads the five big systems out of the box. For any other
court, paste one of its emails below — Docketry finds the fields, shows you what
it would pull out of <em>this</em> message, and only saves once that works.
You never write a regular expression.</p>
<p><a href="/">&larr; back to the queue</a></p>
{message}
<h2>Your adapters</h2>{existing}
<h2>Add a court</h2>{form}
</body></html>"""


def _adapters_path(home: Path) -> Path:
    return home / "adapters.toml"


def _existing_adapters(home: Path) -> str:
    from .notices import AdapterError, load_adapters_file
    path = _adapters_path(home)
    if not path.exists():
        return ('<p class="hint">None yet — only the built-in adapters are'
                " active.</p>")
    try:
        adapters = load_adapters_file(path)
    except AdapterError as e:
        return f'<p class="f">adapters.toml is not loading: {_esc(e)}</p>'
    if not adapters:
        return '<p class="hint">The file is there but defines no adapters.</p>'
    rows = "".join(
        f"<tr><td><code>{_esc(a.name)}</code></td><td>{_esc(a.notice_type)}</td>"
        f"<td>{_esc(', '.join(a.fields))}</td>"
        f"<td>{_esc(', '.join(a.required)) or '—'}</td></tr>"
        for a in adapters
    )
    return ("<table><tr><th>Name</th><th>Type</th><th>Fields</th>"
            f"<th>Required</th></tr>{rows}</table>")


def _paste_form(token: str, sample: str = "") -> str:
    return (
        '<form method="post" action="/adapters/scan">'
        f'<input type="hidden" name="token" value="{_esc(token)}">'
        f"<textarea name=\"sample\" placeholder=\"Paste the whole email, headers"
        ' and all.">' + _esc(sample) + "</textarea>"
        '<p><button type="submit">Find the fields</button></p></form>'
    )


def _candidate_form(token: str, sample: str, env, cands, suggested: dict) -> str:
    from .notices import NOTICE_TYPES
    if not cands:
        return ('<p class="f">No labelled fields found in that message.</p>'
                '<p class="hint">Docketry looks for lines like'
                " <code>Case Number: 2026-CA-000123</code>. If this court"
                " formats its notices differently, that is worth an issue —"
                " the pattern may be one Docketry should learn.</p>"
                + _paste_form(token, sample))
    rows = "".join(
        f'<tr><td><input type="checkbox" name="use" value="{i}" checked></td>'
        f"<td>{_esc(c.label)}</td>"
        f'<td><input type="text" name="field_{i}" value="{_esc(c.field)}" size="18"></td>'
        f"<td>{_esc(c.value)}</td>"
        f'<td style="text-align:center"><input type="checkbox" name="req" value="{i}"'
        f'{" checked" if c.known and "date" in c.field else ""}></td></tr>'
        for i, c in enumerate(cands)
    )
    chosen = suggested.get("notice_type", "service_notice")
    opts = "".join(
        f'<option value="{t}"{" selected" if t == chosen else ""}>{t}</option>'
        for t in NOTICE_TYPES)
    return (
        '<form method="post" action="/adapters/save">'
        f'<input type="hidden" name="token" value="{_esc(token)}">'
        f'<input type="hidden" name="sample" value="{_esc(sample)}">'
        '<p class="hint">Found in this message. Untick anything that is not a'
        " field. <strong>Required</strong> means a message that arrives without"
        " it is held for review instead of being filed on partial"
        " information.</p>"
        "<table><tr><th>Use</th><th>Label</th><th>Field name</th>"
        "<th>Value in this email</th><th>Required</th></tr>"
        f"{rows}</table>"
        f'<p>Name <input type="text" name="name" size="28" value="{_esc(suggested["name"])}">'
        f' &nbsp; Type <select name="notice_type">{opts}</select></p>'
        f'<p>Match sender ending <input type="text" name="from" size="26"'
        f' value="{_esc(suggested["from"])}"> &nbsp;'
        f' subject contains <input type="text" name="subject_contains" size="30"'
        f' value="{_esc(suggested["subject_contains"])}"></p>'
        '<p><button type="submit">Test against this email, then save</button></p>'
        "</form>"
    )


def make_server(store_path, pipeline, host="127.0.0.1", port=8642, home=None):
    # adapters.toml lives in the Docketry home, beside the store.
    home = Path(home) if home else Path(store_path).parent
    if host != "127.0.0.1":
        raise ValueError("the review UI is local-only; refusing to bind " + host)
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _store(self) -> Store:
            return Store(store_path)

        def _html(self, body: str, code: int = 200):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self):
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def _adapters_page(self, message="", form=None):
            self._html(_ADAPTERS.format(
                message=message,
                existing=_existing_adapters(home),
                form=form if form is not None else _paste_form(token),
            ))

        def do_GET(self):
            if self.path in ("/adapters", "/adapters/"):
                self._adapters_page()
                return
            if self.path not in ("/", ""):
                self._html("not found", 404)
                return
            store = self._store()
            try:
                self._html(_render(store, pipeline, token))
            finally:
                store.close()


        def _save_adapter(self, form):
            """Build it, run it against the pasted email, save only if it works."""
            from .adapter_builder import build, scan, scan_email, to_toml
            from .notices import AdapterError, load_adapters_file

            sample = form.get("sample", "")
            try:
                env, cands = scan_email(sample.encode())
            except Exception:
                self._adapters_page('<p class="f">That sample could not be read'
                                    " as an email.</p>")
                return
            use = set(self._multi.get("use", []))
            req_idx = set(self._multi.get("req", []))
            fields, required = {}, []
            for i, c in enumerate(cands):
                if str(i) not in use:
                    continue
                fname = (form.get(f"field_{i}") or c.field).strip()
                if not fname:
                    continue
                fields[fname] = c.pattern
                if str(i) in req_idx:
                    required.append(fname)

            name = form.get("name", "").strip()
            notice_type = form.get("notice_type", "").strip()
            match = {"from": form.get("from", "").strip(),
                     "subject_contains": form.get("subject_contains", "").strip()}
            try:
                adapter = build(name, notice_type, match, fields, required)
            except AdapterError as e:
                self._adapters_page(f'<p class="f">Not saved: {_esc(e)}</p>')
                return

            # The proof: the real parser, on the email you pasted.
            if not adapter.match(env):
                self._adapters_page(
                    '<p class="f">Not saved: with those match rules this adapter'
                    " would not recognise the email you just pasted. Widen the"
                    " sender or subject rule.</p>")
                return
            result = adapter.extract(env)
            if result.missing:
                self._adapters_page(
                    '<p class="f">Not saved: marked required but not found in'
                    f' this email: {_esc(", ".join(result.missing))}.</p>')
                return

            path = _adapters_path(home)
            before = path.read_text() if path.exists() else ""
            block = to_toml(name, notice_type, match, fields, required)
            path.write_text(before + block)
            try:
                load_adapters_file(path)     # never leave a file that will not load
            except AdapterError as e:
                path.write_text(before)
                self._adapters_page(f'<p class="f">Not saved — the file would not'
                                    f" load afterwards: {_esc(e)}</p>")
                return
            extracted = ", ".join(f"{k}={v}" for k, v in result.fields.items())
            self._adapters_page(
                f'<p class="ok">Saved <code>{_esc(name)}</code>. From the email'
                f" you pasted it read: {_esc(extracted)}.</p>"
                '<p class="hint">It takes effect on the next'
                " <code>docketry poll</code>.</p>")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            parsed = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
            self._multi = parsed                      # checkbox groups
            form = {k: v[0] for k, v in parsed.items()}
            if form.get("token") != token:
                self._html("bad token", 403)
                return
            store = self._store()
            try:
                if self.path == "/approve":
                    msg_id = int(form["message"])
                    row = store.get_message(msg_id)
                    bindings = {b.gate.id: b for b in pipeline.bindings_for(row["stage"])}
                    binding = bindings.get(form.get("gate", ""))
                    if row is None or binding is None or form.get("role") != binding.authority:
                        self._html("approval refused: unknown gate or wrong role", 400)
                        return
                    if not form.get("by", "").strip():
                        self._html("approval refused: approver name required", 400)
                        return
                    store.add_approval(msg_id, row["stage"], binding.gate.id,
                                       approved_by=form["by"].strip(),
                                       role=binding.authority, note="via local ui")
                    runner = Runner(pipeline, store)
                    try:
                        status = runner.advance(msg_id)
                        while status == st.OK:
                            status = runner.advance(msg_id)
                    except Exception:
                        pass  # still held by another gate; queue shows it
                    self._redirect()
                elif self.path == "/class-apply":
                    if not form.get("by", "").strip() or not form.get("role", "").strip():
                        self._html("refused: name and role required", 400)
                        return
                    store.apply_classification(int(form["id"]),
                                               by=form["by"].strip(),
                                               role=form["role"].strip())
                    self._redirect()
                elif self.path == "/adapters/scan":
                    from .adapter_builder import (
                        scan, scan_email, suggest_match, suggest_type)
                    sample = form.get("sample", "")
                    if not sample.strip():
                        self._adapters_page('<p class="f">Paste an email first.</p>')
                        return
                    try:
                        env, cands = scan_email(sample.encode())
                    except Exception:
                        env, cands = None, scan(sample)
                    suggested = (suggest_match(env) if env is not None
                                 else {"from": "", "subject_contains": ""})
                    suggested["notice_type"] = (
                        suggest_type(env) if env is not None else "service_notice")
                    domain = suggested.get("from", "").lstrip("@").split(".")[0]
                    kind = suggested["notice_type"].split("_")[0]
                    suggested["name"] = f"{domain}-{kind}" if domain else "local-court"
                    self._adapters_page(
                        form=_candidate_form(token, sample, env, cands, suggested))
                elif self.path == "/adapters/save":
                    self._save_adapter(form)
                else:
                    self._html("not found", 404)
            finally:
                store.close()

    return ThreadingHTTPServer((host, port), Handler)
