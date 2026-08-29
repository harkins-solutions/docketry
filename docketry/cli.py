"""Docketry CLI: init, poll, queue, approve, advance, status."""
from __future__ import annotations

import argparse
import os as _os

def _color(code: str, s: str) -> str:
    if not sys.stdout.isatty() or _os.environ.get("NO_COLOR"):
        return s
    return f"\033[{code}m{s}\033[0m"

def _sev(marker: str) -> str:
    return {"FAIL": _color("31;1", "FAIL"), "ERROR": _color("31;1", "ERROR"),
            "warn": _color("33", "warn"), "WARN": _color("33", "WARN"),
            "  ok": _color("32", "  ok")}.get(marker, marker)
import getpass
import json
import sys
from pathlib import Path

from . import __version__
from . import store as st
from .config import MANIFEST_NAME, load_home, write_config
from .envelope import parse_message
from .mailbox import IntakeMailbox
from .manifest import DEFAULT_MANIFEST, load_manifest
from . import notices as notices_mod
from .pipeline import GateRefusal, Runner
from .store import Store


def _open(home: str):
    cfg = load_home(home)
    if not cfg.manifest_path.exists():
        sys.exit(f"no {MANIFEST_NAME} in {home} — run: docketry init")
    pipeline = load_manifest(cfg.manifest_path)
    return cfg, pipeline, Store(cfg.store_path)


def cmd_init(args) -> None:
    home = Path(args.home)
    host = args.host or input("IMAP host of the intake mailbox (e.g. imap.gmail.com): ").strip()
    user = args.user or input("Intake mailbox address (e.g. intake@yourfirm.com): ").strip()
    folder = args.folder or "INBOX"
    password = None
    if args.store_password:
        password = getpass.getpass("Mailbox password/app password (stored 0600): ")
    write_config(home, host=host, user=user, folder=folder, password=password)
    manifest = home / MANIFEST_NAME
    if not manifest.exists():
        manifest.write_text(DEFAULT_MANIFEST)
    load_manifest(manifest)  # refuse to init half-configured
    print(f"initialized {home}")
    print(f"  config:    {home / 'config.toml'}")
    print(f"  manifest:  {manifest}")
    if not password:
        print("  password:  set DOCKETRY_IMAP_PASSWORD in the environment")
    print("next: point a forwarding rule at the intake mailbox, then run: docketry poll")


def cmd_poll(args) -> None:
    cfg, pipeline, store = _open(args.home)
    if cfg.mailbox is None:
        sys.exit("no [mailbox] configured — run: docketry init")
    if not cfg.mailbox.password:
        sys.exit("no mailbox password (set DOCKETRY_IMAP_PASSWORD)")
    runner = Runner(pipeline, store)
    first_stage = pipeline.stages[0]
    adapters_file = cfg.home / "adapters.toml"
    adapter_stack = notices_mod.stack(adapters_file if adapters_file.exists() else None)
    ingested = held = parsed = 0
    with IntakeMailbox(cfg.mailbox) as mb:
        uidvalidity, last_uid = store.imap_cursor(mb.label)
        current_validity = mb.uidvalidity()
        if uidvalidity is not None and current_validity != uidvalidity:
            print("mailbox UIDVALIDITY changed; resweeping from the start (dedupe is by content hash)")
            last_uid = 0
        max_uid = last_uid
        for uid, raw in mb.new_messages(last_uid):
            env = parse_message(raw, source=mb.label, fetched_at=st.utcnow())
            msg_id = store.ingest(env, first_stage=first_stage)
            max_uid = max(max_uid, uid)
            if msg_id is None:
                continue
            ingested += 1
            from .classify import classify as _classify
            for att in store.attachments_for(msg_id):
                label, tier = _classify(att["filename"])
                if tier != "low":
                    store.stage_classification(att["id"], label, tier)
            result = notices_mod.parse(env, adapter_stack)
            if result is not None:
                store.add_notice(msg_id, result.adapter, result.notice_type,
                                 result.fields, result.missing)
                parsed += 1
            status = runner.enter(msg_id)
            while status == st.OK:
                status = runner.advance(msg_id)
            if status in (st.PENDING_REVIEW, st.BLOCKED):
                held += 1
        store.set_imap_cursor(mb.label, current_validity, max_uid)
    print(f"ingested {ingested} new message(s); {parsed} parsed as court notices; {held} held for review")
    if held:
        print("run: docketry queue")


