"""Admission + back-pressure (F5): decide whether it is safe to spawn a worker.

The dispatcher must not blindly spawn onto an exhausted usage pool or a memory-starved
machine. Before every spawn it consults three signals (design.md admission contract):

    spawn if ready != empty
         and free_ram >= min_free_ram_mb
         and window_remaining >= min_window_minutes
         and no CLI limit-string is active

Limit-strings are the deterministic text Claude Code prints when a usage cap is hit
(see the official error reference; fixtures in tests/fixtures/limit-*.txt reflect the
literal wording). We classify them and react per the brief:

    session limit  -> park until the 5-hour window resets (parse the reset time)
    weekly limit   -> park deeper (shared weekly cap; parse the reset time)
    Opus limit     -> degradation event (only Opus capped; other models still work)
    429            -> transient throttle; retry, do NOT park
    529 overloaded -> transient capacity; retry, do NOT park (not a usage limit)

This module is pure/deterministic: it takes the RAM/window gauges and a transcript
string as input so it is fully testable on fixtures without spawning a real worker.
python 3.9-compatible.
"""
import re
import time
import datetime


# --- limit-string classification -------------------------------------------- #
# Kinds: the classifier returns one of these plus a parsed reset (when present).
KIND_SESSION = "session"
KIND_WEEKLY = "weekly"
KIND_OPUS = "opus"
KIND_429 = "429"
KIND_529 = "529"

# Reactions: PARK holds the task until reset; RETRY keeps trying without parking;
# DEGRADE is a plan event (Opus-only cap) -- other models still work.
REACT_PARK = "park"
REACT_RETRY = "retry"
REACT_DEGRADE = "degrade"

# Literal CLI wording (code.claude.com/docs/en/errors). Matched case-insensitively so a
# minor casing change in a future CLI build does not silently drop back-pressure.
#   "You've hit your session limit · resets 3:45pm"
#   "You've hit your weekly limit · resets Mon 12:00am"
#   "You've hit your Opus limit · resets 3:45pm"
_HIT_LIMIT_RE = re.compile(
    r"you'?ve hit your (session|weekly|opus) limit"
    r"(?:\s*[·:\-]\s*resets\s+(.+?))?\s*(?:$|\n)",
    re.IGNORECASE,
)
# 429: "API Error: Request rejected (429)"  529: "Repeated 529 Overloaded errors"
_429_RE = re.compile(r"\b429\b|request rejected \(429\)", re.IGNORECASE)
_529_RE = re.compile(r"\b529\b|529 overloaded", re.IGNORECASE)

_KIND_REACTION = {
    KIND_SESSION: REACT_PARK,
    KIND_WEEKLY: REACT_PARK,
    KIND_OPUS: REACT_DEGRADE,
    KIND_429: REACT_RETRY,
    KIND_529: REACT_RETRY,
}


def classify_limit(text, now=None):
    """Classify a worker transcript for an active limit-string.

    Returns a dict {kind, reaction, reset_raw, reset_epoch} or None if no limit-string is
    present. reset_epoch is a best-effort future epoch parsed from the reset wording (None
    when the CLI gave no reset time, e.g. 429/529 transient errors).

    A usage limit (session/weekly/Opus) outranks a transient 429/529: if both appear we
    report the more severe usage cap, because parking is the safe reaction.
    """
    if not text:
        return None
    m = _HIT_LIMIT_RE.search(text)
    if m:
        kind = m.group(1).lower()
        reset_raw = (m.group(2) or "").strip() or None
        return {
            "kind": kind,
            "reaction": _KIND_REACTION[kind],
            "reset_raw": reset_raw,
            "reset_epoch": parse_reset_time(reset_raw, now=now) if reset_raw else None,
        }
    if _429_RE.search(text):
        return {"kind": KIND_429, "reaction": REACT_RETRY,
                "reset_raw": None, "reset_epoch": None}
    if _529_RE.search(text):
        return {"kind": KIND_529, "reaction": REACT_RETRY,
                "reset_raw": None, "reset_epoch": None}
    return None


