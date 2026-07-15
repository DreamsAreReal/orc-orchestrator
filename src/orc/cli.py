"""orc CLI entry point — add / start / status / init (python3-stdlib argparse).

Every command supports --json (taste passport: grep-able plain text + JSON for tools).
User-facing report text is Russian; operational CLI lines are English.
"""
import os
import sys
import json
import argparse

from . import config
from . import beads
from . import shift as shiftmod
from . import dispatcher
from . import canary as canarymod
from . import report as reportmod
from . import probes
from . import gitutil
from . import strings as S


def _slugify(text, fallback):
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = "-".join(s.split("-")[:6])
    return s or fallback


def cmd_init(args):
    hub = config.hub_dir()
    config.ensure_home()
    config.write_default_config()
    if not beads.bd_available():
        print(S.ERR_BD_MISSING, file=sys.stderr)
        return 1
    created = beads.init(hub)
    msg = S.HUB_INITIALIZED if created else S.HUB_ALREADY
    out = msg.format(hub=hub)
    if args.json:
        print(json.dumps({"hub": hub, "created": created}))
    else:
        print(out)
    return 0


def _add_one(hub, project, text, priority, gate=False):
    project = os.path.abspath(os.path.expanduser(project))
    if not text or not text.strip():
        raise ValueError(S.ERR_NO_TASK_TEXT)
    slug = _slugify(text, fallback="task")
    labels = ["orc"] + (["gate"] if gate else [])
    meta = {"project": project, "slug": slug, "text": text.strip()}
    # capture the product-layer rev at add time so the dispatcher can re-validate the
    # plan against later docs/ changes (R5). None if the project is not a git repo yet.
    prod_rev = gitutil.product_layer_rev(project) if gitutil.is_repo(project) else None
    if prod_rev:
        meta["product_rev"] = prod_rev
    if gate:
        meta["gate"] = True
    issue_id = beads.create(hub, text.strip(), priority=priority, labels=labels, metadata=meta)
    return issue_id, project


def cmd_add(args):
    hub = config.hub_dir()
    if not os.path.isdir(os.path.join(hub, ".beads")):
        print(S.ERR_HUB_MISSING, file=sys.stderr)
        return 1

    created = []
    if args.batch:
        # each stdin line: "project: task text"
        for line in sys.stdin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if ":" not in line:
                print("skip (no 'project: text'): %s" % line, file=sys.stderr)
                continue
            proj, text = line.split(":", 1)
            proj = proj.strip()
            if not os.path.isdir(os.path.abspath(os.path.expanduser(proj))):
                print(S.ERR_PROJECT_MISSING.format(project=proj), file=sys.stderr)
                continue
            try:
                issue_id, p = _add_one(hub, proj, text.strip(), args.priority)
                created.append({"id": issue_id, "project": p})
            except ValueError as e:
                print(str(e), file=sys.stderr)
        if args.json:
            print(json.dumps({"created": created}))
        else:
            print(S.ADD_BATCH_DONE.format(n=len(created)))
        return 0 if created else 1

    # single task
    if not os.path.isdir(os.path.abspath(os.path.expanduser(args.project))):
        print(S.ERR_PROJECT_MISSING.format(project=args.project), file=sys.stderr)
        return 1
    try:
        issue_id, p = _add_one(hub, args.project, args.text, args.priority, gate=args.gate)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"id": issue_id, "project": p}))
    else:
        print(S.ADD_CREATED.format(id=issue_id, project=p))
    return 0