def _print_row(store: Store, row) -> None:
    env = json.loads(row["envelope_json"])
    print(f"[{row['id']}] {row['status']} @ {row['stage']}  {env['subject']!r}  from {env['from_addr']}")
    for f in store.findings_for(row["id"]):
        if f["severity"] != "info":
            print(f"     {f['severity']}: ({f['gate_id']}) {f['summary']}")


def cmd_queue(args) -> None:
    _, _, store = _open(args.home)
    rows = store.list_by_status(st.PENDING_REVIEW) + store.list_by_status(st.BLOCKED)
    if not rows:
        print("queue empty")
        return
    for row in rows:
        _print_row(store, row)


def cmd_approve(args) -> None:
    cfg, pipeline, store = _open(args.home)
    row = store.get_message(args.message)
    if row is None:
        sys.exit(f"no message {args.message}")
    stage_bindings = {b.gate.id: b for b in pipeline.bindings_for(row["stage"])}
    binding = stage_bindings.get(args.gate)
    if binding is None:
        sys.exit(f"gate '{args.gate}' is not bound at stage '{row['stage']}'"
                 f" (bound here: {', '.join(sorted(stage_bindings)) or 'none'})")
    if args.role != binding.authority:
        sys.exit(f"gate '{args.gate}' at stage '{row['stage']}' requires role"
                 f" '{binding.authority}', not '{args.role}' — approval not recorded")
    store.add_approval(
        args.message, row["stage"], args.gate,
        approved_by=args.by, role=args.role, note=args.note or "",
    )
    runner = Runner(pipeline, store)
    try:
        status = runner.advance(args.message)
        print(f"approved; message {args.message} -> {status}")
    except GateRefusal as e:
        print(f"approval recorded, but: {e}")


def cmd_advance(args) -> None:
    _, pipeline, store = _open(args.home)
    runner = Runner(pipeline, store)
    try:
        status = runner.advance(args.message)
        print(f"message {args.message} -> {status}")
    except GateRefusal as e:
        sys.exit(str(e))


def cmd_notices(args) -> None:
    _, _, store = _open(args.home)
    rows = store.list_notices(args.type)
    if not rows:
        print("no notices parsed yet")
        return
    for r in rows:
        fields = json.loads(r["fields_json"])
        missing = json.loads(r["missing_json"])
        line = f"[msg {r['message_id']}] {r['notice_type']} via {r['adapter']}: " + ", ".join(
            f"{k}={v}" for k, v in fields.items()
        )
        if missing:
            line += f"  MISSING: {', '.join(missing)}"
        print(line)



def _parse_box(spec: str):
    """page:x0,y0,x1,y1 — fractions of the page, top-left origin."""
    from .redact import Box, RedactionError
    try:
        page, rest = spec.split(":", 1)
        x0, y0, x1, y1 = (float(v) for v in rest.split(","))
    except ValueError:
        raise RedactionError(
            f"bad box {spec!r}; expected page:x0,y0,x1,y1 (e.g. 1:0.1,0.2,0.5,0.24)"
        ) from None
    return Box(page=int(page), x0=x0, y0=y0, x1=x1, y1=y1)


def cmd_redact_scan(args) -> None:
    """PREVIEW only. Writes nothing."""
    from .redact import RedactionError, find_terms
    try:
        hits = find_terms(args.file, args.term)
    except RedactionError as e:
        sys.exit(str(e))
    if not hits:
        print("no occurrences found — nothing would be redacted")
        return
    by_page: dict[int, int] = {}
    for h in hits:
        by_page[h.page] = by_page.get(h.page, 0) + 1
        print(f"  p{h.page}  {h.note!r}  at ({h.x0:.3f},{h.y0:.3f})-({h.x1:.3f},{h.y1:.3f})")
    pages = ", ".join(f"p{p} x{n}" for p, n in sorted(by_page.items()))
    print(f"\n{len(hits)} occurrence(s) on {len(by_page)} page(s): {pages}")
    print("PREVIEW ONLY — nothing written. Re-run as 'redact-apply' to write a copy.")


