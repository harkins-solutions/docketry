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
import tomllib
from pathlib import Path

from . import __version__
from .core import store as st
from .core.config import MANIFEST_NAME, load_home, write_config
from .core.envelope import parse_message
from .core.mailbox import IntakeMailbox
from .core.manifest import DEFAULT_MANIFEST, load_manifest
from .tools import notices as notices_mod
from .core.pipeline import GateRefusal, Runner
from .core.store import Store
from .core import gates as gates_mod


def _registry(home):
    """The firm's role registry, when it has written one."""
    from .core.roles import RoleError, load_if_present
    try:
        return load_if_present(home)
    except RoleError as e:
        sys.exit(f"roles.toml refused: {e}")


def _directory(home, registry=None):
    """The firm's contacts directory, when it has written one."""
    from .tools.contacts import ContactError, load_if_present
    try:
        return load_if_present(home, registry)
    except ContactError as e:
        sys.exit(f"contacts.toml refused: {e}")


def _load_gates(home) -> None:
    """Load <home>/gates/*.py and any installed entry-point gates.

    Runs before the manifest, which refuses to load if it binds a gate that
    is not registered. A file that fails to load exits with its name rather
    than being skipped.
    """
    from .core.gates import GateLoadError, load_home as _home_gates, load_installed
    try:
        _home_gates(home)
        load_installed()
    except GateLoadError as e:
        sys.exit(f"gate refused: {e}")


def _open(home: str):
    cfg = load_home(home)
    if not cfg.manifest_path.exists():
        sys.exit(f"no {MANIFEST_NAME} in {home} — run: docketry init")
    _load_gates(cfg.home)
    registry = _registry(cfg.home)
    try:
        pipeline = load_manifest(cfg.manifest_path, registry)
    except Exception as e:
        sys.exit(f"{cfg.manifest_path.name} refused: {e}")
    cfg.registry = registry
    cfg.directory = _directory(cfg.home, registry)
    return cfg, pipeline, Store(cfg.store_path)


def cmd_init(args) -> None:
    """Set up a home — by asking, unless flags already said everything.

    The wizard is the default because the person doing this at a small firm is
    not the person who wants to author three TOML files. Passing --host and
    --user keeps the old one-shot path, so scripts and CI are unaffected.
    """
    from . import wizard
    home = Path(args.home)
    flags_complete = bool(args.host and args.user)
    if args.wizard or (not flags_complete and not args.no_wizard
                       and wizard.available()):
        wizard.run(home)
        return
    if not flags_complete and not wizard.available():
        sys.exit("nothing to read answers from: pass --host and --user"
                 " (and --store-password if the password goes in the file),"
                 " or run `docketry init` where someone can answer.")

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
    elif _os.name == "nt":
        # Do not let "stored 0600" imply a protection Windows does not give.
        print("  password:  stored in config.toml — on Windows that file's"
              " permissions come from the folder it sits in, not from 0600."
              " Set DOCKETRY_IMAP_PASSWORD instead if others use this machine.")
    print("next: point a forwarding rule at the intake mailbox, then run: docketry poll")


def cmd_poll(args) -> None:
    cfg, pipeline, store = _open(args.home)
    if cfg.mailbox is None:
        sys.exit("no [mailbox] configured — run: docketry init")
    if not cfg.mailbox.password:
        sys.exit("no mailbox password (set DOCKETRY_IMAP_PASSWORD)")
    runner = Runner(pipeline, store, registry=cfg.registry)
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
            from .tools.classify import classify as _classify
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
    from .core.roles import refuse_approval
    refusal = refuse_approval(cfg.registry, person=args.by, role=args.role,
                              gate_id=args.gate, required=binding.authority)
    if refusal:
        sys.exit(f"at stage '{row['stage']}': {refusal} — approval not recorded")
    store.add_approval(
        args.message, row["stage"], args.gate,
        approved_by=args.by, role=args.role, note=args.note or "",
    )
    runner = Runner(pipeline, store, registry=cfg.registry)
    try:
        status = runner.advance(args.message)
        print(f"approved; message {args.message} -> {status}")
    except GateRefusal as e:
        print(f"approval recorded, but: {e}")


