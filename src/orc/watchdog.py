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
    """True if the last k heartbeat hashes are all identical (a tool-loop). k<=1 disables."""
    if not heartbeats or k is None or k <= 1:
        return False
    if len(heartbeats) < k:
        return False
    last = [h[2] for h in heartbeats[-k:]]
    return len(set(last)) == 1


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
    if busy is None:
        busy, _ = in_flight(session, now=now, max_tool_seconds=silence_seconds * 4)
    if detect_silence(beats, busy, now, silence_seconds):
        return VERDICT_SILENCE
    return VERDICT_OK


# --- external post-condition check (anti-hallucinated-success) --------------- #
def external_progress(project, since_epoch=None):
    """Check REAL progress on disk, not the worker's self-report (design.md P6).

    Returns True if the project shows genuine forward motion: a git commit newer than
    since_epoch, or uncommitted changes staged/working (the worker is producing output).
    A stuck worker that has made NO real change fails this -> safe to kill and restart.
    """
    from . import gitutil
    if not gitutil.is_repo(project):
        # not a repo: fall back to "any recent file write under the project"
        return _recent_file(project, since_epoch)
    # a commit after the worker started = real checkpoint
    last = gitutil.head_commit_epoch(project)
    if last is not None and since_epoch is not None and last > since_epoch:
        return True
    # or uncommitted work in progress (dirty tree beyond our own settings artifact)
    dirty = gitutil.dirty_paths(project)
    foreign = [p for p in dirty if not p.startswith(".claude/")]
    return bool(foreign)


def _recent_file(project, since_epoch):
    if not os.path.isdir(project) or since_epoch is None:
        return False
    for root, _dirs, files in os.walk(project):
        if ".git" in root:
            continue
        for fn in files:
            try:
                if os.path.getmtime(os.path.join(root, fn)) > since_epoch:
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
            progressing = external_progress(w.get("project", ""), since_epoch=started)
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