def cmd_redact_apply(args) -> None:
    from .redact import RedactionError, apply, find_terms
    boxes = []
    try:
        if args.term:
            boxes += find_terms(args.file, args.term)
        boxes += [_parse_box(b) for b in (args.box or [])]
        if not boxes:
            sys.exit("nothing to redact: give --term and/or --box")
        result = apply(args.file, boxes, args.out,
                       marker=None if args.no_marker else args.marker,
                       verify_terms=args.term or None)
    except RedactionError as e:
        sys.exit(str(e))

    print(f"wrote {result.out_path}")
    print(f"  rasterised: {result.pages_rasterised or 'none'}"
          f"   untouched: {result.pages_untouched or 'none'}")
    if result.page_confidence:
        conf = ", ".join(f"p{p} {c:.0f}" for p, c in sorted(result.page_confidence.items()))
        print(f"  rebuilt text layer confidence: {conf}")
    if result.words_removed:
        shown = result.words_removed[:8]
        more = "" if len(result.words_removed) <= 8 else f" (+{len(result.words_removed)-8} more)"
        print(f"  removed {len(result.words_removed)} word(s): {', '.join(shown)}{more}")
    for w in result.warnings:
        print(_sev("warn") + f"  {w}")
    if result.survivors:
        # Loud, and non-zero: this is the whole point of the check.
        print(_sev("FAIL") + "  these terms are STILL extractable from the output: "
              + ", ".join(result.survivors))
        print("  a term can survive because the burn missed it, or because the same"
              " word appears elsewhere in the document unmarked. Check before"
              " releasing this file.")
        sys.exit(1)
    if result.unverifiable:
        for u in result.unverifiable:
            print(_sev("warn") + f"  {u}")
        print(_sev("warn") + f"  {len(result.unverifiable)} box(es) could not be"
              " machine-verified — the content is destroyed, but only your eyes"
              " can confirm what was under them")
    if result.also_appears:
        print(_sev("warn") + "  redacted phrases still standing elsewhere in this"
              " document (often a missed second occurrence): "
              + ", ".join(result.also_appears))
    n_ok = len([b for b in boxes if b.kind == "redact"]) - len(result.unverifiable)
    if n_ok > 0:
        print(_sev("  ok") + f"  verified {n_ok} redaction(s): nothing they"
              " buried is readable inside them")


def cmd_redact_verify(args) -> None:
    from .redact import verify
    survivors = verify(args.file, args.term)
    if survivors:
        print(_sev("FAIL") + "  still extractable: " + ", ".join(survivors))
        sys.exit(1)
    print(_sev("  ok") + f"  none of the {len(args.term)} term(s) appear in {args.file}")



def _timeline(args):
    from .timeline import build
    _, _, store = _open(args.home)
    return build(store, args.case, threads=args.thread or None)


def cmd_timeline(args) -> None:
    from .timeline import LAYERS
    tl = _timeline(args)
    layers = tuple(args.layer) if args.layer else LAYERS
    rows = tl.sorted_entries(layers, thread=args.in_thread)
    if not rows:
        print(f"no entries for case {args.case}")
        return
    for e in rows:
        when = (e.when[:16].replace("T", " ") or "(undated)").ljust(16)
        num = f"#{e.doc_number}" if e.doc_number else ""
        print(f"  {when}  {e.layer[:6].ljust(6)}  {e.kind.ljust(8)} {num.ljust(5)}"
              f" {e.title[:70]}")
    print(f"\n{len(rows)} entry(ies) in {len(tl.threads())} thread(s)")
    for g in tl.gaps:
        print(_sev("warn") + f"  [{g['class']}] {g['detail']}")
    for f in tl.findings:
        print(_sev("warn") + f"  {f}")
    print("reconstructed from what this firm received — NOT the court's docket,"
          " and not a completeness claim")


def cmd_timeline_export(args) -> None:
    from .export import to_docx, to_xlsx
    from .timeline import LAYERS
    tl = _timeline(args)
    layers = tuple(args.layer) if args.layer else LAYERS
    out = Path(args.out)
    fn = to_xlsx if out.suffix.lower() == ".xlsx" else to_docx
    if out.suffix.lower() not in (".xlsx", ".docx"):
        sys.exit("output must end in .xlsx or .docx")
    try:
        fn(tl, out, layers=layers, thread=args.in_thread)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"wrote {out} ({len(tl.sorted_entries(layers, thread=args.in_thread))} rows)")


