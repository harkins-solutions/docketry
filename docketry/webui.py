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


def make_server(store_path, pipeline, host="127.0.0.1", port=8642):
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

        def do_GET(self):
            if self.path not in ("/", ""):
                self._html("not found", 404)
                return
            store = self._store()
            try:
                self._html(_render(store, pipeline, token))
            finally:
                store.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
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
                else:
                    self._html("not found", 404)
            finally:
                store.close()

    return ThreadingHTTPServer((host, port), Handler)
