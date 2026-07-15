"""Watchdog (F7): detect a stuck worker (tool-loop or silence) and recover safely.

Two failure modes, two signals:

  LOOP    -- the worker keeps issuing the SAME tool call over and over (a meltdown).
             Detected when the last K heartbeat hashes are identical (K from config).

  SILENCE -- the worker has emitted no heartbeat for too long AND is not currently
             inside a tool call. The PreToolUse hook writes a "tool-in-flight" marker
             and the PostToolUse hook clears it, so a legitimately long-running tool
             (a 20-minute build, a slow test) is NOT mistaken for a hang: while the
             marker is present the worker is WORKING, not silent. This is what gives
             0 false kills on a live >=2-minute Bash call (acceptance F7).

Recovery is deliberate and bounded (design.md, anti-Goodhart P6):
  detect -> EXTERNAL post-condition check (git commits / artifacts on disk, NOT the
  worker's self-report) -> kill -> restart FRESH from STATE.md -> cap on restarts ->
  escalate to the operator when the cap is hit.

Heartbeat wire format (one line per PostToolUse):  "<epoch> <tool> <arg-hash>"
In-flight marker: a file ~/.orc/hb/<session>.inflight holding the start epoch; present
between PreToolUse and PostToolUse of a single tool call.

The detection functions here are pure (they take parsed heartbeat data + clocks as
arguments) so loops and silence are testable on synthetic logs without a real worker.
python 3.9-compatible.
"""
import os
import time
import hashlib

from . import config


VERDICT_OK = "ok"
VERDICT_LOOP = "loop"
VERDICT_SILENCE = "silence"


# --- heartbeat + marker paths ------------------------------------------------ #
def heartbeat_path(session):
    return os.path.join(config.heartbeat_dir(), "%s.log" % _safe(session))


def marker_path(session):
    return os.path.join(config.heartbeat_dir(), "%s.inflight" % _safe(session))


def _safe(session):
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(session))


