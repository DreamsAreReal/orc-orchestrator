"""Notifications (F9): tell the operator a worker reached a brief-gate and is waiting live.

The single point where a human is needed. When a worker reaches a task-brief gate the
dispatcher (via poll_completions -> gate) parks the task and fires a macOS notification so
the operator knows to answer; the worker session stays alive, waiting (the slot is held --
the user's accepted trade-off on this 1-worker machine). The gate card (scope / bar /
authority / brief path / cost of error) is rendered by report.py; this module owns only
the delivery channel.

Channel v1: macOS `osascript display notification` (design.md; happy/telegram are P1).
python 3.9-compatible; never raises into the dispatcher (a failed notification must not
crash the shift -- it degrades to the newspaper the operator reads anyway).
"""
import shutil
import subprocess


def macos_available():
    return shutil.which("osascript") is not None


def _escape(s):
    """Escape a string for embedding inside an AppleScript double-quoted literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def notify_macos(title, message, subtitle=None, sound=None):
    """Deliver a macOS notification. Returns True on success, False on any failure.

    Uses `osascript -e 'display notification ...'`. The ORC_NOTIFY_DRYRUN seam records the
    call instead of firing it (tests / headless runs), returning True.
    """
    import os
    parts = ['display notification "%s"' % _escape(message),
             'with title "%s"' % _escape(title)]
    if subtitle:
        parts.append('subtitle "%s"' % _escape(subtitle))
    if sound:
        parts.append('sound name "%s"' % _escape(sound))
    script = " ".join(parts)

    if os.environ.get("ORC_NOTIFY_DRYRUN") == "1":
        # record the composed script so tests can assert content without a real popup
        path = os.environ.get("ORC_NOTIFY_LOG")
        if path:
            try:
                with open(path, "a") as f:
                    f.write(script + "\n")
            except OSError:
                pass
        return True

    if not macos_available():
        return False
    try:
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:
        return False


def notify_gate(cfg, task_id, title, scope):
    """Fire the "a task reached a gate, it is waiting for you" notification (F9).

    Channel comes from cfg['notify'] (default macos). Returns True if delivered. The full
    decision card is in the newspaper; the notification is the nudge to go read it.
    """
    from . import strings as S
    channel = (cfg or {}).get("notify", "macos")
    n_title = S.NOTIFY_GATE_TITLE
    n_msg = S.NOTIFY_GATE_BODY.format(id=task_id, title=title or task_id)
    n_sub = S.NOTIFY_GATE_SUBTITLE.format(scope=scope or "-")
    if channel == "macos":
        return notify_macos(n_title, n_msg, subtitle=n_sub, sound="Glass")
    # unknown channel -> no delivery (still safe; newspaper carries the card)
    return False