def cmd_advance(args) -> None:
    cfg, pipeline, store = _open(args.home)
    runner = Runner(pipeline, store, registry=cfg.registry)
    try:
        status = runner.advance(args.message)
        print(f"message {args.message} -> {status}")
    except GateRefusal as e:
        sys.exit(str(e))
    except KeyError:
        sys.exit(f"no message {args.message} — `docketry queue` lists the ones"
                 " waiting, with their numbers")


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
    from .tools.redact import Box, RedactionError
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
    from .tools.redact import RedactionError, find_terms
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
    from .tools.redact import RedactionError, apply, find_terms
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
    from .tools.redact import verify
    survivors = verify(args.file, args.term)
    if survivors:
        print(_sev("FAIL") + "  still extractable: " + ", ".join(survivors))
        sys.exit(1)
    print(_sev("  ok") + f"  none of the {len(args.term)} term(s) appear in {args.file}")



def _timeline(args):
    from .tools.timeline import build
    cfg, _, store = _open(args.home)
    return build(store, args.case, threads=args.thread or None,
                 directory=cfg.directory)


def cmd_timeline(args) -> None:
    from .tools.timeline import LAYERS
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
    from .tools.export import to_docx, to_xlsx
    from .tools.timeline import LAYERS
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
    from .tools.reconcile import parse_docket, reconcile
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
    from .tools.llm import probe
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



def _article(word):
    from .tools.workflow import article
    return article(word)


def _matter_or_exit(store, case):
    from .tools.timeline import normalise_case_number
    row = store.get_matter(normalise_case_number(case))
    if row is None:
        sys.exit(f"no matter for {case} — open it with: docketry matter-open {case}")
    return row


def cmd_matters(args) -> None:
    _, _, store = _open(args.home)
    rows = store.list_matters(args.stage)
    if not rows:
        print("no matters yet")
        return
    for r in rows:
        name = r["display_name"] or "(unnamed)"
        print(f"  {r['stage'].ljust(12)} {r['case_number'].ljust(18)}"
              f" {r['matter_type'].ljust(10)} {name}")
    print(f"\n{len(rows)} matter(s)")


def cmd_matter_open(args) -> None:
    from .tools.timeline import normalise_case_number
    from .tools.workflow import WorkflowError, workflow_for
    cfg, _, store = _open(args.home)
    try:
        wf = workflow_for(cfg.home, args.type, cfg.registry)
    except WorkflowError as e:
        sys.exit(str(e))
    case = normalise_case_number(args.case)
    mid = store.open_matter(case, stage=wf.first_stage,
                            display_name=args.name or "", matter_type=args.type)
    row = store.get_matter(case)
    print(f"matter {mid}: {case} ({args.type}) at stage '{row['stage']}'")


def cmd_matter_status(args) -> None:
    from .tools.workflow import WorkflowError, available, facts_from_store, workflow_for
    cfg, _, store = _open(args.home)
    row = _matter_or_exit(store, args.case)
    try:
        wf = workflow_for(cfg.home, row["matter_type"], cfg.registry)
    except WorkflowError as e:
        sys.exit(str(e))
    facts = facts_from_store(store, row["case_number"])
    print(f"{row['case_number']} ({row['matter_type']}) — stage"
          f" '{row['stage']}'{(' · ' + row['display_name']) if row['display_name'] else ''}")
    for b in available(wf, row["stage"], facts):
        if not b.reasons and not b.needs_authority:
            print(_sev("  ok") + f"  can move to '{b.target}' now")
            continue
        print(_sev("warn") + f"  held out of '{b.target}':")
        for reason in b.reasons:
            print(f"      {reason}")
        if b.needs_authority:
            print(f"      needs {_article(b.needs_authority)}"
                  f" {b.needs_authority} to release it")
    events = store.matter_events(row["id"])
    if events:
        last = events[-1]
        print(f"  last moved {last['from_stage']} -> {last['to_stage']}"
              f" by {last['moved_by']} on {last['at'][:10]}")