def arg_hash(tool, tool_input):
    """Stable short hash of a tool call's identity (tool name + its arguments).

    Identical (tool, args) across calls -> identical hash -> loop signal. We hash a
    canonical string of the sorted arguments so key ordering does not matter.
    """
    try:
        import json
        payload = json.dumps(tool_input or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(tool_input)
    h = hashlib.sha1(("%s|%s" % (tool, payload)).encode("utf-8")).hexdigest()
    return h[:12]


# --- hook entrypoints (run inside the worker via its settings.json) ---------- #
def record_heartbeat(session, tool, tool_input, now=None):
    """PostToolUse: append a heartbeat line and clear the in-flight marker."""
    config.ensure_home()
    ts = int(now if now is not None else time.time())
    line = "%d %s %s\n" % (ts, tool or "?", arg_hash(tool, tool_input))
    with open(heartbeat_path(session), "a") as f:
        f.write(line)
    _clear_marker(session)


def mark_in_flight(session, tool, now=None):
    """PreToolUse: write the tool-in-flight marker (a tool call just STARTED)."""
    config.ensure_home()
    ts = int(now if now is not None else time.time())
    tmp = marker_path(session) + ".tmp"
    with open(tmp, "w") as f:
        f.write("%d %s\n" % (ts, tool or "?"))
    os.replace(tmp, marker_path(session))


def _clear_marker(session):
    try:
        os.remove(marker_path(session))
    except OSError:
        pass


# --- Claude Code hook entrypoints (invoked from the worker settings.json) ----- #
def _hook_session(data):
    """Derive a stable per-worker session id from the Claude Code hook payload.

    Claude Code passes session_id in the hook stdin JSON; ORC_SESSION (set in the worker
    env by the dispatcher) overrides it so the dispatcher and worker agree on the id.
    """
    return os.environ.get("ORC_SESSION") or data.get("session_id") or "worker"


def pretooluse_hook():
    """PreToolUse: mark a tool call in flight (distinguishes work from silence)."""
    import sys
    import json as _json
    try:
        data = _json.load(sys.stdin)
    except Exception:
        data = {}
    session = _hook_session(data)
    mark_in_flight(session, data.get("tool_name", "?"))
    sys.exit(0)


def posttooluse_hook():
    """PostToolUse: record a heartbeat and clear the in-flight marker."""
    import sys
    import json as _json
    try:
        data = _json.load(sys.stdin)
    except Exception:
        data = {}
    session = _hook_session(data)
    record_heartbeat(session, data.get("tool_name", "?"), data.get("tool_input") or {})
    sys.exit(0)


def in_flight(session, now=None, max_tool_seconds=None):
    """Return (busy, started_epoch) — is a tool call currently in flight?

    A stale marker older than max_tool_seconds is ignored (a tool that never posted a
    PostToolUse, e.g. the worker died mid-tool) so silence detection still fires. When
    max_tool_seconds is None there is no staleness bound (any marker means busy).
    """
    path = marker_path(session)
    try:
        with open(path) as f:
            first = f.readline().split()
        started = int(first[0]) if first else None
    except (OSError, ValueError, IndexError):
        return False, None
    if started is None:
        return False, None
    if max_tool_seconds is not None:
        base = now if now is not None else time.time()
        if base - started > max_tool_seconds:
            return False, started    # stale marker -> treat as not busy
    return True, started


# --- worker settings hook blocks (F7 heartbeat) ------------------------------ #
def _hb_command(entry):
    """Command that invokes a watchdog hook entrypoint with a self-contained sys.path."""
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src
    return ('python3 -c "import sys; sys.path.insert(0, r\'%s\'); '
            'from orc.watchdog import %s; %s()"' % (src, entry, entry))


def heartbeat_hook_blocks():
    """Return {PreToolUse:[...], PostToolUse:[...]} for the worker heartbeat (F7).

    PreToolUse writes the tool-in-flight marker; PostToolUse writes the heartbeat and
    clears the marker. Matches every tool so any activity is a heartbeat.
    """
    return {
        "PreToolUse": [
            {"matcher": "*",
             "hooks": [{"type": "command", "command": _hb_command("pretooluse_hook")}]}
        ],
        "PostToolUse": [
            {"matcher": "*",
             "hooks": [{"type": "command", "command": _hb_command("posttooluse_hook")}]}
        ],
    }


# --- heartbeat parsing ------------------------------------------------------- #
def read_heartbeats(session):
    """Return the parsed heartbeat lines: [(epoch, tool, hash), ...] (oldest first)."""
    path = heartbeat_path(session)
    out = []
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        out.append((int(parts[0]), parts[1], parts[2]))
                    except ValueError:
                        continue
    except OSError:
        return []
    return out


# --- detection (pure) -------------------------------------------------------- #
def detect_loop(heartbeats, k):
    """True if the last k heartbeat hashes are all identical (a tool-loop). k<=1 disables.

    This is the STRICT signal (K identical in a row). It only catches the naive meltdown
    that repeats one exact call. Alternation (A/B/A/B) and short rotations (A/B/C) slip
    past it -- see detect_cycle for the window-entropy signal that catches those (P1 fix,
    from E3 fuzz watchdog.py:201: "detect requires k CONSECUTIVE identical hashes").
    """
    if not heartbeats or k is None or k <= 1:
        return False
    if len(heartbeats) < k:
        return False
    last = [h[2] for h in heartbeats[-k:]]
    return len(set(last)) == 1


def detect_cycle(heartbeats, window, max_unique):
    """True if the last `window` heartbeats cycle through <= `max_unique` distinct calls.

    Catches the short-cycle meltdowns detect_loop misses (P1, from E3 fuzz): a spin-loop
    that FLIPS between a handful of identical calls -- A/B/A/B/... or A/B/C rotation -- never
    has K identical hashes in a row, so the strict detector says "not a loop" forever. Here
    we look at a window of the last N heartbeats: if it is long enough (>= `window`) yet the
    worker only ever issues at most `max_unique` distinct tool calls across the whole window,
    it is churning in place, not progressing. A worker doing real work produces a stream of
    DIFFERENT calls (edit this file, run that test, read the next module) -- its unique count
    in a window of, say, 8 is well above 2-3.

    window<=1 or max_unique<=0 disables. Requires at least `window` beats (do not fire early
    on a short log). The false-positive guard is NOT here: supervise() still runs the EXTERNAL
    post-condition check (external_progress) before any kill, so a worker that genuinely
    changed files on disk is spared even if its heartbeat pattern looks cyclic -- real
    progress wins over the heuristic (this is why a live tool is never falsely killed).
    """
    if not heartbeats or window is None or window <= 1 or max_unique is None or max_unique <= 0:
        return False
    if len(heartbeats) < window:
        return False
    last = [h[2] for h in heartbeats[-window:]]
    return len(set(last)) <= max_unique


def detect_silence(heartbeats, busy, now, silence_seconds):
    """True if the worker is silent past the threshold AND not inside a tool call.

    busy=True (a tool is in flight) NEVER reports silence -- that is the guard against
    killing a legitimately long-running tool (0 false kills, acceptance F7).
    """
    if busy:
        return False
    if not heartbeats:
        # no heartbeat ever: only silence if we have waited past the threshold from a
        # known start; without any timeline we conservatively say not-silent here (the
        # caller passes started time via a synthetic first beat in tests).
        return False
    last_ts = heartbeats[-1][0]
    return (now - last_ts) >= silence_seconds


def classify(session, cfg, now=None, silence_seconds=120, busy=None):
    """Classify a worker's health: VERDICT_OK / VERDICT_LOOP / VERDICT_SILENCE.

    silence_seconds default 120s (2 min): a live >=2-minute tool call is protected by the
    in-flight marker, not by this threshold. busy can be injected for tests; otherwise it
    is read from the marker with a staleness bound of silence_seconds*4.
    """
    now = time.time() if now is None else now
    beats = read_heartbeats(session)
    k = cfg.get("loop_hash_k", 4)
    if detect_loop(beats, k):
        return VERDICT_LOOP
    # short-cycle meltdown (A/B/A/B, A/B/C) that detect_loop misses (P1, E3 fuzz).
    win = cfg.get("loop_cycle_window", 8)
    mu = cfg.get("loop_cycle_max_unique", 3)
    if detect_cycle(beats, win, mu):
        return VERDICT_LOOP
    if busy is None:
        busy, _ = in_flight(session, now=now, max_tool_seconds=silence_seconds * 4)
    if detect_silence(beats, busy, now, silence_seconds):
        return VERDICT_SILENCE
    return VERDICT_OK


# --- external post-condition check (anti-hallucinated-success) --------------- #
# Paths orc itself generates in a project -- NOT the worker's real deliverable. A dirty
# tree consisting only of these is NOT external progress (a worker cannot pass the DONE
# wall by writing its own docs/tasks/<slug>/STATE.md, which is exactly the reward-hack
# path -- B1). Kept in sync with dispatcher._OURS_PREFIXES:
#   .claude/ -> the F1 deny-walls settings.json; .orc/ -> the F13 seatbelt profile;
#   docs/tasks/ -> the two-layer task mini-pipe (incl. the STATE.md the worker writes to
#   signal DONE -- orc-managed, never counts as the deliverable itself).
_ORC_MANAGED_PREFIXES = (".claude/", ".orc/", "docs/tasks/")


def external_progress(project, since_epoch=None, baseline_rev=None):
    """Check REAL progress on disk, not the worker's self-report (design.md P6 / B1).

    Returns True ONLY on a genuine, non-empty deliverable produced after the worker started:
      - a git commit (newer than since_epoch) that changed at least one NON-empty file
        outside the orc-managed scaffolding -- a `git commit --allow-empty` (0 diff) or a
        commit that only rewrites the worker's own STATE.md does NOT count; OR
      - an uncommitted NON-empty file outside the orc-managed scaffolding -- an empty
        `touch out.txt` (size 0) does NOT count.
    "Something appeared" (empty touch / empty commit / only STATE.md) is a token imitation
    of a fact, not a fact -- exactly the reward-hacking bypass the reverify found. A worker
    that produced NO real content fails this -> its DONE is parked "suspected-fake-done",
    and (watchdog) a stuck worker that made no real change is safe to kill and restart.

    `baseline_rev` is the repo HEAD captured when the worker started; passing it lets the
    commit filter recognize a fast, same-second worker commit while still excluding the
    pre-existing HEAD (P2/3 boundary fix -- see gitutil.commits_since).
    """
    from . import gitutil
    if not gitutil.is_repo(project):
        # not a repo: fall back to "a recent NON-empty file write under the project"
        return _recent_nonempty_file(project, since_epoch)
    # a commit after the worker started counts ONLY if it changed a real, non-empty file
    # (rejects --allow-empty and STATE.md-only commits).
    for rev in gitutil.commits_since(project, since_epoch, baseline_rev=baseline_rev):
        if gitutil.commit_touches_real_files(project, rev,
                                             exclude_prefixes=_ORC_MANAGED_PREFIXES):
            return True
    # or uncommitted work in progress: a NON-empty dirty file beyond our orc-managed
    # artifacts. An empty touch or a STATE.md-only change is not a deliverable.
    return gitutil.dirty_has_nonempty_file(project, exclude_prefixes=_ORC_MANAGED_PREFIXES)


def _recent_nonempty_file(project, since_epoch):
    """Fallback for non-git projects: a NON-empty file written after since_epoch (B1).

    An empty file (touch) never counts -- a real deliverable has content. orc-managed
    scaffolding (.orc/ / .claude/ / docs/tasks/) is excluded so a worker cannot pass the
    gate by (re)writing its own STATE.md, mirroring the git path's exclude_prefixes."""
    root_real = os.path.realpath(project)
    if not os.path.isdir(project) or since_epoch is None:
        return False
    for root, _dirs, files in os.walk(project):
        if ".git" in root:
            continue
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, root_real)
            if rel.startswith(_ORC_MANAGED_PREFIXES):
                continue
            try:
                if os.path.getmtime(full) > since_epoch and os.path.getsize(full) > 0:
                    return True
            except OSError:
                continue
    return False


