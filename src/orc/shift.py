"""Shift runtime state (~/.orc/shift.json) — the truth about PROCESSES.

Crash-safe: atomic write (tmp + rename). bd is the truth about TASKS; on divergence
bd wins and shift.json is repaired from bd + live PIDs (F4/F8). This module owns the
file format and safe read/write only; reconciliation logic lives in the dispatcher.

Format:
  {
    "started": <iso ts or null>,
    "workers": [{"pid","tab_id","session","project","task","phase","started","tokens_before"}],
    "parked":  [{"task","reason","ts"}],
    "done":    [{"task","ts"}],
    "failed":  [{"task","reason","ts"}],
    "window_pct_at_start": <int or null>,     # legacy, kept for back-compat (unused)
    "tokens_at_start": <int or null>,         # ccusage totalTokens captured at shift start
    "cost_at_start":   <float or null>        # ccusage costUSD captured at shift start
  }
The shift's honest spend = current ccusage total minus the *_at_start baseline. This is a
REAL resource figure (tokens / USD), not the block-reset timer (window_pct_at_start is the
old, misleading "time elapsed in the 5-hour block" and is no longer surfaced).
"""
import os
import json
import time

from . import config


def _empty():
    return {
        "started": None,
        "workers": [],
        "parked": [],
        "done": [],
        "failed": [],
        "window_pct_at_start": None,   # legacy (block-reset timer); no longer surfaced
        "tokens_at_start": None,       # ccusage totalTokens baseline for shift-spend
        "cost_at_start": None,         # ccusage costUSD baseline (fallback shift-spend)
    }


def load():
    path = config.shift_path()
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty()
        # backfill any missing keys
        base = _empty()
        base.update(data)
        return base
    except ValueError:
        return _empty()


def save(state):
    config.ensure_home()
    path = config.shift_path()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def start_shift(state, window_pct=None, tokens_at_start=None, cost_at_start=None):
    """Mark the shift started and capture the ccusage spend baseline.

    tokens_at_start / cost_at_start are the ccusage totals at shift start; the newspaper's
    honest shift-spend figure is (current total - this baseline). window_pct is the legacy
    block-reset timer, retained for back-compat only (no longer shown as "spent")."""
    state["started"] = _now_iso()
    state["window_pct_at_start"] = window_pct
    state["tokens_at_start"] = tokens_at_start
    state["cost_at_start"] = cost_at_start
    return state


def add_worker(state, pid, session, project, task, phase="build",
               tokens_before=None, tab_id=None):
    state["workers"] = [w for w in state["workers"] if w.get("task") != task]
    state["workers"].append({
        "pid": pid,
        "tab_id": tab_id,       # Terminal window id (F14) — closes the tab on completion
        "session": session,
        "project": project,
        "task": task,
        "phase": phase,
        "started": _now_iso(),
        "started_epoch": time.time(),
        "tokens_before": tokens_before,
    })
    return state


def remove_worker(state, task):
    state["workers"] = [w for w in state["workers"] if w.get("task") != task]
    return state


def mark_done(state, task, kind="done", spent=None):
    """Record a completed task. `kind` distinguishes DONE / DONE-WAVE-N (wave) / BETA so the
    newspaper can show them differently (F6). `spent` is the per-task token spend (F6)."""
    remove_worker(state, task)
    if not any(d.get("task") == task for d in state["done"]):
        state["done"].append({"task": task, "ts": _now_iso(), "kind": kind, "spent": spent})
    return state


def mark_parked(state, task, reason):
    remove_worker(state, task)
    state["parked"] = [p for p in state["parked"] if p.get("task") != task]
    state["parked"].append({"task": task, "reason": reason, "ts": _now_iso()})
    return state


def mark_failed(state, task, reason):
    remove_worker(state, task)
    state["failed"] = [fl for fl in state["failed"] if fl.get("task") != task]
    state["failed"].append({"task": task, "reason": reason, "ts": _now_iso()})
    return state


def reset(state=None):
    return _empty()


def is_finished(state):
    """True if a shift was started but is fully drained (no live workers, no pending gates).

    A drained shift is stale: keeping its `started` marker + old `done` entries makes the
    live status hide a freshly added queue (consumer-1: the second shift of the day is
    invisible behind the previous shift's completed newspaper). When this is True, the
    operator's next `orc status` should show the fresh ready queue, not the old newspaper.

    A shift with parked gates is NOT finished: a gate waits live for the operator, so its
    session/window is still meaningful and must stay on the status.
    """
    if not state.get("started"):
        return False
    if state.get("workers"):
        return False
    if state.get("parked"):
        return False
    return True


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
