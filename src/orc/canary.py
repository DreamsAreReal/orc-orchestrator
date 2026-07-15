"""Canary preflight: run before every shift starts (F2 signature, gate G7).

Checks every component the shift depends on. A single failure means the shift does
NOT start and the operator is notified. Each check returns (name, ok, detail).

A synthetic failure can be injected with ORC_CANARY_FAIL=<check-name> so the "broken
component -> shift refuses to start" path is testable without breaking a real component.
"""
import os

from . import probes
from . import beads
from . import strings as S


def _forced_fail(name):
    return os.environ.get("ORC_CANARY_FAIL", "") == name


def run(cfg, hub, spawn_probe=True):
    """Run all canary checks. Returns (checks, all_ok).

    checks: list of (name, ok, detail). all_ok: bool.
    """
    checks = []

    # 1. beads queue reachable
    ok = beads.bd_available() and not _forced_fail("bd")
    detail = "bd on PATH, queue at %s/.beads" % hub if ok else "bd missing or forced-fail"
    if ok:
        try:
            beads.ready(hub)
        except Exception as e:
            ok = False
            detail = "bd ready query failed: %s" % e
    checks.append(("bd", ok, detail))

    # 2. claude auth
    claude_bin = cfg["claude_bin"]
    ok = probes.claude_auth_ok(claude_bin) and not _forced_fail("auth")
    detail = "loggedIn via %s" % claude_bin if ok else "claude auth not loggedIn / forced-fail"
    checks.append(("auth", ok, detail))

    # 3. ccusage window gauge
    w = probes.ccusage_window()
    ok = (w is not None and w.get("active")) and not _forced_fail("ccusage")
    if ok:
        detail = "window active, %s min remaining" % w.get("remaining_minutes")
    else:
        detail = "ccusage window unavailable/inactive / forced-fail"
    checks.append(("ccusage", ok, detail))

    # 4. notification channel
    ok = probes.notifier_available() and not _forced_fail("notify")
    detail = "osascript available" if ok else "osascript missing / forced-fail"
    checks.append(("notify", ok, detail))

    # 5. free RAM above admission threshold
    ram = probes.free_ram_mb()
    min_ram = cfg["min_free_ram_mb"]
    ok = (ram is not None and ram >= min_ram) and not _forced_fail("ram")
    detail = ("%s MB free (>= %s)" % (ram, min_ram)) if ok else (
        "%s MB free (< %s) / forced-fail" % (ram, min_ram))
    checks.append(("ram", ok, detail))

    # 6. terminal spawn from this context (TCC/osascript) — probe only if requested
    if spawn_probe:
        ok = _probe_terminal() and not _forced_fail("spawn")
        detail = "osascript terminal spawn ok" if ok else "terminal spawn blocked / forced-fail"
        checks.append(("spawn", ok, detail))

    all_ok = all(c[1] for c in checks)
    return checks, all_ok


def _probe_terminal():
    """Non-destructive: confirm we can drive Terminal via osascript (count windows)."""
    import subprocess
    try:
        p = subprocess.run(
            ["osascript", "-e", 'tell application "Terminal" to count windows'],
            capture_output=True, text=True, timeout=15,
        )
        return p.returncode == 0
    except Exception:
        return False


def format_report(checks):
    """Human-readable canary report (en; operator terminal)."""
    lines = [S.CANARY_HEADER]
    for name, ok, detail in checks:
        tmpl = S.CANARY_LINE_OK if ok else S.CANARY_LINE_FAIL
        lines.append(tmpl.format(name=name, detail=detail))
    return "\n".join(lines)