def cmd_matter_advance(args) -> None:
    from .tools.workflow import WorkflowError, check, facts_from_store, workflow_for
    cfg, _, store = _open(args.home)
    row = _matter_or_exit(store, args.case)
    try:
        wf = workflow_for(cfg.home, row["matter_type"], cfg.registry)
    except WorkflowError as e:
        sys.exit(str(e))
    facts = facts_from_store(store, row["case_number"])
    blocked = check(wf, row["stage"], args.to, facts)
    if blocked is not None:
        if blocked.reasons:
            print(_sev("FAIL") + f"  cannot move to '{args.to}':")
            for reason in blocked.reasons:
                print(f"      {reason}")
            sys.exit(1)
        if blocked.needs_authority and args.role != blocked.needs_authority:
            print(_sev("FAIL") + f"  '{args.to}' must be released by"
                  f" {_article(blocked.needs_authority)}"
                  f" {blocked.needs_authority}; --role said '{args.role}'")
            sys.exit(1)
    was = store.move_matter(row["id"], args.to, by=args.by, role=args.role,
                            note=args.note or "")
    print(_sev("  ok") + f"  {row['case_number']}: {was} -> {args.to}"
          f" (recorded: {args.by}{', ' + args.role if args.role else ''})")


def cmd_workflow_check(args) -> None:
    """The sandbox: run a workflow and watch where it holds."""
    from .tools.workflow import MatterFacts, WorkflowError, load_workflow, simulate
    try:
        wf = load_workflow(args.file)
    except WorkflowError as e:
        sys.exit(str(e))
    facts = MatterFacts(
        documents=set(args.document or []),
        notices=set(args.notice or []),
        fields=set(args.field or []),
    )
    print(f"{wf.matter_type}: {' -> '.join(wf.stages)}"
          f"{'   (as of ' + wf.as_of + ')' if wf.as_of else ''}")
    path, blocked = simulate(wf, facts)
    print("  reached: " + " -> ".join(path))
    if blocked is None:
        print(_sev("warn") + "  ran to the end with nothing stopping it —"
              " check that is what you meant")
        return
    print(_sev("warn") + f"  holds before '{blocked.target}':")
    for reason in blocked.reasons:
        print(f"      {reason}")
    if blocked.needs_authority:
        print(f"      needs {_article(blocked.needs_authority)}"
              f" {blocked.needs_authority} to release it")



def cmd_roles(args) -> None:
    cfg, _, _ = _open(args.home)
    reg = cfg.registry
    if reg is None:
        print("no roles.toml — authority values in your guardrails and"
              " workflows are NOT checked")
        print("to declare them, copy examples/roles.toml into"
              f" {cfg.home}/roles.toml")
        return
    for name in reg.names():
        role = reg.roles[name]
        releases = ", ".join(role.may_release) or "only holds marked for it"
        print(f"  {name.ljust(14)} releases: {releases}")
        if role.description:
            print(f"                 {role.description}")
    if reg.people:
        print()
        for person, held in sorted(reg.people.items()):
            print(f"  {person}: {', '.join(held)}")
    else:
        print("\n  no people listed — any name may claim any declared role")
    print("\na role here is recorded against a name, not authenticated:"
          " it catches mistakes, not lies")



