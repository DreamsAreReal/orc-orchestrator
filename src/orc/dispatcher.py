"""Dispatcher core: ready -> claim -> re-validate -> preflight -> mutex -> spawn.

F2 uses the minimal single-task path (claim the top ready task, spawn one worker).
F4 generalizes ordering (gate tasks to the end), project-mutex, re-validate, preflight,
and shift.json PID reconciliation. This module keeps the spawn/claim primitives so both
features share one code path.
"""
import os

from . import beads
from . import shift as shiftmod
from . import spawn
from . import worker_walls
from . import probes
from . import strings as S


GATE_LABEL = "gate"


def order_ready(tasks):
    """Sort ready tasks: gate/human-waiting tasks to the END, else by priority (0 first).

    bd priority is 0..4 (lower = more urgent). Gate tasks (label 'gate' or metadata
    gate=true) are pushed last so autonomous tasks run first (ADR-0002).
    """
    def is_gate(t):
        labels = t.get("labels") or []
        meta = beads.task_meta(t)
        return GATE_LABEL in labels or bool(meta.get("gate"))

    def prio(t):
        p = t.get("priority")
        try:
            return int(p)
        except (TypeError, ValueError):
            return 2

    return sorted(tasks, key=lambda t: (1 if is_gate(t) else 0, prio(t)))


def project_busy(state, project):
    """True if a worker for this project is already active (project-mutex, F4)."""
    real = os.path.realpath(project)
    for w in state.get("workers", []):
        if os.path.realpath(w.get("project", "")) == real:
            return True
    return False


def prepare_worker_walls(cfg, project):
    """Write the deny-walls settings.json into the project before spawning (F1)."""
    path, merged = worker_walls.write_worker_settings(
        project, mcp_allowlist=cfg.get("mcp_allowlist") or [],
    )
    return path, merged


def start_prompt(project, slug, text):
    """The worker start prompt (en) — resume pipeline or start phase 0 (design.md).

    ORC_RAW_PROMPT=1 spawns the worker with the raw task text instead of the pipeline
    wrapper. Used by the skeleton E2E (F2) to prove the SPAWN mechanism deterministically;
    real shifts use the pipeline wrapper so the conveyor's quality gates apply.
    """
    if os.environ.get("ORC_RAW_PROMPT") == "1":
        return text
    return (
        "Resume/start pipeline task. Workspace: docs/tasks/%s/. Product layer: docs/. "
        "Task: %s. Read docs/tasks/%s/STATE.md if it exists (resume), else phase 0."
        % (slug, text, slug)
    )


def spawn_one(cfg, hub, state, task):
    """Claim + spawn a single ready task. Returns (ok, detail, updated_state)."""
    meta = beads.task_meta(task)
    project = meta.get("project")
    slug = meta.get("slug") or task.get("id")
    text = meta.get("text") or task.get("title") or ""
    task_id = task.get("id")

    if not project or not os.path.isdir(project):
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, "project path missing: %s" % project)
        return False, "project missing", state

    if project_busy(state, project):
        return False, "project busy (mutex)", state

    # claim atomically
    beads.claim(hub, task_id)

    # F1 walls before spawn
    prepare_worker_walls(cfg, project)

    prompt = start_prompt(project, slug, text)
    tokens_before = probes.total_tokens_now()
    ok, detail = spawn.spawn_terminal(project, cfg["claude_bin"], prompt)
    if not ok:
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_failed(state, task_id, "spawn failed: %s" % detail)
        return False, "spawn failed: %s" % detail, state

    # register worker (PID discovered best-effort after spawn)
    pids = spawn.worker_pids(project)
    pid = pids[0] if pids else None
    shiftmod.add_worker(state, pid=pid, session=detail, project=project,
                        task=task_id, phase="build", tokens_before=tokens_before)
    return True, S.START_SPAWNED.format(id=task_id, project=project, pid=pid), state
