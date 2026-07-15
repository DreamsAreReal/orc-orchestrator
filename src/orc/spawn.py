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


def build_start_command(project, claude_bin, prompt):
    """The shell line executed inside the new terminal tab."""
    # cd into the project, then launch interactive claude with the start prompt.
    return "cd %s && %s %s" % (
        shlex.quote(project),
        shlex.quote(claude_bin),
        shlex.quote(prompt),
    )


def spawn_terminal(project, claude_bin, prompt):
    """Open a new Terminal tab, cd into project, run interactive claude.

    Returns (ok, detail). The returned detail is the osascript stdout (tab id) or the
    error text. This spawns an interactive session; the dispatcher captures the PID
    out-of-band (F4) by matching claude processes with this project cwd.
    """
    cmd = build_start_command(project, claude_bin, prompt)
    # AppleScript string escaping: wrap the shell command as a do-script argument.
    esc = cmd.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Terminal"\n'
        "    activate\n"
        '    do script "%s"\n'
        "end tell" % esc
    )
    p = _osascript(script)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()
    return True, p.stdout.strip()


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