def cmd_report(args) -> None:
    """Pipeline health. Counted by role and by gate, never by person."""
    from .tools.report import build
    cfg, pipeline, store = _open(args.home)
    rep = build(store, pipeline, days=args.days,
                firm_domains=cfg.firm_domains, directory=cfg.directory)

    print(f"last {rep.days} days — {rep.ingested} message(s) in")
    if rep.by_status:
        print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(rep.by_status.items())))

    if rep.correspondence:
        print("\ncorrespondence — mail someone has to answer")
        for domain, n in rep.correspondence[:10]:
            print(f"  {str(n).rjust(5)}  {domain}")
    if rep.by_kind:
        print("\nwho wrote — correspondence by who they are")
        for kind, n in rep.by_kind:
            print(f"  {str(n).rjust(5)}  {kind.replace('_', ' ')}")
    if rep.notifications:
        print("\nnotifications — one-way, nobody replies to these")
        for domain, n in rep.notifications[:10]:
            print(f"  {str(n).rjust(5)}  {domain}")
    if rep.internal or rep.external:
        print(f"\n  external {rep.external} · internal {rep.internal}"
              + (f" · unattributed {rep.unknown_side}" if rep.unknown_side else ""))

    if rep.turnaround:
        print("\nhow long each check held things (hours)")
        for gate, t in sorted(rep.turnaround.items(),
                              key=lambda kv: -(kv[1]["p90"] or 0)):
            print(f"  {gate.ljust(22)} n={str(t['n']).ljust(4)}"
                  f" median {t['p50']}   slowest tenth {t['p90']}")

    if rep.hold_reasons:
        print("\nwhat held them up")
        for gate, summary, n in rep.hold_reasons:
            print(f"  {str(n).rjust(4)}  [{gate}] {summary[:70]}")

    if rep.quiet_adapters:
        print()
        for name, was, now in rep.quiet_adapters:
            print(_sev("warn") + f"  '{name}' matched {was} notice(s) in the"
                  f" previous {rep.days} days and none since — that source"
                  " probably changed its template")

    if rep.silent_gates:
        print()
        print(_sev("warn") + "  configured but never fired: "
              + ", ".join(rep.silent_gates))
        print("      a gate that has never caught anything is not protecting"
              " anything; check it still matches reality")

    if rep.documents_not_held:
        print()
        print(_sev("warn") + f"  {rep.documents_not_held} document(s) were named"
              " in a notice with a link but no copy — you were told about them"
              " and cannot open them")

    if rep.stuck_matters:
        print("\nmatters that have not moved")
        for case, stage, d in rep.stuck_matters[:10]:
            print(f"  {str(int(d)).rjust(4)}d  {case.ljust(18)} {stage}")
        print("      standing still is not the same as neglected — this is"
              " visibility, not a scoreboard")

    for note in rep.notes:
        print("\n  " + note)
    print("\ncounted by role and by gate. Docketry has no login, so it does not"
          " measure people.")



def cmd_contacts(args) -> None:
    from .tools.contacts import KINDS
    cfg, _, _ = _open(args.home)
    d = cfg.directory
    if d is None:
        print("no contacts.toml — Docketry cannot tell client mail from the"
              " other side's, so all of it is filed as correspondence")
        print("to set it up, copy examples/contacts.toml into"
              f" {cfg.home}/contacts.toml")
        return
    everyone = sorted(list(d.by_email.values()) + list(d.domains.values()),
                      key=lambda c: (KINDS.index(c.kind), c.email))
    for c in everyone:
        roles = f"  roles: {', '.join(c.roles)}" if c.roles else ""
        print(f"  {c.kind.ljust(18)} {c.email.ljust(32)} {c.label}{roles}")
    print(f"\n{len(everyone)} contact(s)")
    if not any(c.kind == "client" for c in everyone):
        print(_sev("warn") + "  no client contacts — until one is listed,"
              " privileged mail is filed alongside the other side's")


def cmd_verify_draft(args) -> None:
    from .tools.cite import CiteError, verify, extract_citations
    from .tools.extract import ExtractionError, extract_path

    try:
        text = extract_path(args.file).full_text
    except ExtractionError as e:
        sys.exit(str(e))
    try:
        if args.offline:
            raise CiteError("offline requested")
        from .tools.cite_client import CourtListenerClient
        client = CourtListenerClient(token=args.token)
        report = verify(text, client)
        client.close()
    except CiteError as e:
        if not args.offline:
            print(f"network verification unavailable ({e}); extraction-only mode")
        try:
            from .tools.cite import citation_inventory
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
    from .tools.extract import ExtractionError, extract_path
    from .tools.lint import RulepackError, lint, load_rulepack

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
    from .tools.classify import classify
    from .tools.extract import ExtractionError, extract_path

    path = Path(args.file)
    text, degraded = "", ""
    if not path.exists():
        # Naming a document you were told about, without having it, is a real
        # way people use this. Classify the title, and say plainly that is all
        # that happened.
        degraded = "there is no file at that path"
    try:
        text = extract_path(path).full_text if not degraded else ""
    except ExtractionError as e:
        # A file whose text cannot be read can still be classified from its
        # title, and often correctly. What must not happen is presenting that
        # as an ordinary result: the classifier never saw the document, and
        # whoever reads this needs to know which of the two it was.
        degraded = str(e)

    label, tier = classify(path.stem, text)
    print(f"{label} ({tier})")
    if degraded:
        print(_sev("warn") + f"  classified from the FILENAME only — the text"
              f" could not be read: {degraded}")
    elif not text.strip():
        print(_sev("warn") + "  classified from the FILENAME only — the file"
              " yielded no text")


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
    try:
        outcome = store.apply_classification(args.id, by=args.by, role=args.role)
    except KeyError:
        sys.exit(f"no staged proposal {args.id} — `docketry class-queue` lists"
                 " them with their numbers")
    print(outcome)