def cmd_docket_reconcile(args) -> None:
    from .reconcile import parse_docket, reconcile
    tl = _timeline(args)
    text = Path(args.docket).read_text(errors="replace")
    lines = parse_docket(text)
    if not lines:
        sys.exit("could not read any docket lines from that file —"
                 " expected a CSV with headers, or 'number date title' lines")
    rec = reconcile(tl, lines)
    print(f"pulled docket: {len(lines)} line(s); reconstruction:"
          f" {len([e for e in tl.entries if e.of_record])} record entry(ies)")
    print(f"  matched on document number: {len(rec.matched)}")
    for line in rec.only_on_docket:
        print(_sev("FAIL") + f"  on the docket, NOT here: "
              f"{('#' + str(line.doc_number) + ' ') if line.doc_number else ''}"
              f"{line.date} {line.title[:60]}")
    for e in rec.only_here:
        print(_sev("warn") + f"  here, NOT on the docket: {e.when[:10]}"
              f" {e.title[:60]}")
    for line, e in rec.to_confirm:
        print(_sev("warn") + f"  probable match, CONFIRM BY HAND: docket"
              f" '{line.title[:40]}' <-> ours '{e.title[:40]}'")
    if rec.clean:
        print(_sev("  ok") + "  every record entry is accounted for in both"
              " directions")
    else:
        sys.exit(1)



def cmd_llm_check(args) -> None:
    """Is a local model configured, reachable, and actually local?"""
    from .llm import probe
    cfg, _, _ = _open(args.home)
    if cfg.llm is None:
        print("no model configured — Docketry works fully without one")
        print("to add a local model, put this in config.toml:")
        print('  [llm]\n  base_url = "http://127.0.0.1:11434"\n  model = "llama3.1"')
        return
    result = probe(cfg.llm)
    if result.startswith("REFUSED"):
        print(_sev("FAIL") + f"  {result}")
        sys.exit(1)
    if result.startswith("unreachable"):
        print(_sev("warn") + f"  {result}")
        sys.exit(1)
    print(_sev("  ok") + f"  {result}")
    print("  a model here proposes only — it never releases a hold, approves,"
          " classifies, or decides what to redact")


def cmd_verify_draft(args) -> None:
    from .cite import CiteError, verify, extract_citations
    from .extract import ExtractionError, extract_path

    try:
        text = extract_path(args.file).full_text
    except ExtractionError as e:
        sys.exit(str(e))
    try:
        if args.offline:
            raise CiteError("offline requested")
        from .cite_client import CourtListenerClient
        client = CourtListenerClient(token=args.token)
        report = verify(text, client)
        client.close()
    except CiteError as e:
        if not args.offline:
            print(f"network verification unavailable ({e}); extraction-only mode")
        try:
            from .cite import citation_inventory
            cites, n_short = citation_inventory(text)
        except CiteError as e2:
            sys.exit(str(e2))
        print(f"{len(cites)} full citation(s) found — NOT verified:")
        for c in cites:
            name = f"{c.plaintiff} v. {c.defendant}".strip(" v.")
            print(f"  {name}, {c.text}" + (f" (pin p. {c.pin_page})" if c.pin_page else ""))
        if n_short:
            print(f"plus {n_short} short-form citation(s)"
                  + (" — WITH NO FULL CITATION IN THIS DOCUMENT; unverifiable as written"
                     if not cites else " riding on the fulls above"))
        sys.exit(2)
    fails = [f for f in report.findings if f.severity == "fail"]
    warns = [f for f in report.findings if f.severity == "warn"]
    for f in report.findings:
        marker = _sev({"fail": "FAIL", "warn": "warn", "info": "  ok"}[f.severity])
        print(f"{marker}  [{f.check}] {f.summary}")
    print(f"\n{len(report.citations)} citation(s): {len(fails)} failed,"
          f" {len(warns)} warning(s)")
    if fails:
        sys.exit(1)