def cmd_start(args):
    cfg = config.load()
    hub = config.hub_dir()
    if not os.path.isdir(os.path.join(hub, ".beads")):
        print(S.ERR_HUB_MISSING, file=sys.stderr)
        return 1

    # canary preflight
    checks, ok = canarymod.run(cfg, hub, spawn_probe=not args.no_spawn_probe)
    print(canarymod.format_report(checks))
    if not ok:
        fails = sum(1 for c in checks if not c[1])
        print(S.START_CANARY_FAIL.format(n=fails), file=sys.stderr)
        if args.json:
            print(json.dumps({"canary_ok": False,
                              "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]}))
        return 2
    print(S.START_CANARY_OK)

    # single-shift path (F2 skeleton): claim + spawn the top ready task
    state = shiftmod.load()
    # reconcile shift.json against live PIDs + bd (F4 arbiter): drop dead workers,
    # their tasks return to ready via lease before we compute what to spawn.
    state, dropped = dispatcher.reconcile(state, hub)
    for tid in dropped:
        print("reconcile: dropped dead worker for %s (task returned to ready)" % tid)
    window = probes.ccusage_window()
    pct = reportmod._window_pct(window)
    shiftmod.start_shift(state, window_pct=pct)

    tasks = dispatcher.order_ready(beads.ready(hub))
    if not tasks:
        print(S.START_NO_READY)
        shiftmod.save(state)
        if args.json:
            print(json.dumps({"canary_ok": True, "spawned": []}))
        return 0

    spawned = []
    limit = 1 if args.once else cfg.get("max_workers", 1)
    for task in tasks:
        if len(state.get("workers", [])) >= limit:
            break
        oks, detail, state = dispatcher.spawn_one(cfg, hub, state, task)
        if oks:
            spawned.append({"id": task.get("id"), "detail": detail})
            print(detail)
        if args.once and oks:
            break
    shiftmod.save(state)
    if args.json:
        print(json.dumps({"canary_ok": True, "spawned": spawned}))
    return 0


def cmd_status(args):
    hub = config.hub_dir()
    state = shiftmod.load()

    # Close the loop (F14): before rendering, poll each active worker's task STATE.md and
    # react to a terminal status (done -> bd close + close the Terminal window; gate ->
    # park). This is exactly the consumer scenario — the operator looks at the newspaper,
    # and the newspaper must have caught up to the DONE that is already on disk. We persist
    # so the transition is durable and not recomputed every view.
    hub_ready = os.path.isdir(os.path.join(hub, ".beads"))
    if hub_ready and state.get("workers"):
        cfg = config.load()
        # F6: park any live worker over its per-task token budget before rendering, so the
        # newspaper shows the budget parking (protecting the weekly pool).
        budget_parked = dispatcher.enforce_budget(cfg, hub, state)
        state, transitions = dispatcher.poll_completions(state, hub)
        if transitions or budget_parked:
            shiftmod.save(state)

    # The ready queue (tasks added but not yet started) — shown when no shift is running.
    ready_tasks = []
    if hub_ready and not state.get("started") and not state.get("workers"):
        try:
            ready_tasks = dispatcher.order_ready(beads.ready(hub))
        except beads.BeadsError:
            ready_tasks = []

    window = probes.ccusage_window()
    if args.json:
        print(json.dumps({
            "started": state.get("started"),
            "workers": state.get("workers", []),
            "parked": state.get("parked", []),
            "done": state.get("done", []),
            "failed": state.get("failed", []),
            "ready": [{"id": t.get("id"),
                       "project": beads.task_meta(t).get("project")} for t in ready_tasks],
            "summary": reportmod.summary_line(state),
        }, ensure_ascii=False))
        return 0
    if args.newspaper:
        print(reportmod.newspaper(state, hub, window=window))
    else:
        print(reportmod.live_status(state, hub, window=window, ready_tasks=ready_tasks))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="orc", description="autonomous task-shift loop for Claude Code")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser(
        "init",
        help="initialize the orc hub (a single ~/.orc dir with a beads queue)",
        description=(
            "Create the orc hub: a single directory at ~/.orc (override with $ORC_HOME) "
            "that holds one beads queue (.beads/) shared by ALL your projects, plus the "
            "default config.json. Idempotent: running it again reports the existing hub. "
            "The hub is global, not per-project — all `orc add` tasks land in this one "
            "queue regardless of which project they target."),
    )
    pi.add_argument("--json", action="store_true",
                    help="print the result as JSON ({hub, created})")
    pi.set_defaults(func=cmd_init)

    pa = sub.add_parser("add", help="add a task to the queue")
    pa.add_argument("project", nargs="?", help="project directory")
    pa.add_argument("text", nargs="?", help="task text")
    pa.add_argument("-p", "--priority", type=int, default=2, help="priority 0..4 (0=urgent)")
    pa.add_argument("--gate", action="store_true", help="mark as a gate task (needs a human)")
    pa.add_argument("--batch", action="store_true", help="read 'project: text' lines from stdin")
    pa.add_argument("--json", action="store_true")
    pa.set_defaults(func=cmd_add)

    ps = sub.add_parser("start", help="run canary preflight and start the shift")
    ps.add_argument("--once", action="store_true", help="spawn a single worker and return")
    ps.add_argument("--no-spawn-probe", action="store_true",
                    help="skip the terminal-spawn canary probe")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_start)

    pt = sub.add_parser("status", help="live status or completion newspaper")
    pt.add_argument("--newspaper", action="store_true", help="print the completion newspaper")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(func=cmd_status)

    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
