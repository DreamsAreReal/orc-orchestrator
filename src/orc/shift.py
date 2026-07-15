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
    "window_pct_at_start": <int or null>
  }
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
        "window_pct_at_start": None,
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


def start_shift(state, window_pct=None):
    state["started"] = _now_iso()
    state["window_pct_at_start"] = window_pct
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


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