def cmd_lint(args) -> None:
    from .extract import ExtractionError, extract_path
    from .lint import RulepackError, lint, load_rulepack

    try:
        text = extract_path(args.file).full_text
    except ExtractionError as e:
        sys.exit(str(e))
    rulepack = None
    if args.rules:
        try:
            rulepack = load_rulepack(args.rules)
        except RulepackError as e:
            sys.exit(f"rulepack refused: {e}")
    findings = lint(text, rulepack)
    errors = [f for f in findings if f.severity == "error"]
    for f in findings:
        loc = f"line {f.line}" if f.line else "document"
        print(f"{_sev(f.severity.upper()):5} {loc:>10}  [{f.rule}] {f.message}")
        if f.excerpt:
            print(f"                  > {f.excerpt}")
    print(f"\n{len(findings)} finding(s), {len(errors)} error(s)")
    if errors:
        sys.exit(1)


def cmd_classify(args) -> None:
    from .classify import classify
    from .extract import ExtractionError, extract_path

    text = ""
    try:
        text = extract_path(args.file).full_text
    except ExtractionError:
        pass  # title-only classification still works
    label, tier = classify(Path(args.file).stem, text)
    print(f"{label} ({tier})")


def cmd_class_queue(args) -> None:
    _, _, store = _open(args.home)
    rows = store.open_classifications()
    if not rows:
        print("no staged classifications")
        return
    for r in rows:
        current = f" (current: {r['doc_type']})" if r["doc_type"] else ""
        print(f"[{r['id']}] {r['filename']} -> {r['label']} ({r['tier']}){current}")


def cmd_class_apply(args) -> None:
    _, _, store = _open(args.home)
    outcome = store.apply_classification(args.id, by=args.by, role=args.role)
    print(outcome)