def cmd_ui(args) -> None:
    from .webui import make_server
    cfg, pipeline, store = _open(args.home)
    store.close()
    server = make_server(cfg.store_path, pipeline, port=args.port,
                         home=cfg.home, firm_domains=cfg.firm_domains,
                         directory=cfg.directory, registry=cfg.registry)
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
    from .core.config import load_home
    from .core.manifest import ManifestError, load_manifest as _lm

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
    # Gates before the manifest: a manifest that binds one refuses to load
    # until the gate is registered, so a broken gate file must be diagnosed
    # here rather than surfacing as a confusing "unknown gate".
    from .core.gates import GateLoadError, described
    from .core.gates import load_home as _home_gates, load_installed
    try:
        _home_gates(home)
        load_installed()
        rows = described()
        outside = [g for g, src, _, _ in rows if not src.startswith("built-in")]
        report("PASS", f"gates: {len(rows)} available"
                       + (f", {len(outside)} from outside the package"
                          f" ({', '.join(outside)})" if outside else ""))
        for gate_id, src, _, _ in rows:
            if src.startswith("file:"):
                report("PASS", f"  {gate_id} ← {_short_source(src, home)}"
                               " (executed from your home directory)")
    except GateLoadError as e:
        report("FAIL", f"gate refused: {e}")
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
        from .tools.notices import AdapterError, load_adapters_file
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
    reg = _registry(cfg.home) if cfg.manifest_path.exists() else None
    if reg is not None:
        report("PASS", f"roles declared: {', '.join(reg.names())}")
    else:
        report("WARN", "no roles.toml — authority values are not checked")
    if cfg.store_path.exists():
        chain = Store(cfg.store_path)
        try:
            chain_report = chain.chain_report()
        finally:
            chain.close()
        if not chain_report.ok:
            report("FAIL", chain_report.summary + " — the approval log has been"
                           " edited since it was written")
        elif chain_report.total:
            report("PASS", chain_report.summary)
    if cfg.llm is not None:
        from .tools.llm import probe
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
    chain = store.chain_report()
    if not chain.ok:
        lines.append(f"  ALERT: {chain.summary}")
    elif chain.chained:
        # The daily paste is the cheapest anchor there is: once this line is
        # in a mailbox the firm does not administer, the log cannot be quietly
        # rewritten to disagree with it.
        lines.append(f"  approvals head: {chain.head} ({chain.chained} row(s))")
    print("\n".join(lines))


def cmd_anchor(args) -> None:
    """Verify the approval chain and print its head.

    The digests are on the same disk as the rows they cover, so a rewrite can
    recompute them. A copy of the head kept elsewhere is what makes a rewrite
    detectable. Refuses to print over a chain that does not verify.
    """
    cfg, _, store = _open(args.home)
    report = store.chain_report()
    if not report.ok:
        sys.exit(f"{report.summary}. Not anchoring a log that does not"
                 " verify: rows from that point were changed after they were"
                 " written. Earlier rows still verify.")
    stamp = st.utcnow()
    line = (f"docketry-anchor {stamp} approvals={report.chained}"
            f" head={report.head}")
    path = Path(cfg.home) / "anchors.log"
    with open(path, "a") as fh:
        fh.write(line + "\n")
    print(line)
    if report.unchained:
        print(f"note: {report.unchained} approval(s) predate the chain and are"
              " not covered by this anchor")
    print()
    print("Store this line outside this machine: mail it, print it, or paste it")
    print("into a case note. If the approvals table is rewritten later, its head")
    print("will no longer match this value.")
    print(f"Also appended to {path}. That copy is on the same disk as the log it")
    print("describes, so it does not detect a rewrite on its own.")