# --- reset-time parsing ------------------------------------------------------ #
# The CLI prints wall-clock reset hints like "3:45pm" (session/Opus, today) or
# "Mon 12:00am" (weekly, a weekday). We parse these into a concrete future epoch so the
# dispatcher can decide when a parked task may retry. Best-effort: unparseable -> None.
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap]m)?$", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})\s*([ap]m)?$",
    re.IGNORECASE,
)
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _apply_ampm(hour, ampm):
    ampm = (ampm or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour


def parse_reset_time(raw, now=None):
    """Parse a CLI reset hint into a future epoch, or None if not parseable.

    Accepts:
      "3:45pm"          -> the next occurrence of 15:45 (today, else tomorrow)
      "Mon 12:00am"     -> the next occurrence of Monday 00:00
    now is injectable (a datetime) for deterministic tests.
    """
    if not raw:
        return None
    raw = raw.strip()
    base = now if now is not None else datetime.datetime.now()

    m = _TIME_RE.match(raw)
    if m:
        hour = _apply_ampm(int(m.group(1)), m.group(3))
        minute = int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= base:
            cand += datetime.timedelta(days=1)
        return cand.timestamp()

    m = _WEEKDAY_RE.match(raw)
    if m:
        target_dow = _WEEKDAYS[m.group(1).lower()[:3]]
        hour = _apply_ampm(int(m.group(2)), m.group(4))
        minute = int(m.group(3))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        days_ahead = (target_dow - base.weekday()) % 7
        cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        cand += datetime.timedelta(days=days_ahead)
        if cand <= base:
            cand += datetime.timedelta(days=7)
        return cand.timestamp()

    return None


# --- admission gate ---------------------------------------------------------- #
def admission_check(cfg, free_ram_mb, window, ready_count, limit_text=None, now=None):
    """The pre-spawn admission gate. Returns (ok, reason, meta).

    ok      -> True only when it is safe to spawn a worker.
    reason  -> a short machine-stable reason key when not ok (else None).
    meta    -> extras: {"reset_epoch", "limit_kind", "reaction"} for parking/retry logic.

    Signals (design.md admission contract):
      - ready_count == 0        -> nothing to do ("no-ready")
      - free_ram < threshold    -> memory-starved ("low-ram")
      - window inactive/too low -> pool nearly closed ("window-low")
      - active limit-string     -> back-pressure: park (session/weekly/opus) or the caller
                                    retries (429/529). ("limit-<kind>")

    A transient 429/529 is NOT a spawn blocker by itself (retry-able), but if a usage cap
    (session/weekly/opus) is active the gate refuses and hands back the reset epoch so the
    dispatcher can park until the pool reopens.
    """
    meta = {"reset_epoch": None, "limit_kind": None, "reaction": None}

    if ready_count <= 0:
        return False, "no-ready", meta

    min_ram = cfg.get("min_free_ram_mb", 400)
    if free_ram_mb is None or free_ram_mb < min_ram:
        return False, "low-ram", meta

    if not window or not window.get("active"):
        return False, "window-inactive", meta
    rem = window.get("remaining_minutes")
    min_win = cfg.get("min_window_minutes", 5)
    if rem is None or rem < min_win:
        return False, "window-low", meta

    limit = classify_limit(limit_text, now=now) if limit_text else None
    if limit:
        meta["limit_kind"] = limit["kind"]
        meta["reaction"] = limit["reaction"]
        meta["reset_epoch"] = limit.get("reset_epoch")
        if limit["reaction"] == REACT_PARK:
            return False, "limit-%s" % limit["kind"], meta
        if limit["reaction"] == REACT_DEGRADE:
            # Opus-only cap: not a hard block (other models work), but a plan event the
            # dispatcher surfaces. We admit but flag degradation in meta.
            return True, None, meta
        # 429/529: transient -> admit; the spawn/retry path handles the throttle.
        return True, None, meta

    return True, None, meta


def reset_wait_seconds(reset_epoch, now=None):
    """Seconds until a parked task's pool resets (>=0), or None if unknown."""
    if reset_epoch is None:
        return None
    base = time.time() if now is None else now
    return max(0.0, reset_epoch - base)