# --- kill + restart with cap ------------------------------------------------- #
def can_restart(worker, cfg):
    """True if this worker is still under the restart cap (else -> escalate)."""
    cap = cfg.get("restart_cap", 2)
    return int(worker.get("restarts", 0)) < cap


def note_restart(worker):
    worker["restarts"] = int(worker.get("restarts", 0)) + 1
    return worker


def supervise(cfg, hub, state, project_progress=None, now=None,
              silence_seconds=120, verdicts=None):
    """Run one watchdog pass over live workers. Returns a list of actions taken.

    For each worker classified LOOP/SILENCE:
      1. external post-condition check -- if the worker is genuinely making progress
         (fresh commit / real file changes since it started) we DO NOT kill it, even if a
         heartbeat pattern looked loopy; real progress wins over a heuristic.
      2. otherwise kill it (stop the process, free RAM) and, if under the restart cap,
         mark its task for a FRESH restart from STATE.md (bd stays open -> re-served);
         if the cap is hit, escalate: park the task with an escalation note.

    Pure-ish: `verdicts` (session->verdict) and `project_progress` (project->bool) may be
    injected for deterministic tests; otherwise they are computed live. Returns actions as
    dicts: {"task","verdict","action": "restart"|"escalate"|"spared"}.
    """
    from . import shift as shiftmod
    from . import beads
    from . import spawn as spawnmod
    from . import strings as S

    now = time.time() if now is None else now
    actions = []
    for w in list(state.get("workers", [])):
        session = w.get("session") or w.get("task")
        if verdicts is not None:
            verdict = verdicts.get(session, VERDICT_OK)
        else:
            verdict = classify(session, cfg, now=now, silence_seconds=silence_seconds)
        if verdict == VERDICT_OK:
            continue

        # external post-condition check before any kill (never trust self-report)
        started = w.get("started_epoch")
        if project_progress is not None:
            progressing = project_progress.get(w.get("project"), False)
        else:
            progressing = external_progress(w.get("project", ""), since_epoch=started,
                                            baseline_rev=w.get("head_at_start"))
        if progressing:
            actions.append({"task": w.get("task"), "verdict": verdict, "action": "spared"})
            continue

        # no real progress -> kill the worker (stop process, free RAM). F15: close_worker
        # routes to the configured backend so the window closes cleanly (Ghostty) too.
        spawnmod.close_worker(cfg, w.get("tab_id"), session=w.get("session"))
        task_id = w.get("task")
        if can_restart(w, cfg):
            note_restart(w)
            # FRESH restart from STATE.md: keep bd open so the dispatcher re-serves it;
            # drop the dead worker record (a new one is spawned on the next loop tick).
            shiftmod.remove_worker(state, task_id)
            try:
                beads.reopen(hub, task_id)
            except beads.BeadsError:
                pass
            actions.append({"task": task_id, "verdict": verdict, "action": "restart",
                            "restarts": w.get("restarts")})
        else:
            reason = S.WATCHDOG_ESCALATE.format(verdict=verdict,
                                                cap=cfg.get("restart_cap", 2))
            shiftmod.mark_parked(state, task_id, reason)
            try:
                beads.set_status(hub, task_id, "blocked")
            except beads.BeadsError:
                pass
            actions.append({"task": task_id, "verdict": verdict, "action": "escalate"})
    return actions
