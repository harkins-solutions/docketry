"""Portico CLI: init, poll, queue, approve, advance, status."""
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
        sys.exit(f"no {MANIFEST_NAME} in {home} — run: portico init")
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
        print("  password:  set PORTICO_IMAP_PASSWORD in the environment")
    print("next: point a forwarding rule at the intake mailbox, then run: portico poll")


def cmd_poll(args) -> None:
    cfg, pipeline, store = _open(args.home)
    if cfg.mailbox is None:
        sys.exit("no [mailbox] configured — run: portico init")
    if not cfg.mailbox.password:
        sys.exit("no mailbox password (set PORTICO_IMAP_PASSWORD)")
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
        print("run: portico queue")


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
    print(f"Portico review UI: http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
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
        report("FAIL", f"home {home} does not exist — run: portico init")
        sys.exit(1)
    report("PASS", f"home: {home}")
    cfg = load_home(home)
    if cfg.mailbox is None:
        report("WARN", "no [mailbox] configured (init with --host/--user to poll)")
    else:
        report("PASS", f"mailbox: {cfg.mailbox.user} @ {cfg.mailbox.host} ({cfg.mailbox.folder})")
        if not cfg.mailbox.password:
            report("WARN", "no mailbox password: set PORTICO_IMAP_PASSWORD")
    if cfg.manifest_path.exists():
        try:
            pipeline = _lm(cfg.manifest_path)
            report("PASS", f"manifest: stages {pipeline.stages}, "
                           f"{len(pipeline.bindings)} gate binding(s)")
        except ManifestError as e:
            report("FAIL", f"manifest refused: {e}")
    else:
        report("FAIL", f"no {cfg.manifest_path.name} — run: portico init")
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
            report("WARN", f"{what} missing — pip install 'portico-legal[{extra}]'")
    for binary, pkg in (("tesseract", "tesseract-ocr"), ("pdftoppm", "poppler-utils")):
        if shutil.which(binary):
            report("PASS", f"{binary} present (OCR possible)")
        else:
            report("WARN", f"{binary} missing — scanned PDFs need {pkg}")
    if _os.environ.get("COURTLISTENER_TOKEN"):
        report("PASS", "COURTLISTENER_TOKEN set (live citation verification)")
    else:
        report("WARN", "COURTLISTENER_TOKEN not set — verify-draft runs extraction-only")
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
    """Prints a paste-anywhere summary. Portico never sends anything."""
    _, _, store = _open(args.home)
    s = store.stats(days=1)
    held = store.list_by_status("pending_review") + store.list_by_status("blocked")
    lines = [f"Portico intake digest — {len(held)} awaiting review,"
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


def cmd_status(args) -> None:
    _, _, store = _open(args.home)
    counts = store.counts()
    if not counts:
        print("no messages yet")
        return
    for status, n in sorted(counts.items()):
        print(f"{status:15} {n}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="portico", description="Local gate-enforced email port")
    p.add_argument("--home", default="./portico-home", help="installation directory")
    p.add_argument("--version", action="version", version=f"portico {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="set up a Portico home directory")
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

    sp = sub.add_parser("status", help="message counts by status")
    sp.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