def cmd_new_gate(args) -> None:
    """Write a working gate into the firm's home, ready to edit."""
    from .core.gates import GATES_DIR, ID_SHAPE
    from .scaffold import gate_binding_toml, gate_source

    gate_id = args.id.strip()
    if not ID_SHAPE.match(gate_id):
        sys.exit(f"gate id {gate_id!r} should be lowercase words joined by"
                 " hyphens, like 'long-subject' — that is the name manifests"
                 " will bind it by")
    directory = Path(args.home) / GATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (gate_id.replace("-", "_") + ".py")
    if path.exists() and not args.force:
        sys.exit(f"{path} already exists — pass --force to overwrite it")
    path.write_text(gate_source(gate_id, args.title or ""))
    print(f"wrote {path}")
    print()
    print("As written it holds any message with a subject over five words.")
    print("Run it against a message:")
    print(f'  docketry --home {args.home} try-gate {gate_id} \\')
    print('      --subject "this subject is quite a lot longer than five words"')
    print()
    print("It runs once guardrails.toml binds it. Add:")
    print()
    for line in gate_binding_toml(gate_id).splitlines():
        print(f"    {line}")


def _short_source(source: str, home) -> str:
    """`file:/long/path/to/home/gates/x.py` reads better as `file:gates/x.py`."""
    if not source.startswith("file:"):
        return source
    path = Path(source[len("file:"):])
    try:
        return "file:" + str(path.relative_to(Path(home).resolve()))
    except ValueError:
        return source


def cmd_gates(args) -> None:
    """Every gate this installation can bind, and where each came from."""
    from .core.gates import described

    _load_gates(args.home)
    rows = described()
    width = max((len(r[0]) for r in rows), default=4)
    for gate_id, source, doc, stages in rows:
        where = "" if stages is None else f"  [{', '.join(sorted(stages))} only]"
        print(f"{gate_id:{width}}  {_short_source(source, args.home)}{where}")
        if doc and not args.quiet:
            print(f"{'':{width}}  {doc}")
    if not args.quiet:
        print()
        print(f"{len(rows)} gates. Sources: built-in = docketry/core/gates,")
        print("built-in (tools) = docketry/tools, file: = <home>/gates/,")
        print("package: = an installed docketry.gates entry point.")
        print("Writing one: GATES.md")


def _try_options(args) -> dict:
    """Options for the run: what was passed, else what the manifest says.

    Falling back to the manifest means `try-gate` exercises the firm's own
    configuration rather than a default nobody chose.
    """
    options = {}
    for pair in args.option or []:
        key, _, value = pair.partition("=")
        if not _:
            sys.exit(f"--option wants KEY=VALUE, got {pair!r}")
        try:
            options[key.strip()] = tomllib.loads(f"v = {value}")["v"]
        except tomllib.TOMLDecodeError:
            options[key.strip()] = value          # a bare string is fine
    if options:
        return options
    manifest = Path(args.home) / MANIFEST_NAME
    if manifest.exists():
        data = tomllib.loads(manifest.read_text())
        for gate in data.get("gate", []):
            if gate.get("id") == args.gate:
                return gate.get("options", {})
    return {}


def cmd_try_gate(args) -> None:
    """Run one gate against one message and print its findings.

    Builds the message from the flags, or reads --eml. Options come from the
    manifest binding unless --option overrides them. Nothing is stored.
    """
    from email.message import EmailMessage

    from .core.envelope import parse_message
    from .core.gates import get
    from .core.pipeline import SEVERITY_FAIL
    from .core.store import utcnow

    _load_gates(args.home)
    try:
        cls = get(args.gate)
    except KeyError as e:
        sys.exit(f"{e}\n(`docketry gates` lists them; `docketry new-gate <id>`"
                 " writes one)")
    gate = cls()

    if args.eml:
        raw = Path(args.eml).read_bytes()
    else:
        m = EmailMessage()
        m["From"] = args.sender
        m["To"] = "intake@yourfirm.example"
        m["Subject"] = args.subject
        m.set_content(args.body or "")
        for name in args.attach or []:
            m.add_attachment(b"%PDF-1.4 sample", maintype="application",
                             subtype="pdf", filename=name)
        raw = bytes(m)
    env = parse_message(raw, source="try-gate", fetched_at=utcnow())

    options = _try_options(args)
    problems = getattr(gate, "validate_options", lambda o: [])(options)
    if problems:
        sys.exit("these options would refuse the manifest: " + "; ".join(problems))

    findings = gate.check(env, options)
    print(f"gate:    {args.gate}"
          f" ({_short_source(gates_mod.source_of(args.gate), args.home)})")
    print(f"message: {env.subject!r} from {env.from_addr},"
          f" {len(env.attachments)} attachment(s)")
    if options:
        print(f"options: {options}")
    if not findings:
        print("result:  no findings — this message passes")
        return
    for f in findings:
        print(f"result:  [{f.severity}] {f.summary}")
    if any(f.severity == SEVERITY_FAIL for f in findings):
        print()
        print("A 'fail' finding holds the message. Whether that means blocked"
              " or queued")
        print("for review is the binding's on_fail in guardrails.toml.")


