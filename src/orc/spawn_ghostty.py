"""Ghostty spawner (F15): spawn workers in Ghostty and close their window cleanly.

The pain (user feedback 2026-07-15): spawning into Terminal.app leaves husk windows with
a "confirm close" dialog, because Terminal's shellExitAction=keep-window vetoes a scripted
close and killing by tty does not make the shell exit cleanly. They accumulated by the
dozen. Ghostty -- the user's actual terminal -- closes a surface automatically when its
`-e` command exits (spiked: a window whose command finishes disappears, no husk, no
dialog). So in Ghostty we STOP a worker by making its command exit (kill the process),
and the window closes itself.

Spawn:  `open -na Ghostty.app --args -e bash -lc '<cmd>'`
Identity: the command exports ORC_SESSION=<session>, which appears verbatim in the
worker process's argv -- so we can find its PID (pgrep -f) and stop it (pkill -f), and the
window self-closes on exit. There is no AppleScript window id (Ghostty does not expose one
reliably); the session marker is the handle instead. This is more robust than a window id.

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


def build_inner_command(project, claude_bin, prompt, session):
    """The shell program Ghostty runs via `-e bash -lc '<this>'`.

    Exports ORC_SESSION (heartbeat hooks + the find/kill handle), cd's into the project,
    then launches interactive claude. A verification seam (ORC_SPAWN_CMD_OVERRIDE) swaps
    the claude launch for a literal command so the spawn/identify/stop path is testable
    without a real claude.
    """
    export = "export ORC_SESSION=%s; " % shlex.quote(str(session))
    override = os.environ.get("ORC_SPAWN_CMD_OVERRIDE")
    if override:
        return "%scd %s && %s" % (export, shlex.quote(project), override)
    return "%scd %s && exec %s %s" % (
        export, shlex.quote(project), shlex.quote(claude_bin), shlex.quote(prompt))


def spawn_ghostty(project, claude_bin, prompt, session):
    """Open a Ghostty window running the worker. Returns (ok, detail).

    On success `detail` is the session marker (the stable handle used to stop the worker
    and, by exiting, close its window). Unlike Terminal there is no window id -- the marker
    is the identifier stored in shift.json as tab_id so close_ghostty can find it.
    """
    inner = build_inner_command(project, claude_bin, prompt, session)
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