def cmd_ui(args) -> None:
    from .webui import make_server
    cfg, pipeline, store = _open(args.home)
    store.close()
    server = make_server(cfg.store_path, pipeline, port=args.port)
    print(f"Docketry review UI: http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def cmd_watch(args) -> None:
    import time
    print(f"sweeping every {args.every}s (Ctrl-C to stop)")
    while True:
        try:
            cmd_poll(args)
        except SystemExit as e:
            raise
        except Exception as e:
            print(f"sweep failed: {e} — retrying in {args.every}s")
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            print("\nstopped")
            return


def cmd_doctor(args) -> None:
    import shutil
    from .config import load_home
    from .manifest import ManifestError, load_manifest as _lm

    ok = True

    def report(level, msg):
        nonlocal ok
        if level == "FAIL":
            ok = False
        print(f"{_sev(level if level != 'PASS' else '  ok'):5} {msg}")

    home = Path(args.home)
    if not home.exists():
        report("FAIL", f"home {home} does not exist — run: docketry init")
        sys.exit(1)
    report("PASS", f"home: {home}")
    cfg = load_home(home)
    if cfg.mailbox is None:
        report("WARN", "no [mailbox] configured (init with --host/--user to poll)")
    else:
        report("PASS", f"mailbox: {cfg.mailbox.user} @ {cfg.mailbox.host} ({cfg.mailbox.folder})")
        if not cfg.mailbox.password:
            report("WARN", "no mailbox password: set DOCKETRY_IMAP_PASSWORD")
    if cfg.manifest_path.exists():
        try:
            pipeline = _lm(cfg.manifest_path)
            report("PASS", f"manifest: stages {pipeline.stages}, "
                           f"{len(pipeline.bindings)} gate binding(s)")
        except ManifestError as e:
            report("FAIL", f"manifest refused: {e}")
    else:
        report("FAIL", f"no {cfg.manifest_path.name} — run: docketry init")
    adapters = home / "adapters.toml"
    if adapters.exists():
        from .notices import AdapterError, load_adapters_file
        try:
            n = len(load_adapters_file(adapters))
            report("PASS", f"firm adapters: {n} loaded")
        except AdapterError as e:
            report("FAIL", f"adapters.toml refused: {e}")
    else:
        report("PASS", "no firm adapters.toml (built-ins only)")
    for mod, extra, what in (("pypdf", "pdf", "PDF extraction"),
                             ("docx", "docx", "DOCX extraction"),
                             ("eyecite", "cite", "citation extraction"),
                             ("httpx", "cite", "citation verification")):
        try:
            __import__(mod)
            report("PASS", f"{what} available")
        except ImportError:
            report("WARN", f"{what} missing — pip install 'docketry[{extra}]'")
    for binary, pkg in (("tesseract", "tesseract-ocr"), ("pdftoppm", "poppler-utils")):
        if shutil.which(binary):
            report("PASS", f"{binary} present (OCR possible)")
        else:
            report("WARN", f"{binary} missing — scanned PDFs need {pkg}")
    if _os.environ.get("COURTLISTENER_TOKEN"):
        report("PASS", "COURTLISTENER_TOKEN set (live citation verification)")
    else:
        report("WARN", "COURTLISTENER_TOKEN not set — verify-draft runs extraction-only")
    # Say plainly whether anything here can reach off this network. A promise
    # the operator can check beats one they have to take on faith.
    if cfg.llm is not None:
        from .llm import probe
        result = probe(cfg.llm)
        if result.startswith("REFUSED"):
            report("FAIL", f"model endpoint is NOT local — {result}")
        elif result.startswith("unreachable"):
            report("WARN", f"local model configured but {result}")
        else:
            report("PASS", f"local model {result} — proposals only, nothing leaves this network")
    else:
        report("PASS", "no model configured — nothing is sent anywhere")
    sys.exit(0 if ok else 1)


def cmd_stats(args) -> None:
    _, _, store = _open(args.home)
    s = store.stats(days=args.days)
    if args.json:
        print(json.dumps(s, indent=2))
        return
    print(f"last {s['days']} day(s): {s['ingested']} message(s) ingested")
    for k, v in sorted(s["by_status"].items()):
        print(f"  {k:15} {v}")
    if s["holds_by_gate"]:
        print("holds by gate:")
        for g, n in s["holds_by_gate"].items():
            print(f"  {g:20} {n}")
    if s["notices_by_type"]:
        print("court notices:")
        for t, n in sorted(s["notices_by_type"].items()):
            print(f"  {t:20} {n}")
    if s["template_drift_messages"]:
        print(f"TEMPLATE DRIFT: {s['template_drift_messages']} message(s) — a court"
              " system may have changed its email format")
    if s["avg_hours_to_release"] is not None:
        print(f"avg hours held before release: {s['avg_hours_to_release']}")
    if s["approvals_by_role"]:
        print("approvals recorded (audit): "
              + ", ".join(f"{r}={n}" for r, n in sorted(s["approvals_by_role"].items())))
    if s["classifications"]:
        print("doc-type proposals: "
              + ", ".join(f"{k}={n}" for k, n in sorted(s["classifications"].items())))


def cmd_digest(args) -> None:
    """Prints a paste-anywhere summary. Docketry never sends anything."""
    _, _, store = _open(args.home)
    s = store.stats(days=1)
    held = store.list_by_status("pending_review") + store.list_by_status("blocked")
    lines = [f"Docketry intake digest — {len(held)} awaiting review,"
             f" {s['ingested']} ingested in the last day"]
    for row in held[:15]:
        env = json.loads(row["envelope_json"])
        gates = sorted({f["gate_id"] for f in store.findings_for(row["id"])
                        if f["severity"] == "fail"})
        lines.append(f"  [{row['id']}] {env['subject']!r} from {env['from_addr']}"
                     f" — held by {', '.join(gates) or 'unknown'}")
    if s["template_drift_messages"]:
        lines.append(f"  NOTE: {s['template_drift_messages']} template-drift event(s)"
                     " — check portal formats")
    print("\n".join(lines))


def cmd_demo(args) -> None:
    """Seed a disposable home with sample traffic and open the dashboard."""
    import tempfile
    import webbrowser
    from email.message import EmailMessage

    from . import notices as nmod
    from .classify import classify as _classify
    from .envelope import parse_message
    from .manifest import load_manifest
    from .pipeline import Runner
    from .store import Store
    from .webui import make_server

    home = Path(tempfile.mkdtemp(prefix="docketry-demo-"))
    (home / "guardrails.toml").write_text(DEMO_MANIFEST)
    pipeline = load_manifest(home / "guardrails.toml")
    store = Store(home / "store")
    runner = Runner(pipeline, store)
    adapter_stack = nmod.stack()

    def mail(from_addr, subject, body, attach=None):
        m = EmailMessage()
        m["From"] = from_addr
        m["To"] = "intake@demofirm.example"
        m["Subject"] = subject
        m.set_content(body)
        if attach:
            m.add_attachment(b"%PDF-1.4 demo", maintype="application",
                             subtype="pdf", filename=attach)
        return bytes(m)

    samples = [
        mail("eservice@myflcourtaccess.com", "SERVICE OF COURT DOCUMENT",
             "Case Number: 562026CA000123\nCase Style: DOE v. ACME INSURANCE\n"
             "Document: Motion for Summary Judgment\nServed: you@demofirm.example\n",
             attach="Motion for Summary Judgment.pdf"),
        mail("ecf_bounces@flsd.uscourts.gov",
             "Activity in Case 2:26-cv-00123-XYZ Doe v. Acme Order on Motion",
             "Document Number: 45\nDocket Text: ORDER granting in part.\n"
             "https://ecf.flsd.uscourts.gov/doc1/demo (one-time free look —"
             " captured, never fetched)\n"),
        mail("ja@circuit19.example", "Hearing Scheduled",
             "Judicial Automated Calendaring System\nCase Number: 562026CA000123\n"
             "Hearing Date: 09/15/2026\nTime: 10:30 AM\nJudge: Hon. Demo Judge\n"),
        mail("eservice@myflcourtaccess.com", "SERVICE OF COURT DOCUMENT",
             "a redesigned portal template this adapter has never seen\n"),
        mail("stranger@sketchy.example", "Invoice attached — pay today",
             "wire transfer please", attach="invoice.pdf"),
    ]
    for raw in samples:
        env = parse_message(raw, source="demo", fetched_at=st.utcnow())
        msg_id = store.ingest(env, first_stage=pipeline.stages[0])
        for att in store.attachments_for(msg_id):
            label, tier = _classify(att["filename"])
            if tier != "low":
                store.stage_classification(att["id"], label, tier)
        res = nmod.parse(env, adapter_stack)
        if res is not None:
            store.add_notice(msg_id, res.adapter, res.notice_type,
                             res.fields, res.missing)
        status = runner.enter(msg_id)
        while status == st.OK:
            status = runner.advance(msg_id)
    store.close()

    server = make_server(home / "store", pipeline, port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Demo home: {home} (disposable)")
    print(f"Two messages passed clean; three are held — a drifted portal template,")
    print(f"an unknown sender, and its attachment. Release them from the dashboard:")
    print(f"  {url}   (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndemo stopped; nothing was saved outside", home)


DEMO_MANIFEST = """\
[pipeline]
stages = ["ingest", "review"]

[[gate]]
id = "sender-scope"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[gate.options]
allow = ["@myflcourtaccess.com", "@uscourts.gov", "@circuit19.example"]

[[gate]]
id = "attachment-policy"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[[gate]]
id = "notice-parser"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[[gate]]
id = "provenance-stamp"
binds_to = ["ingest"]
on_fail = "warn"
authority = "paralegal"
"""


def cmd_status(args) -> None:
    _, _, store = _open(args.home)
    counts = store.counts()
    if not counts:
        print("no messages yet")
        return
    for status, n in sorted(counts.items()):
        print(f"{status:15} {n}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="docketry", description="Local gate-enforced email port")
    p.add_argument("--home", default="./docketry-home", help="installation directory")
    p.add_argument("--version", action="version", version=f"docketry {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="set up a Docketry home directory")
    sp.add_argument("--host")
    sp.add_argument("--user")
    sp.add_argument("--folder")
    sp.add_argument("--store-password", action="store_true",
                    help="prompt for the password and store it in config.toml (0600)")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("poll", help="sweep the intake mailbox once")
    sp.set_defaults(fn=cmd_poll)

    sp = sub.add_parser("queue", help="list messages held for review")
    sp.set_defaults(fn=cmd_queue)

    sp = sub.add_parser("approve", help="record an authority approval and release a hold")
    sp.add_argument("message", type=int)
    sp.add_argument("--gate", required=True)
    sp.add_argument("--by", required=True, help="name of the approving person")
    sp.add_argument("--role", required=True, help="role granted the authority in the manifest")
    sp.add_argument("--note")
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("advance", help="move a message one stage forward")
    sp.add_argument("message", type=int)
    sp.set_defaults(fn=cmd_advance)

    sp = sub.add_parser("notices", help="list parsed court notices")
    sp.add_argument("--type", choices=["service_notice", "filing_receipt", "hearing_notice"])
    sp.set_defaults(fn=cmd_notices)

    def _tl_args(sp):
        sp.add_argument("case", help="case number, in any of its formats")
        sp.add_argument("--thread", action="append",
                        help="thread key to attach as correspondence; repeatable")
        sp.add_argument("--layer", action="append",
                        choices=["record", "correspondence", "client", "derived"],
                        help="restrict to these layers; repeatable")
        sp.add_argument("--in-thread", help="only entries in this thread")
        return sp

    sp = sub.add_parser("llm-check", help="is a local model configured, reachable, and local")
    sp.set_defaults(fn=cmd_llm_check)

    sp = _tl_args(sub.add_parser("timeline", help="the case as this firm received it"))
    sp.set_defaults(fn=cmd_timeline)

    sp = _tl_args(sub.add_parser("timeline-export", help="write the timeline to .docx or .xlsx"))
    sp.add_argument("out", help="output path ending .docx or .xlsx")
    sp.set_defaults(fn=cmd_timeline_export)

    sp = _tl_args(sub.add_parser("docket-reconcile", help="diff the reconstruction against a pulled docket"))
    sp.add_argument("docket", help="docket a human exported/pasted (CSV or text)")
    sp.set_defaults(fn=cmd_docket_reconcile)

    sp = sub.add_parser("redact-scan", help="PREVIEW where terms appear in a PDF (writes nothing)")
    sp.add_argument("file")
    sp.add_argument("--term", action="append", required=True,
                    help="term to find; repeatable")
    sp.set_defaults(fn=cmd_redact_scan)

    sp = sub.add_parser("redact-apply", help="write a redacted copy: text removed, page still searchable")
    sp.add_argument("file")
    sp.add_argument("out", help="output path (the source is never modified)")
    sp.add_argument("--term", action="append", help="redact every occurrence; repeatable")
    sp.add_argument("--box", action="append",
                    help="page:x0,y0,x1,y1 as page fractions; repeatable")
    sp.add_argument("--marker", default="[REDACTED]",
                    help="text placed in each bar and in the text layer")
    sp.add_argument("--no-marker", action="store_true",
                    help="leave the bar unlabelled and the text layer silent")
    sp.set_defaults(fn=cmd_redact_apply)

    sp = sub.add_parser("redact-verify", help="check a finished file for terms that survived")
    sp.add_argument("file")
    sp.add_argument("--term", action="append", required=True)
    sp.set_defaults(fn=cmd_redact_verify)

    sp = sub.add_parser("verify-draft", help="verify every citation in a draft (exists/name/quote/pin)")
    sp.add_argument("file", help="draft file (.docx, .pdf, .txt)")
    sp.add_argument("--token", help="CourtListener API token (or COURTLISTENER_TOKEN env)")
    sp.add_argument("--offline", action="store_true", help="extraction-only, no network")
    sp.set_defaults(fn=cmd_verify_draft)

    sp = sub.add_parser("lint", help="deterministic writing checks for a litigation draft")
    sp.add_argument("file", help="draft file (.docx, .pdf, .txt)")
    sp.add_argument("--rules", help="firm rulepack (TOML)")
    sp.set_defaults(fn=cmd_lint)

    sp = sub.add_parser("classify", help="classify one document (deterministic, proposes only)")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_classify)

    sp = sub.add_parser("class-queue", help="list staged doc-type proposals")
    sp.set_defaults(fn=cmd_class_queue)

    sp = sub.add_parser("class-apply", help="apply a staged proposal (fill-only)")
    sp.add_argument("id", type=int)
    sp.add_argument("--by", required=True)
    sp.add_argument("--role", required=True)
    sp.set_defaults(fn=cmd_class_apply)

    sp = sub.add_parser("ui", help="local review dashboard (127.0.0.1 only)")
    sp.add_argument("--port", type=int, default=8642)
    sp.set_defaults(fn=cmd_ui)

    sp = sub.add_parser("watch", help="sweep the intake mailbox on a loop")
    sp.add_argument("--every", type=int, default=300, help="seconds between sweeps")
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("doctor", help="check the installation and say what is missing")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("stats", help="queue-health stats (volumes, holds, drift, latency)")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_stats)

    sp = sub.add_parser("digest", help="print a paste-anywhere intake summary (never sends)")
    sp.set_defaults(fn=cmd_digest)

    sp = sub.add_parser("demo", help="seed sample traffic and open the dashboard (no mailbox needed)")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(fn=cmd_demo)

    sp = sub.add_parser("status", help="message counts by status")
    sp.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