def cmd_demo(args) -> None:
    """Seed a disposable home with sample traffic and open the dashboard."""
    import tempfile
    import webbrowser
    from email.message import EmailMessage

    from .tools import notices as nmod
    from .tools.classify import classify as _classify
    from .core.envelope import parse_message
    from .core.manifest import load_manifest
    from .core.pipeline import Runner
    from .core.store import Store
    from .webui import make_server

    from .core.roles import load_roles

    home = Path(tempfile.mkdtemp(prefix="docketry-demo-"))
    (home / "guardrails.toml").write_text(DEMO_MANIFEST)
    (home / "roles.toml").write_text(DEMO_ROLES)
    registry = load_roles(home / "roles.toml")
    pipeline = load_manifest(home / "guardrails.toml", registry)
    store = Store(home / "store")
    runner = Runner(pipeline, store, registry=registry)
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
        mail("reception@demofirm.example", "New matter — conflicts check",
             "Walk-in this morning wants to sue Roberta Vance over the"
             " Riverside build. Pulling the file now.\n"),
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

    server = make_server(home / "store", pipeline, port=args.port,
                         registry=registry)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Demo home: {home} (disposable, deleted with the directory)")
    print("Six messages ingested. Three passed all gates. Three are held:")
    print()
    print("  name-screen     blocked          a conflicts email naming a")
    print("                                   screened party. Releasable by")
    print("                                   attorney only; the release is")
    print("                                   recorded with a name and time.")
    print("  notice-parser   pending_review   an e-service notice whose portal")
    print("                                   changed its template, so the")
    print("                                   required fields did not extract.")
    print("  sender-scope    pending_review   an unknown sender, outside the")
    print("                                   configured allow list.")
    print()
    print(f"Release them at {url}   (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndemo stopped; nothing was saved outside", home)


DEMO_ROLES = """\
[[role]]
name = "paralegal"
description = "Clears routine intake holds."
may_release = ["sender-scope", "attachment-policy", "notice-parser",
               "provenance-stamp"]

[[role]]
name = "attorney"
description = "Releases anything, including a conflict hold."
may_release = ["*"]
"""

