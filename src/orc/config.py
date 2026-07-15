"""orc configuration + hub paths (JSON config per ADR-0002; tomllib absent on 3.9.6).

All calibrations live in ~/.orc/config.json so there is no hard-coded threshold in
code (F10 gate). This module also owns the well-known runtime paths.
"""
import os
import json


def orc_home():
    return os.environ.get("ORC_HOME") or os.path.expanduser("~/.orc")


def hub_dir():
    """The beads queue hub. Defaults to ~/.orc (holds .beads/)."""
    return os.environ.get("ORC_HUB") or orc_home()


def config_path():
    return os.path.join(orc_home(), "config.json")


def shift_path():
    return os.path.join(orc_home(), "shift.json")


def heartbeat_dir():
    return os.path.join(orc_home(), "hb")


# Defaults are the single source of truth for calibration knobs. config.json overrides.
DEFAULTS = {
    "claude_bin": "/opt/homebrew/bin/claude",
    "orc_src": None,               # resolved at runtime to this package's src dir
    "min_free_ram_mb": 400,        # admission: refuse spawn below this (8GB machine)
    "min_window_minutes": 5,       # admission: refuse spawn if window nearly closed
    "max_workers": 1,             # 8GB RAM -> one active worker (research finding)
    "restart_cap": 2,              # watchdog restart cap before escalation (F7)
    "loop_hash_k": 4,              # watchdog: K identical heartbeat hashes = loop (F7)
    "lease_ttl_seconds": 1800,     # F8: a worker whose lease is older than this AND whose
                                   #     PID is dead -> its task returns to ready (30 min)
    "gate_card_cap": 5,            # max gate cards approved in one batch (F9)
    "secret_denylist_extra": [],   # extra env var patterns to strip (F1 base is built-in)
    "mcp_allowlist": [],           # worker MCP servers (default: none) (F1)
    # F13: OS-sandbox (macOS seatbelt) as the PRIMARY wall over the F1 pattern-hook. Workers
    # run under `sandbox-exec` with a profile that allows file writes ONLY inside the task
    # workspace -- obfuscated escapes (base64|bash rm, python rmtree, find -delete) are
    # blocked at the syscall level. Default ON (.spikes/probe/sandbox.md proves the walls).
    "sandbox": True,
    # Fully block outbound network for the worker (per-host allowlisting is unreliable in
    # user seatbelt -- see the spike). Default OFF: workers need the claude API / git fetch /
    # brew; git push stays blocked by the F1 hook. Turn on for locked-down runs.
    "sandbox_deny_network": False,
    # P5: fail-CLOSED on the sandbox. The seatbelt sandbox is the PRIMARY wall; if it would
    # NOT be applied (sandbox-exec unavailable, or sandbox=false) orc REFUSES to spawn an
    # unsupervised worker rather than running it wall-less (fail-open). Set this True to
    # deliberately run without the sandbox (recorded, not recommended). Default False =
    # the wall must be present for an unattended shift.
    "allow_no_sandbox": False,
    "task_token_cap": 0,           # 0 = unlimited (F6 overrides)
    "shift_token_cap": 0,          # 0 = unlimited (F6)
    "notify": "macos",             # notification channel (F9)
    # F15 / R-M2 fix: the DEFAULT backend is Terminal.app because it reliably EXECUTES the
    # worker command. Ghostty 1.3.1 on this machine opens an EMPTY window (`-e` never spawns
    # the shell -- proven exhaustively in .spikes/probe/ghostty-exec.md), so it must NOT be
    # the default. Ghostty stays an opt-in backend for a future build where `-e` works.
    "terminal": "terminal",
    # --- F10: LaunchAgent (autostart in the GUI/Aqua session) --------------------- #
    # The dispatcher runs as a USER LaunchAgent in the Aqua session so it can reach the
    # login Keychain and a working `claude auth` (proven: .spikes/probe/launchagent.md,
    # auth_exit=0). LaunchAgents do NOT inherit the interactive shell PATH, so we set an
    # explicit PATH in the plist and call claude by absolute path (claude_bin above).
    "launchagent_label": "com.user.orc",
    # PATH written into the plist so brew binaries (bd, ccusage, node) resolve for the
    # dispatcher and any child it spawns. No threshold is hard-coded in code -- this is a
    # calibration knob like the rest.
    "launchagent_path": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    # F10 kill switch: how long `orc stop` waits for a worker to die after SIGTERM before
    # escalating to SIGKILL. The whole stop must complete within ~10s (G10 acceptance).
    "stop_grace_seconds": 5,
    # F10 daemon: seconds between dispatch ticks in the LaunchAgent loop.
    "poll_interval_seconds": 15,
    # F10 setup: the Terminal.app profile orc spawns workers into. `orc setup` sets its
    # shellExitAction to 0 (close the window when the shell exits) via plistlib so husk
    # windows do not accumulate for ANY user -- with a backup of the previous value.
    # None -> resolve the machine's default Terminal profile at setup time.
    "terminal_profile": None,
}


def load():
    """Load config.json merged over DEFAULTS. Missing file -> defaults."""
    cfg = dict(DEFAULTS)
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
        except ValueError:
            pass  # malformed config -> fall back to defaults (never crash the dispatcher)
    if not cfg.get("orc_src"):
        cfg["orc_src"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return cfg


def ensure_home():
    home = orc_home()
    os.makedirs(home, exist_ok=True)
    os.makedirs(heartbeat_dir(), exist_ok=True)
    return home


def write_default_config():
    """Write config.json with defaults if it does not exist. Returns (path, created)."""
    ensure_home()
    path = config_path()
    if os.path.exists(path):
        return path, False
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(DEFAULTS, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path, True
