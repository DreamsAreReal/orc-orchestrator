"""orc CLI entry point — add / start / status / init (python3-stdlib argparse).

Every command supports --json (taste passport: grep-able plain text + JSON for tools).
User-facing report text is Russian; operational CLI lines are English.
"""
import os
import sys
import json
import time as _time
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


def _add_one(hub, project, text, priority, gate=False, gate_card=None):
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
        # F9 gate card: scope / bar / authority / cost of error / irreversible flag. Shown
        # on the newspaper gate card when the worker reaches the gate and waits live.
        if gate_card:
            meta["gate_card"] = {k: v for k, v in gate_card.items() if v is not None}
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
    gate_card = None
    if args.gate:
        gate_card = {
            "scope": getattr(args, "scope", None),
            "bar": getattr(args, "bar", None),
            "authority": getattr(args, "authority", None),
            "cost": getattr(args, "cost", None),
            "irreversible": True if getattr(args, "irreversible", False) else None,
        }
    try:
        issue_id, p = _add_one(hub, args.project, args.text, args.priority,
                               gate=args.gate, gate_card=gate_card)
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

    # P7: in --json mode stdout must be ONLY a single valid JSON object (pipeable to jq).
    # All human-readable canary report / status lines go to stderr; plain mode keeps them
    # on stdout as before. `_info` routes a human line to the right stream.
    def _info(line):
        print(line, file=sys.stderr if args.json else sys.stdout)

    # canary preflight
    checks, ok = canarymod.run(cfg, hub, spawn_probe=not args.no_spawn_probe)
    _info(canarymod.format_report(checks))
    if not ok:
        failed = [n for n, o, _d in checks if not o]
        print(S.START_CANARY_FAIL.format(n=len(failed)), file=sys.stderr)
        # G7: PUSH a macOS notification so the operator learns the shift did NOT start --
        # in unattended mode this is the only signal (the newspaper cannot catch up when
        # there is no shift). Never let a notification failure change the exit path.
        try:
            from . import notify
            notify.notify_canary_fail(cfg, failed)
        except Exception:
            pass
        if args.json:
            print(json.dumps({"canary_ok": False,
                              "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]}))
        return 2
    _info(S.START_CANARY_OK)

    # single-shift path (F2 skeleton): claim + spawn the top ready task
    state = shiftmod.load()
    # reconcile shift.json against live PIDs + bd (F4 arbiter): drop dead workers,
    # their tasks return to ready via lease before we compute what to spawn.
    state, dropped = dispatcher.reconcile(state, hub, cfg=cfg)
    for tid in dropped:
        _info("reconcile: dropped dead worker for %s (task returned to ready)" % tid)
    window = probes.ccusage_window()
    pct = reportmod._window_pct(window)
    shiftmod.start_shift(state, window_pct=pct)

    tasks = dispatcher.order_ready(beads.ready(hub))
    if not tasks:
        _info(S.START_NO_READY)
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
            _info(detail)
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
        state, transitions = dispatcher.poll_completions(state, hub, cfg=cfg)
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


def cmd_stop(args):
    """Kill switch (F10/G10): stop all workers <=10s; their tasks return to ready.

    SIGTERM every worker's session (kill by tty/backend), wait up to stop_grace_seconds,
    then SIGKILL any survivor. Each stopped task is reopened in bd so the next shift
    re-serves it (nothing is lost). The whole operation is bounded so an operator can
    always halt an autonomous shift promptly.
    """
    cfg = config.load()
    hub = config.hub_dir()
    state = shiftmod.load()
    workers = list(state.get("workers", []))
    if not workers:
        if args.json:
            print(json.dumps({"stopped": [], "secs": 0}))
        else:
            print(S.STOP_NO_WORKERS)
        return 0

    t0 = _time.time()
    grace = cfg.get("stop_grace_seconds", 5)
    ttys = []
    recorded_pids = []
    for w in workers:
        # backend-aware stop (Terminal: kill by tty; Ghostty: kill by session marker)
        dispatcher.spawn.close_worker(cfg, w.get("tab_id"), session=w.get("session"))
        tty = dispatcher.spawn.window_tty(w.get("tab_id")) if w.get("tab_id") else None
        if tty:
            ttys.append(tty)
        # E3 fix: the RECORDED worker PID (captured into shift.json by F8) is the reliable
        # kill handle. tty resolution can fail if the window already went away / the tab id
        # is stale (Terminal backend), which would let a live worker survive `orc stop`
        # silently. So we always ALSO track the recorded PID and SIGKILL it below --
        # kill-by-own-PID is the brief P0 discipline ("kill only your own PIDs").
        pid = w.get("pid")
        if pid:
            recorded_pids.append(pid)

    def _still_alive():
        alive = any(dispatcher.spawn.pids_on_tty(t) for t in ttys)
        for p in recorded_pids:
            if dispatcher._pid_alive(p):
                alive = True
        return alive

    # bounded wait for the SIGTERM'd processes to exit, then SIGKILL any survivor.
    deadline = t0 + grace
    while _time.time() < deadline:
        if not _still_alive():
            break
        _time.sleep(0.2)
    # PID-anchored SIGKILL first (survives a failed tty resolve), then tty sweep as backup.
    for pid in recorded_pids:
        if dispatcher._pid_alive(pid):
            try:
                os.kill(int(pid), 9)
            except (OSError, ValueError, TypeError):
                pass
    for t in ttys:
        for pid in dispatcher.spawn.pids_on_tty(t):
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass

    # return each stopped task to ready (bd reopen) so the shift re-serves it; reset shift.
    requeued = []
    for w in workers:
        task_id = w.get("task")
        if not task_id:
            continue
        try:
            task = beads.show(hub, task_id)
            if task and task.get("status") not in ("closed", "done"):
                beads.reopen(hub, task_id)
                requeued.append(task_id)
        except beads.BeadsError:
            pass
    shiftmod.save(shiftmod.reset())

    secs = round(_time.time() - t0, 1)
    if args.json:
        print(json.dumps({"stopped": [w.get("task") for w in workers],
                          "requeued": requeued, "secs": secs}))
    else:
        print(S.STOP_DONE.format(n=len(workers), secs=secs))
        for tid in requeued:
            print(S.STOP_TASK_REQUEUED.format(task=tid))
    return 0


def cmd_daemon(args):
    """Dispatcher loop run by the LaunchAgent (F10). Runs until stopped/idle.

    One tick = reconcile -> poll completions -> admit+spawn ready tasks -> supervise.
    Sleeps `poll_interval` between ticks. Exits cleanly when the queue is fully drained
    (KeepAlive.Crashed means a clean exit is NOT restarted -- a daytime shift ends when
    the work is done). ORC_DAEMON_ONCE=1 runs a single tick (used by the verify script).
    """
    from . import watchdog
    cfg = config.load()
    hub = config.hub_dir()
    config.ensure_home()
    if not beads.bd_available() or not os.path.isdir(os.path.join(hub, ".beads")):
        print(S.ERR_HUB_MISSING, file=sys.stderr)
        return 1

    once = os.environ.get("ORC_DAEMON_ONCE") == "1" or args.once
    interval = cfg.get("poll_interval_seconds", 15)
    idle_ticks = 0
    while True:
        state = shiftmod.load()
        state, dropped = dispatcher.reconcile(state, hub, cfg=cfg)
        state, _tr = dispatcher.poll_completions(state, hub, cfg=cfg)
        dispatcher.enforce_budget(cfg, hub, state)
        try:
            watchdog.supervise(cfg, hub, state)
        except Exception:
            pass  # watchdog must never crash the daemon
        tasks = dispatcher.order_ready(beads.ready(hub))
        limit = cfg.get("max_workers", 1)
        for task in tasks:
            if len(state.get("workers", [])) >= limit:
                break
            oks, detail, state = dispatcher.spawn_one(cfg, hub, state, task)
            if oks:
                print(detail)
        shiftmod.save(state)
        # idle = no workers and no ready tasks -> the shift is drained; exit cleanly.
        if not state.get("workers") and not tasks:
            idle_ticks += 1
        else:
            idle_ticks = 0
        if once or idle_ticks >= 2:
            break
        _time.sleep(interval)
    return 0


def cmd_setup(args):
    """F10: make husk windows not accumulate for any user (reproducible profile fix).

    Sets the orc Terminal profile's shellExitAction to 0 (close window on shell exit) via
    plistlib, backing up the previous value first. Also hints at `orc install` for autostart.
    """
    from . import terminal_profile as tp
    cfg = config.load()
    result = {"profile": None, "changed": False}
    path = tp.terminal_plist_path()
    if os.path.exists(path):
        try:
            data = tp._load(path)
            profile = tp.resolve_profile(data, requested=cfg.get("terminal_profile"))
            if profile:
                if getattr(args, "revert", False):
                    r = tp.revert(path, profile)
                    result = {"profile": profile, "reverted": r.get("reverted")}
                    if not args.json:
                        print("orc setup: reverted profile '%s' shellExitAction" % profile)
                else:
                    r = tp.set_close_on_exit(path, profile)
                    result = {"profile": profile, "changed": r["changed"], "old": r["old"]}
                    if not args.json:
                        if r["changed"]:
                            print(S.SETUP_PROFILE_DONE.format(profile=profile, old=r["old"]))
                        else:
                            print(S.SETUP_PROFILE_ALREADY.format(profile=profile))
            else:
                if not args.json:
                    print(S.SETUP_PROFILE_NONE)
        except Exception as e:
            print("orc setup: profile edit failed: %s" % e, file=sys.stderr)
            if not args.json:
                print(S.SETUP_PROFILE_NONE)
    else:
        if not args.json:
            print(S.SETUP_PROFILE_NONE)
    if not args.json:
        print(S.SETUP_LA_HINT)
    if args.json:
        print(json.dumps(result))
    return 0


def cmd_install(args):
    """F10: install (and bootstrap) the user LaunchAgent for autostart in the GUI session."""
    from . import launchagent as la
    cfg = config.load()
    config.ensure_home()
    if getattr(args, "uninstall", False):
        ok, path = la.uninstall(cfg)
        if not args.json:
            print(S.LA_UNINSTALLED.format(label=cfg.get("launchagent_label")))
        else:
            print(json.dumps({"uninstalled": True, "label": cfg.get("launchagent_label")}))
        return 0
    ok, detail = la.install(cfg)
    if not ok:
        print(S.LA_BOOTSTRAP_FAIL.format(err=detail), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"installed": True, "label": cfg.get("launchagent_label"),
                          "plist": detail}))
    else:
        print(S.LA_INSTALLED.format(label=cfg.get("launchagent_label"), path=detail))
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
    pa.add_argument("--scope", help="gate card: what is being decided")
    pa.add_argument("--bar", help="gate card: the quality bar to check against")
    pa.add_argument("--authority", help="gate card: what the worker is/ isn't allowed to do")
    pa.add_argument("--cost", help="gate card: the cost of getting this decision wrong")
    pa.add_argument("--irreversible", action="store_true",
                    help="gate card: this decision is irreversible (never batch-approved)")
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

    # F10: kill switch
    pstop = sub.add_parser("stop", help="stop all workers now; their tasks return to ready")
    pstop.add_argument("--json", action="store_true")
    pstop.set_defaults(func=cmd_stop)

    # F10: dispatcher loop (run by the LaunchAgent)
    pd = sub.add_parser("daemon", help="run the dispatcher loop (used by the LaunchAgent)")
    pd.add_argument("--once", action="store_true", help="run a single dispatch tick and exit")
    pd.set_defaults(func=cmd_daemon)

    # F10: setup (Terminal profile husk fix) + install (LaunchAgent autostart)
    pset = sub.add_parser("setup", help="configure the Terminal profile so husk windows close")
    pset.add_argument("--revert", action="store_true",
                     help="restore the profile's previous shellExitAction from the orc backup")
    pset.add_argument("--json", action="store_true")
    pset.set_defaults(func=cmd_setup)

    pin = sub.add_parser("install", help="install the user LaunchAgent (autostart in GUI session)")
    pin.add_argument("--uninstall", action="store_true", help="bootout and remove the LaunchAgent")
    pin.add_argument("--json", action="store_true")
    pin.set_defaults(func=cmd_install)

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