DEMO_MANIFEST = """\
[pipeline]
stages = ["ingest", "review"]

# The ethical wall. Anything naming a screened party stops here, and only a
# recorded release by an attorney moves it.
[[gate]]
id = "name-screen"
binds_to = ["ingest"]
on_fail = "block"
authority = "attorney"

[gate.options]
terms = ["Roberta Vance"]
note = "ethical wall"

[[gate]]
id = "notice-parser"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[[gate]]
id = "sender-scope"
binds_to = ["ingest"]
on_fail = "bounce"
authority = "paralegal"

[gate.options]
allow = ["@myflcourtaccess.com", "@uscourts.gov", "@circuit19.example",
         "@demofirm.example"]

[[gate]]
id = "attachment-policy"
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



def _user_errors():
    """Errors that mean the user's config or input is wrong, not that we broke.

    These get one clean sentence and a non-zero exit. Anything NOT in this
    tuple keeps its traceback on purpose: an unexpected crash is a bug in
    Docketry, and swallowing it into a tidy message is how bugs go unreported.
    """
    from .tools.cite import CiteError
    from .tools.extract import ExtractionError
    from .tools.llm import LLMError
    from .core.manifest import ManifestError
    from .tools.notices import AdapterError
    from .tools.redact import RedactionError
    from .core.roles import RoleError
    from .wizard import WizardAborted
    from .tools.workflow import WorkflowError
    return (AdapterError, CiteError, ExtractionError, LLMError, ManifestError,
            RedactionError, RoleError, WizardAborted, WorkflowError)


def build_parser() -> argparse.ArgumentParser:
    """The whole CLI surface, separable from running it.

    Split out so a test can ask whether a command the documentation shows is
    a command the tool accepts. A tutorial that has drifted is worse than none.
    """
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
    sp.add_argument("--wizard", action="store_true",
                    help="ask the setup questions even if --host/--user were given")
    sp.add_argument("--no-wizard", action="store_true",
                    help="write a starter manifest instead of asking")
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

    sp = sub.add_parser("report", help="pipeline health: sources, bottlenecks, dead config")
    sp.add_argument("--days", type=int, default=30)
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("contacts", help="who an address belongs to")
    sp.set_defaults(fn=cmd_contacts)

    sp = sub.add_parser("roles", help="who may release what")
    sp.set_defaults(fn=cmd_roles)

    sp = sub.add_parser("matters", help="matters and the stage each one is at")
    sp.add_argument("--stage")
    sp.set_defaults(fn=cmd_matters)

    sp = sub.add_parser("matter-open", help="start tracking a matter")
    sp.add_argument("case")
    sp.add_argument("--type", default="generic", help="matter type (picks the workflow)")
    sp.add_argument("--name", help="how the firm refers to it")
    sp.set_defaults(fn=cmd_matter_open)

    sp = sub.add_parser("matter-status", help="where a matter is and what it is waiting on")
    sp.add_argument("case")
    sp.set_defaults(fn=cmd_matter_status)

    sp = sub.add_parser("matter-advance", help="move a matter to the next stage")
    sp.add_argument("case")
    sp.add_argument("to", help="stage to move it to")
    sp.add_argument("--by", required=True, help="who is making this change")
    sp.add_argument("--role", default="", help="the role they hold")
    sp.add_argument("--note")
    sp.set_defaults(fn=cmd_matter_advance)

    sp = sub.add_parser("workflow-check", help="run a workflow and see where it holds")
    sp.add_argument("file")
    sp.add_argument("--document", action="append", help="pretend this doc type exists")
    sp.add_argument("--notice", action="append")
    sp.add_argument("--field", action="append")
    sp.set_defaults(fn=cmd_workflow_check)

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

    sp = sub.add_parser("anchor", help="print the approval chain's head, to keep off this machine")
    sp.set_defaults(fn=cmd_anchor)

    sp = sub.add_parser("gates", help="list every gate this installation can bind")
    sp.add_argument("--quiet", action="store_true", help="ids and sources only")
    sp.set_defaults(fn=cmd_gates)

    sp = sub.add_parser("new-gate", help="write a working gate into <home>/gates/ to edit")
    sp.add_argument("id", help="the id manifests bind it by, e.g. long-subject")
    sp.add_argument("--title", help="one-line description for its docstring")
    sp.add_argument("--force", action="store_true", help="overwrite an existing file")
    sp.set_defaults(fn=cmd_new_gate)

    sp = sub.add_parser("try-gate", help="run one gate against one message, and print the findings")
    sp.add_argument("gate")
    sp.add_argument("--eml", help="a saved .eml message to run it against")
    sp.add_argument("--subject", default="Test message")
    sp.add_argument("--from", dest="sender", default="someone@example.com")
    sp.add_argument("--body", default="")
    sp.add_argument("--attach", action="append",
                    help="attachment filename (repeatable)")
    sp.add_argument("--option", action="append", metavar="KEY=VALUE",
                    help="override a [gate.options] value (repeatable)")
    sp.set_defaults(fn=cmd_try_gate)

    sp = sub.add_parser("status", help="message counts by status")
    sp.set_defaults(fn=cmd_status)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except _user_errors() as e:
        # Loud, and a sentence rather than a stack trace: the message from
        # each of these already says what is wrong and what to do about it.
        sys.exit(str(e))
    except tomllib.TOMLDecodeError as e:
        # The likeliest failure of all, because these files are hand-edited.
        sys.exit(f"that file is not valid TOML: {e}")
    except FileNotFoundError as e:
        sys.exit(f"no such file: {e.filename}")
    except IsADirectoryError as e:
        sys.exit(f"that is a directory, not a file: {e.filename}")
    except PermissionError as e:
        sys.exit(f"no permission to read {e.filename}")
    except KeyboardInterrupt:
        sys.exit("\nstopped")


if __name__ == "__main__":
    main()
