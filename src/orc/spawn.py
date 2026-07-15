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
