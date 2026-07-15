"""System probes: window budget (ccusage), free RAM, claude auth, terminal spawn.

Used by the canary preflight (F2) and admission (F5). Each probe returns a small
dict/tuple and never raises for an expected "unavailable" state — it reports it, so
the canary can decide to fail the shift deliberately.
"""
import os
import re
import json
import shutil
import subprocess


def free_ram_mb():
    """Available RAM in MB (free + inactive + speculative pages). None on failure."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    except Exception:
        return None
    m_size = re.search(r"page size of (\d+)", out)
    if not m_size:
        return None
    psize = int(m_size.group(1))

    def pages(name):
        m = re.search(r"Pages %s:\s+(\d+)" % re.escape(name), out)
        return int(m.group(1)) if m else 0

    total = pages("free") + pages("inactive") + pages("speculative")
    return int(total * psize / 1024 / 1024)


def ccusage_window():
    """Return the active window gauge dict or None.

    {active, remaining_minutes, total_tokens} from `ccusage blocks --active --json`.
    """
    bin_ = shutil.which("ccusage")
    if not bin_:
        return None
    try:
        p = subprocess.run(
            [bin_, "blocks", "--active", "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return None
    blocks = data.get("blocks") or []
    active = [b for b in blocks if b.get("isActive")]
    if not active:
        return {"active": False, "remaining_minutes": None, "total_tokens": None}
    b = active[0]
    proj = b.get("projection") or {}
    return {
        "active": True,
        "remaining_minutes": proj.get("remainingMinutes"),
        "total_tokens": b.get("totalTokens"),
    }


def total_tokens_now():
    """Best-effort current total tokens in the active window (F6 attribution). None if n/a."""
    w = ccusage_window()
    if not w:
        return None
    return w.get("total_tokens")


def claude_auth_ok(claude_bin):
    """True if `claude auth status` reports loggedIn."""
    if not os.path.exists(claude_bin):
        return False
    try:
        p = subprocess.run(
            [claude_bin, "auth", "status"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return False
    if p.returncode != 0:
        return False
    try:
        data = json.loads(p.stdout)
        return bool(data.get("loggedIn"))
    except ValueError:
        return "loggedIn" in p.stdout and "true" in p.stdout.lower()


def notifier_available():
    """macOS notifications require osascript."""
    return shutil.which("osascript") is not None
