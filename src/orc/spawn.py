"""Spawn a REAL interactive Terminal running `claude` in a project (osascript).

Not headless: the worker is an interactive Claude Code session in Terminal.app, per
the hard requirement in the brief. The dispatcher records the worker PID separately
(F4); this module only opens the terminal and runs the start command.

The generated worker `.claude/settings.json` (deny-walls, F1) must already be written
in the project before spawning.
"""
import os
import shlex
import subprocess


def _osascript(script):
    return subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True
    )


# --------------------------------------------------------------------------- #
# Backend selector (F15): Ghostty (default, closes windows cleanly) or Terminal.
# --------------------------------------------------------------------------- #
# The dispatcher calls spawn_worker / close_worker; this layer routes to the configured
# terminal backend. Ghostty is the default because it closes a worker's window on exit
# (no husk, no confirm dialog); Terminal.app is kept as a fallback.
def _backend(cfg):
    backend = (cfg or {}).get("terminal", "ghostty")
    if backend == "ghostty":
        from . import spawn_ghostty
        if spawn_ghostty.ghostty_available():
            return "ghostty"
        # Ghostty requested but not installed -> fall back to Terminal so spawns still work
        return "terminal"
    return "terminal"


def spawn_worker(cfg, project, claude_bin, prompt, session):
    """Spawn a worker in the configured terminal backend. Returns (ok, handle).

    `handle` is stored in shift.json as tab_id: a Terminal window id, or (Ghostty) the
    session marker used to find/stop the worker. close_worker understands both.
    """
    if _backend(cfg) == "ghostty":
        from . import spawn_ghostty
        return spawn_ghostty.spawn_ghostty(project, claude_bin, prompt, session)
    return spawn_terminal(project, claude_bin, prompt, session=session)


def close_worker(cfg, handle, session=None):
    """Stop a worker and close its window (F14/F15). Returns {"killed","window_closed"}.

    Ghostty: kill the session-marked process -> the window self-closes (clean, 0 husk).
    Terminal: kill by tty + best-effort window close (may leave a husk on keep profiles).
    """
    if _backend(cfg) == "ghostty":
        from . import spawn_ghostty
        return spawn_ghostty.close_ghostty(session)
    return close_window(handle)


def worker_pid(cfg, project, session, handle=None):
    """Capture a real worker PID in the configured backend (F8). None if not found."""
    if _backend(cfg) == "ghostty":
        from . import spawn_ghostty
        pid = spawn_ghostty.pid_for_session(session)
        if pid is not None:
            return pid
        # fall through to cwd matching as a backstop
    if handle is not None:
        pid = pid_on_window(handle)
        if pid is not None:
            return pid
    pids = worker_pids(project)
    return pids[0] if pids else None


def build_start_command(project, claude_bin, prompt, session=None):
    """The shell line executed inside the new terminal tab.

    Normally: cd into the project, then launch interactive claude with the start prompt.
    When `session` is given, ORC_SESSION is exported first so the worker's heartbeat hooks
    (F7) key their heartbeat / in-flight marker to a session id the dispatcher also knows.

    Verification seam: ORC_SPAWN_CMD_OVERRIDE replaces the in-tab program with a literal
    shell command (still run in the project cwd). The loop-close E2E uses it to drive the
    worker's on-disk output deterministically -- the spawn / window-id / tty / kill / close
    path stays 100% real; only the program inside the tab is made deterministic so the test
    does not hinge on live-model latency or an exhausted usage window. Not used in real
    shifts (the dispatcher always passes a claude prompt).
    """
    prefix = ""
    if session:
        prefix = "export ORC_SESSION=%s; " % shlex.quote(str(session))
    override = os.environ.get("ORC_SPAWN_CMD_OVERRIDE")
    if override:
        return "cd %s && %s%s" % (shlex.quote(project), prefix, override)
    # cd into the project, then launch interactive claude with the start prompt.
    return "%scd %s && %s %s" % (
        prefix,
        shlex.quote(project),
        shlex.quote(claude_bin),
        shlex.quote(prompt),
    )


def spawn_terminal(project, claude_bin, prompt, session=None):
    """Open a new Terminal window, cd into project, run interactive claude.

    Returns (ok, detail). On success `detail` is the numeric Terminal WINDOW ID of the
    freshly opened window — a stable identifier the dispatcher stores in shift.json so it
    can (a) show a real id instead of `None` and (b) close the worker's window when the
    task reaches a terminal status (F14). One `do script` opens one window, so closing by
    window id never touches another worker's tab (verified on 2.1.193 / Terminal.app).

    This spawns an interactive session; the PID is discovered out-of-band (F4) by matching
    claude processes with this project cwd (RAM is the mutex; there is one worker).
    """
    cmd = build_start_command(project, claude_bin, prompt, session=session)
    # AppleScript string escaping: wrap the shell command as a do-script argument.
    esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
    # `close tab` is not understood by Terminal.app; only `close (window id N)` works, and
    # each `do script` opens its own window — so we return the window id, not the tab.
    script = (
        'tell application "Terminal"\n'
        "    activate\n"
        '    set t to do script "%s"\n'
        "    set wid to id of (window 1 whose tabs contains t)\n"
        "    return wid as text\n"
        "end tell" % esc
    )
    p = _osascript(script)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()
    return True, p.stdout.strip()


