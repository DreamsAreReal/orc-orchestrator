"""Ghostty spawner (F15) -- OPT-IN, NOT the default. See the status note below.

STATUS (R-M2, evaluator round 2): this backend is NOT the shipped default and is NOT
proven to work on this machine. On Ghostty 1.3.1 `open -na Ghostty.app --args -e <cmd>`
opens an EMPTY window: `-e` never spawns the shell, so the worker command never runs
(exhaustively probed in .spikes/probe/ghostty-exec.md, variants A-M all NOT EXECUTED).
The earlier claim that "Ghostty closes the surface on command exit (0 husk)" was also
disproved (husk windows were found). The DEFAULT backend is Terminal.app (config
`terminal: terminal`), which reliably executes the worker command.

This module is retained as an opt-in path (`terminal: ghostty`) for a future Ghostty build
or a correct invocation proven by re-running the same spike. It MUST NOT be selected until
that spike passes. The identity/kill logic (ORC_SESSION marker -> pgrep/pkill) is sound; it
is the `-e` execution that is broken in this Ghostty build.

Spawn:  `open -na Ghostty.app --args -e bash -lc '<cmd>'` (does not execute on 1.3.1)
Identity: the command exports ORC_SESSION=<session>, which would appear in the worker
process's argv -- so we could find its PID (pgrep -f) and stop it (pkill -f). There is no
AppleScript window id; the session marker is the handle.

python 3.9-compatible.
"""
import os
import shlex
import subprocess


GHOSTTY_APP = "Ghostty.app"


def ghostty_available():
    """True if Ghostty is installed (an app bundle we can `open -na`)."""
    for path in ("/Applications/Ghostty.app",
                 os.path.expanduser("~/Applications/Ghostty.app")):
        if os.path.isdir(path):
            return True
    # fall back to a launcher on PATH
    import shutil
    return shutil.which("ghostty") is not None


def _session_marker(session):
    """The exact string that identifies this worker in its argv (find/kill handle)."""
    return "ORC_SESSION=%s" % session


def build_inner_command(project, claude_bin, prompt, session, cfg=None):
    """The shell program Ghostty runs via `-e bash -lc '<this>'`.

    Exports ORC_SESSION (heartbeat hooks + the find/kill handle), cd's into the project,
    then launches interactive claude. A verification seam (ORC_SPAWN_CMD_OVERRIDE) swaps
    the claude launch for a literal command so the spawn/identify/stop path is testable
    without a real claude. F13: wrapped under the OS-sandbox (seatbelt) when enabled, same
    as the Terminal backend.
    """
    from . import worker_walls as _ww
    # G0c: same git-push credential strip as the Terminal backend (defense-in-depth).
    export = _ww.push_neutralizing_export_prefix()
    export += "export ORC_SESSION=%s; " % shlex.quote(str(session))
    override = os.environ.get("ORC_SPAWN_CMD_OVERRIDE")
    if override:
        inner = "%scd %s && %s" % (export, shlex.quote(project), override)
    else:
        inner = "%scd %s && exec %s %s" % (
            export, shlex.quote(project), shlex.quote(claude_bin), shlex.quote(prompt))
    from . import spawn as _spawn
    return _spawn._maybe_sandbox(cfg, project, inner)


def spawn_ghostty(project, claude_bin, prompt, session, cfg=None):
    """Open a Ghostty window running the worker. Returns (ok, detail).

    On success `detail` is the session marker (the stable handle used to stop the worker
    and, by exiting, close its window). Unlike Terminal there is no window id -- the marker
    is the identifier stored in shift.json as tab_id so close_ghostty can find it.
    """
    inner = build_inner_command(project, claude_bin, prompt, session, cfg=cfg)
    # open -na launches a NEW Ghostty instance/window with the -e command.
    argv = ["open", "-na", GHOSTTY_APP, "--args", "-e",
            "bash", "-lc", inner]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001 - report, never raise into the dispatcher
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()
    return True, _session_marker(session)


def worker_pids_by_session(session):
    """PIDs of processes whose argv carries this worker's session marker."""
    marker = _session_marker(session)
    try:
        p = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    except Exception:
        return []
    return [int(x) for x in p.stdout.split() if x.strip().isdigit()]


def pid_for_session(session):
    """A single live worker PID for this session, or None (F8 PID capture in Ghostty)."""
    pids = worker_pids_by_session(session)
    return pids[0] if pids else None


def close_ghostty(session):
    """Stop the worker (kill its process) so Ghostty closes the window cleanly (F15).

    Returns {"killed": <n>, "window_closed": <bool>}. window_closed is True when no process
    carrying the session marker remains -- because Ghostty closes the surface once its `-e`
    command exits, "no process left" is exactly "window gone" (0 husk, 0 dialog).
    """
    result = {"killed": 0, "window_closed": False}
    if not session:
        return result
    pids = worker_pids_by_session(session)
    for pid in pids:
        try:
            os.kill(pid, 15)   # SIGTERM -> the -e command exits -> window self-closes
            result["killed"] += 1
        except OSError:
            pass
    # confirm the window is gone: no marked process remains
    import time
    for _ in range(10):
        if not worker_pids_by_session(session):
            result["window_closed"] = True
            break
        time.sleep(0.2)
    else:
        # a stubborn process: SIGKILL as a last resort, then re-check
        for pid in worker_pids_by_session(session):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        result["window_closed"] = not worker_pids_by_session(session)
    return result
