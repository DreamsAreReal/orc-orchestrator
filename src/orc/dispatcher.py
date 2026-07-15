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
from . import gitutil
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


# The .claude/settings.json we write for walls is OUR artifact; a tree dirty only
# because of it is still "clean" from the operator's point of view. dirty_paths uses
# -uall so untracked dirs are expanded; we still tolerate a collapsed ".claude/" entry.
_OURS_PREFIXES = (
    ".claude/settings.json", ".claude/settings.json.tmp", ".claude/",
)


def preflight(project):
    """Check the project is safe to spawn into. Returns (ok, reason).

    - not a repo            -> park (we need git checkpoints)
    - dirty tree that is    -> park with "human may be mid-edit" (R8): never build on
      NOT just our own          top of someone's uncommitted work
      artifact
    A tree dirty only because of our generated .claude/settings.json is fine.
    """
    if not os.path.isdir(project):
        return False, S.PARK_PROJECT_MISSING.format(project=project)
    if not gitutil.is_repo(project):
        return False, S.PARK_NOT_A_REPO.format(project=project)
    dirty = gitutil.dirty_paths(project)
    foreign = [p for p in dirty if not p.startswith(_OURS_PREFIXES)]
    if foreign:
        return False, S.PARK_DIRTY_TREE.format(paths=", ".join(foreign[:5]))
    return True, None


def revalidate(project, task):
    """Re-validate a claimed task against the current product layer (R5).

    If docs/ changed after the task's brief was approved, return a note string to be
    recorded in the task's STATE (the plan may be stale). Returns None if unchanged or
    unknowable. Uses the task metadata field `product_rev` captured at add time; if the
    field is absent (older task), we skip (no false alarm).
    """
    meta = beads.task_meta(task)
    approved_rev = meta.get("product_rev")
    if not approved_rev:
        return None
    current = gitutil.product_layer_rev(project)
    if current and current != approved_rev:
        return S.REVALIDATE_NOTE.format(rev=current[:12])
    return None


def record_revalidate_note(project, slug, note):
    """Append a re-validate note into the task's STATE.md (docs/tasks/<slug>/STATE.md).

    Best-effort: creates the task workspace dir if needed. Returns the path or None.
    """
    if not note:
        return None
    task_dir = os.path.join(project, "docs", "tasks", slug)
    try:
        os.makedirs(task_dir, exist_ok=True)
        state_path = os.path.join(task_dir, "STATE.md")
        with open(state_path, "a") as f:
            f.write("\n## orc re-validate\n%s\n" % note)
        return state_path
    except OSError:
        return None


def reconcile(state, hub):
    """Arbiter for shift.json vs bd vs live PIDs (design.md).

    bd is the truth about TASKS; shift.json is the truth about PROCESSES. On restart or
    drift: drop workers whose PID is no longer alive (their task returns to ready via
    lease); keep workers that are still alive. Returns (state, dropped_task_ids).
    """
    alive = []
    dropped = []
    for w in state.get("workers", []):
        pid = w.get("pid")
        if pid and _pid_alive(pid):
            alive.append(w)
        else:
            # dead/unknown worker: verify by cwd match too before dropping
            live_pids = spawn.worker_pids(w.get("project", "")) if w.get("project") else []
            if live_pids:
                w["pid"] = live_pids[0]
                alive.append(w)
            else:
                dropped.append(w.get("task"))
                # return the task to ready (lease) unless bd already closed it
                task = beads.show(hub, w.get("task")) if w.get("task") else None
                if task and task.get("status") not in ("closed", "done"):
                    beads.reopen(hub, w.get("task"))
    state["workers"] = alive
    return state, dropped


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


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

    if not project:
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, S.PARK_PROJECT_MISSING.format(project=project))
        return False, "project missing", state

    # project-mutex: one active task per repo, checked BEFORE claiming.
    if project_busy(state, project):
        return False, "project busy (mutex)", state

    # preflight: never spawn on top of a human's uncommitted work; must be a repo.
    ok, reason = preflight(project)
    if not ok:
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, reason)
        return False, reason, state

    # claim atomically
    beads.claim(hub, task_id)

    # re-validate the plan against the current product layer (R5); note into STATE.
    note = revalidate(project, task)
    if note:
        record_revalidate_note(project, slug, note)

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