def window_tty(window_id):
    """Return the tty device path of the worker's Terminal tab, or None (best effort)."""
    try:
        wid = int(str(window_id).strip())
    except (TypeError, ValueError):
        return None
    p = _osascript(
        'tell application "Terminal" to return tty of tab 1 of window id %d' % wid)
    if p.returncode != 0:
        return None
    tty = (p.stdout or "").strip()
    return tty or None


def pids_on_tty(tty):
    """Return the PIDs of processes attached to a tty (newest last). [] on failure."""
    if not tty or not tty.startswith("/dev/"):
        return []
    ttyname = tty[len("/dev/"):]
    out = subprocess.run(["ps", "-t", ttyname, "-o", "pid="],
                         capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.strip().isdigit()]


def pid_on_window(window_id, retries=10, delay=0.3):
    """Robustly capture the worker's PID via the spawned window's tty (F8 fix).

    The eval found `pid` could be None because worker_pids() matches process cwd via lsof
    immediately after spawn, before the interactive shell has `cd`'d -- a race. Resolving
    the window's tty and reading the process ON that tty is race-free and reliable: the tty
    exists the moment the window opens. We retry briefly because the shell/claude process
    may take a fraction of a second to attach. Returns an int PID or None.

    Prefers a `claude` process on the tty; falls back to the newest non-shell PID (the
    verification seam runs a plain shell command, which is still a real, killable worker).
    """
    import time as _t
    for _ in range(max(1, retries)):
        tty = window_tty(window_id)
        pids = pids_on_tty(tty)
        if pids:
            # prefer a claude process; else the highest PID (the child of the login shell)
            claude_pid = _first_claude_pid(pids)
            return claude_pid if claude_pid is not None else max(pids)
        _t.sleep(delay)
    return None


def _first_claude_pid(pids):
    """Return the first PID whose command is claude, or None."""
    for pid in pids:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True)
        if "claude" in (out.stdout or "").lower():
            return pid
    return None


def _kill_tty_processes(tty):
    """Terminate the processes running on a tty (the finished worker). Returns count killed.

    This is the SUBSTANTIVE half of closing a worker: on this 8GB machine a lingering
    interactive claude holds gigabytes; the loop is only truly closed when the worker
    process is gone and its RAM is freed. Killing by tty is reliable and precise (it only
    touches the worker's own session), unlike scripted window-close which the Terminal
    profile (shellExitAction) can veto.
    """
    if not tty or not tty.startswith("/dev/"):
        return 0
    ttyname = tty[len("/dev/"):]
    out = subprocess.run(["ps", "-t", ttyname, "-o", "pid="],
                         capture_output=True, text=True)
    killed = 0
    for pid in out.stdout.split():
        pid = pid.strip()
        if pid.isdigit():
            try:
                os.kill(int(pid), 15)   # SIGTERM the worker's session processes
                killed += 1
            except OSError:
                pass
    return killed


def close_window(window_id):
    """Stop a finished worker and close its Terminal window by window id (F14).

    Two steps, reliability first:
      1. resolve the tab's tty and SIGTERM its processes -- this reliably STOPS the
         lingering worker and frees its RAM (the substantive requirement);
      2. best-effort `close (window id)` to remove the (now empty) window. On profiles
         with shellExitAction = "keep window open" the empty husk may remain; that is
         cosmetic (no process, no RAM) and depends on a user-owned Terminal setting.

    Returns a dict {"killed": <n>, "window_closed": <bool>} so callers can assert the real
    outcome (worker terminated) rather than only the cosmetic window state.
    """
    result = {"killed": 0, "window_closed": False}
    if window_id in (None, "", "None"):
        return result
    try:
        wid = int(str(window_id).strip())
    except (TypeError, ValueError):
        return result

    tty = window_tty(wid)
    result["killed"] = _kill_tty_processes(tty)

    p = _osascript('tell application "Terminal" to close (window id %d) saving no' % wid)
    if p.returncode != 0:
        err = (p.stderr or "").lower()
        # a missing window means the desired state (window gone) already holds
        result["window_closed"] = ("doesn" in err or "not " in err or "invalid" in err)
    else:
        # verify the window actually went away (close can silently no-op on keep profiles)
        chk = _osascript(
            'tell application "Terminal" to return (exists (window id %d))' % wid)
        result["window_closed"] = (chk.stdout or "").strip().lower() == "false"
    return result


def worker_pids(project):
    """Return PIDs of claude processes whose cwd is `project` (best effort).

    Uses `lsof` to match the process working directory; the dispatcher uses this to
    register/monitor/kill only its own workers (kill-by-own-PID discipline, F4/F8).
    """
    real = os.path.realpath(project)
    pids = []
    try:
        # pgrep for claude, then confirm cwd via lsof -a -d cwd.
        pg = subprocess.run(["pgrep", "-f", "claude"], capture_output=True, text=True)
        for pid in pg.stdout.split():
            pid = pid.strip()
            if not pid.isdigit():
                continue
            lo = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                capture_output=True, text=True,
            )
            for line in lo.stdout.splitlines():
                if line.startswith("n") and os.path.realpath(line[1:]) == real:
                    pids.append(int(pid))
                    break
    except Exception:
        return pids
    return pids
