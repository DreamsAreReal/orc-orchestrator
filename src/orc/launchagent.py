"""F10: user LaunchAgent that autostarts the orc dispatcher in the GUI (Aqua) session.

Why a user LaunchAgent (not cron / LaunchDaemon): the OAuth token lives in the login
Keychain, which is only reachable from a GUI (Aqua) session. The probe
(.spikes/probe/launchagent.md) proved a user LaunchAgent in the Aqua session has full
Keychain access and a working `claude auth` (auth_exit=0) -- provided claude is called
by ABSOLUTE PATH and PATH is set in the plist, because LaunchAgents do NOT inherit the
interactive shell PATH.

This module builds the plist and wraps `launchctl bootstrap/bootout/kickstart/print`.
It never hard-codes calibration: the label, PATH and claude binary all come from config.
"""
import os
import plistlib
import subprocess

from . import config


def plist_path(cfg):
    label = cfg.get("launchagent_label", "com.user.orc")
    return os.path.expanduser("~/Library/LaunchAgents/%s.plist" % label)


def orc_bin():
    """Absolute path to the `orc` entry point (bin/orc in this checkout)."""
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo = os.path.dirname(src)  # src/ -> repo root
    return os.path.join(repo, "bin", "orc")


def build_plist_dict(cfg):
    """Assemble the LaunchAgent plist as a dict (F10).

    The dispatcher runs as `orc daemon`. Key choices, each proven by the probe:
      - LimitLoadToSessionType = Aqua  -> GUI session, Keychain reachable.
      - EnvironmentVariables.PATH      -> LaunchAgents do not inherit shell PATH; set it
                                          so bd / ccusage / node / claude resolve.
      - RunAtLoad                      -> start the dispatcher when the agent loads (login).
      - KeepAlive (Crashed)            -> restart the daemon if it crashes, but not if it
                                          exits cleanly (a deliberate `orc stop`).
    Absolute paths only (no reliance on cwd or PATH for the program itself).
    """
    label = cfg.get("launchagent_label", "com.user.orc")
    path = cfg.get("launchagent_path",
                   "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    home = config.orc_home()
    log_dir = os.path.join(home, "log")
    return {
        "Label": label,
        "ProgramArguments": ["/bin/bash", orc_bin(), "daemon"],
        "RunAtLoad": True,
        # Aqua = GUI login session; the only context where the login Keychain (and thus
        # `claude auth`) is available to the dispatcher (probe: auth_exit=0).
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {
            "PATH": path,
            # Point child processes at the same hub the CLI uses (no surprise home).
            "ORC_HOME": home,
        },
        # Restart only on a crash, never on a clean exit (so `orc stop` truly stops it).
        "KeepAlive": {"Crashed": True},
        # Throttle restarts so a crash-loop cannot burn the usage pool.
        "ThrottleInterval": 30,
        "StandardOutPath": os.path.join(log_dir, "daemon.out.log"),
        "StandardErrorPath": os.path.join(log_dir, "daemon.err.log"),
    }


def write_plist(cfg):
    """Write the LaunchAgent plist to ~/Library/LaunchAgents. Returns the path."""
    path = plist_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.join(config.orc_home(), "log"), exist_ok=True)
    data = build_plist_dict(cfg)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        plistlib.dump(data, f)
    os.replace(tmp, path)
    return path


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain_target(cfg):
    return "gui/%d/%s" % (os.getuid(), cfg.get("launchagent_label", "com.user.orc"))


def is_loaded(cfg):
    """True if the LaunchAgent is currently bootstrapped in the gui domain."""
    p = _launchctl("print", _domain_target(cfg))
    return p.returncode == 0


def last_exit_code(cfg):
    """Best-effort: the last exit status reported by launchctl print, or None."""
    p = _launchctl("print", _domain_target(cfg))
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("last exit code ="):
            val = line.split("=", 1)[1].strip()
            try:
                return int(val)
            except ValueError:
                return val
    return None


def install(cfg):
    """Write the plist and bootstrap the LaunchAgent into the gui session (F10).

    Idempotent: if already loaded, boots it out first so the fresh plist takes effect.
    Returns (ok, detail). `bootstrap gui/<uid>` is the modern launchctl load path (the
    probe confirmed it on this machine).
    """
    path = write_plist(cfg)
    if is_loaded(cfg):
        _launchctl("bootout", _domain_target(cfg))
    p = _launchctl("bootstrap", "gui/%d" % os.getuid(), path)
    if p.returncode != 0:
        # already-bootstrapped race is not a failure
        err = (p.stderr or p.stdout).strip()
        if "already" not in err.lower() and not is_loaded(cfg):
            return False, err
    _launchctl("enable", _domain_target(cfg))
    return True, path


def uninstall(cfg):
    """Bootout the LaunchAgent and remove its plist (F10). Returns (ok, detail)."""
    if is_loaded(cfg):
        _launchctl("bootout", _domain_target(cfg))
    path = plist_path(cfg)
    existed = os.path.exists(path)
    if existed:
        os.remove(path)
    return True, path


def kickstart(cfg):
    """Force the LaunchAgent to run now (used by the verification script)."""
    return _launchctl("kickstart", "-k", _domain_target(cfg))
