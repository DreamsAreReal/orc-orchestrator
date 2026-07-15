"""Dispatcher core: ready -> claim -> re-validate -> preflight -> mutex -> spawn.

F2 uses the minimal single-task path (claim the top ready task, spawn one worker).
F4 generalizes ordering (gate tasks to the end), project-mutex, re-validate, preflight,
and shift.json PID reconciliation. This module keeps the spawn/claim primitives so both
features share one code path.
"""
import os
import re
import time

from . import beads
from . import shift as shiftmod
from . import spawn
from . import worker_walls
from . import probes
from . import gitutil
from . import admission
from . import strings as S


GATE_LABEL = "gate"

# --- F14: completion detection ------------------------------------------------
# The dispatcher does not trust the worker process to signal completion (an interactive
# claude tab lingers after the work is done -- consumer M1). Instead it polls the task's
# STATE.md on disk ("disk = truth") and reacts to a TERMINAL status. This mirrors the
# pipeline status vocabulary (SKILL state machine): DONE-WAVE-N / BETA / DONE are done;
# a task parked on a gate is waiting-on-you (a human must answer before it resumes).
#
# Product STATE.md is written in the product language (Russian). The status-field label
# and gate phrases below are matched DATA; they are kept as \u escape sequences so this
# source file stays ASCII (EN-only code policy). English fallbacks cover en STATE.md too.

# Terminal "done" markers (the shift closes the bd task and the worker window).
_DONE_RE = re.compile(r"\bDONE(?:-WAVE-\d+)?\b")
_BETA_RE = re.compile(r"\bBETA\b")
# Status-field label: the escaped literal is the product-language word for "Status".
_STATUS_LABEL = "(?:\u0421\u0442\u0430\u0442\u0443\u0441|Status)"
_STATUS_FIELD_RE = re.compile(
    "\\s*[-*]?\\s*\\**\\s*" + _STATUS_LABEL + "\\s*\\**\\s*[:\uff1a]\\s*(.+)")
# Gate / waiting-on-a-human phrases. Escaped product-language literals mean:
#   waits / answer / decision / user. English fallbacks follow.
_GATE_RE = re.compile(
    "parked[- ]on[- ]gate|"
    "\u0436\u0434\u0451\u0442 (?:\u043e\u0442\u0432\u0435\u0442\u0430|"
    "\u0440\u0435\u0448\u0435\u043d\u0438\u044f|"
    "\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f)|"
    "waiting (?:on|for) (?:the )?(?:user|human|gate)", re.IGNORECASE)


def task_state_path(project, slug):
    """Path to a task's STATE.md inside the two-layer workspace (design.md contract)."""
    return os.path.join(project, "docs", "tasks", slug, "STATE.md")


def _status_field(text):
    """Extract the value of the STATE.md status field ('Status'/RU equivalent)."""
    for line in text.splitlines():
        m = _STATUS_FIELD_RE.match(line)
        if m:
            return m.group(1).strip().strip("*").strip()
    return None


def detect_terminal_status(state_text):
    """Classify a task STATE.md as ('done'|'gate'|None).

    Prefers the explicit status field; falls back to scanning the whole document so a
    "5 VERIFY -> DONE" phase line (no separate status field) is still detected. A gate
    marker outranks a stray DONE token so a gated task is never auto-closed by mistake.
    """
    if not state_text:
        return None
    field = _status_field(state_text)
    haystack = field if field else state_text
    if _GATE_RE.search(state_text):
        return "gate"
    if _DONE_RE.search(haystack) or _BETA_RE.search(haystack):
        return "done"
    # field present but non-terminal (still in progress) -> not done yet
    return None


def done_kind(state_text):
    """Distinguish the flavour of a terminal 'done' status for the newspaper (F6 backlog).

    Returns one of 'done' (plain DONE, the user said "enough"), 'wave' (DONE-WAVE-N, a
    wave was proposed -- NOT the end), or 'beta' (BETA, non-terminal, awaiting the user's
    decision). The digest must NOT show BETA/DONE-WAVE-N as a flat "готово" -- they mean
    different things to the operator (design.md status vocabulary).
    """
    if not state_text:
        return "done"
    field = _status_field(state_text)
    haystack = field if field else state_text
    if _BETA_RE.search(haystack):
        return "beta"
    if re.search(r"\bDONE-WAVE-\d+\b", haystack):
        return "wave"
    return "done"


def read_task_state(project, slug):
    """Return the task STATE.md text, or None if it does not exist yet."""
    path = task_state_path(project, slug)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def poll_completions(state, hub):
    """Detect finished tasks by polling their STATE.md and close the loop (F14).

    For each active worker: read its task STATE.md; on a terminal status ->
      - done: `bd close`, mark_done in shift.json, close the worker's Terminal window;
      - gate: `bd` blocked (park), mark_parked (waiting-on-you), window KEPT for the
        operator to answer the live gate (F9 owns the notification).
    bd is the truth about tasks; on a bd error we still repair shift.json so the newspaper
    reflects reality. Returns (state, [(task_id, kind)]) for the transitions applied.
    """
    transitions = []
    for w in list(state.get("workers", [])):
        task_id = w.get("task")
        project = w.get("project")
        slug = _worker_slug(hub, task_id, project)
        if not (task_id and project and slug):
            continue
        text = read_task_state(project, slug)
        status = detect_terminal_status(text)
        if status == "done":
            _safe_close(hub, task_id)
            # per-task spend attribution (F6): tokens consumed = ccusage total at close
            # minus the total captured at claim. On this 1-worker machine one worker runs
            # at a time, so the whole-window delta is this task's spend (design.md).
            spent = task_spend(w)
            kind = done_kind(text)
            shiftmod.mark_done(state, task_id, kind=kind, spent=spent)
            # stop the lingering worker (frees RAM) and close its window (best effort)
            spawn.close_window(w.get("tab_id"))
            transitions.append((task_id, "done"))
        elif status == "gate":
            reason = S.PARK_ON_GATE
            _safe_block(hub, task_id)
            shiftmod.mark_parked(state, task_id, reason)
            # keep the worker window: the session waits live for the operator (F9)
            transitions.append((task_id, "gate"))
    return state, transitions


# --- F6: per-task spend attribution + budget caps ---------------------------- #
def task_spend(worker, tokens_now=None):
    """Tokens a worker has consumed = ccusage total now - total captured at claim (F6).

    On this 1-worker machine attribution is exact (one worker runs at a time). Returns an
    int >= 0, or None if either endpoint is unknown (ccusage unavailable). tokens_now is
    injectable for tests.
    """
    before = worker.get("tokens_before")
    if before is None:
        return None
    now = tokens_now if tokens_now is not None else probes.total_tokens_now()
    if now is None:
        return None
    return max(0, int(now) - int(before))


def shift_spend(state, tokens_now=None):
    """Total tokens spent so far this shift = done spends + live worker deltas (F6)."""
    total = 0
    known = False
    for d in state.get("done", []):
        s = d.get("spent")
        if s is not None:
            total += int(s)
            known = True
    for w in state.get("workers", []):
        s = task_spend(w, tokens_now=tokens_now)
        if s is not None:
            total += s
            known = True
    return total if known else None


def over_task_cap(cfg, worker, tokens_now=None):
    """True if a live worker has exceeded the per-task token cap (F6). 0 cap = unlimited."""
    cap = cfg.get("task_token_cap", 0)
    if not cap or cap <= 0:
        return False
    spent = task_spend(worker, tokens_now=tokens_now)
    return spent is not None and spent > cap


def over_shift_cap(cfg, state, tokens_now=None):
    """True if the shift's total spend has exceeded the shift token cap (F6). 0 = unlimited."""
    cap = cfg.get("shift_token_cap", 0)
    if not cap or cap <= 0:
        return False
    spent = shift_spend(state, tokens_now=tokens_now)
    return spent is not None and spent > cap


def enforce_budget(cfg, hub, state, tokens_now=None):
    """Park any live worker over its per-task token cap (F6). Returns [(task_id, spent)].

    A worker whose spend blew past the task cap is stopped (killed, RAM freed) and its task
    parked with a budget note so the newspaper shows why. The shift cap is enforced by the
    dispatcher loop (it stops STARTING new tasks); this function handles running workers.
    """
    parked = []
    for w in list(state.get("workers", [])):
        if over_task_cap(cfg, w, tokens_now=tokens_now):
            spent = task_spend(w, tokens_now=tokens_now)
            task_id = w.get("task")
            reason = S.PARK_TASK_BUDGET.format(
                spent=spent, cap=cfg.get("task_token_cap"))
            _safe_block(hub, task_id)
            spawn.close_window(w.get("tab_id"))
            shiftmod.mark_parked(state, task_id, reason)
            parked.append((task_id, spent))
    return parked


def _worker_slug(hub, task_id, project):
    """Resolve a worker's task slug from bd metadata (falls back to the task id)."""
    task = beads.show(hub, task_id) if task_id else None
    if task:
        meta = beads.task_meta(task)
        return meta.get("slug") or task_id
    return task_id


def _safe_close(hub, task_id):
    try:
        beads.close(hub, task_id)
        return True
    except beads.BeadsError:
        return False


def _safe_block(hub, task_id):
    try:
        beads.set_status(hub, task_id, "blocked")
        return True
    except beads.BeadsError:
        try:
            beads.set_status(hub, task_id, "open")
        except beads.BeadsError:
            pass
        return False


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


def _limit_text():
    """Best-effort source of a recent limit-string transcript for admission (F5).

    Real shifts feed the most recent worker's tail here; the verification seam
    ORC_LIMIT_TEXT injects a fixture transcript so the back-pressure path is testable
    without a live worker actually hitting a cap. None -> no limit-string signal.
    """
    return os.environ.get("ORC_LIMIT_TEXT")


def admit(cfg, ready_count, limit_text=None, now=None):
    """Run the admission gate (F5) with live RAM/window gauges. Returns (ok, reason, meta).

    Thin wrapper over admission.admission_check that reads the machine's current free RAM
    and the ccusage window. Keeps the pure decision logic in admission.py (fixture-tested)
    and the live I/O here.
    """
    free_ram = probes.free_ram_mb()
    window = probes.ccusage_window()
    if limit_text is None:
        limit_text = _limit_text()
    return admission.admission_check(
        cfg, free_ram, window, ready_count, limit_text=limit_text, now=now)


def _park_reason_for_admission(cfg, reason, meta):
    """Map an admission refusal reason key to an operator-facing park string (F5)."""
    if reason == "low-ram":
        return S.PARK_LOW_RAM.format(ram=probes.free_ram_mb(),
                                     min=cfg.get("min_free_ram_mb", 400))
    if reason in ("window-low", "window-inactive"):
        w = probes.ccusage_window() or {}
        return S.PARK_WINDOW_LOW.format(rem=w.get("remaining_minutes"),
                                        min=cfg.get("min_window_minutes", 5))
    if reason == "limit-session":
        return S.PARK_LIMIT_SESSION.format(reset=_reset_str(meta))
    if reason == "limit-weekly":
        return S.PARK_LIMIT_WEEKLY.format(reset=_reset_str(meta))
    return reason


def _reset_str(meta):
    epoch = (meta or {}).get("reset_epoch")
    if not epoch:
        return "unknown"
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M", _t.localtime(epoch))


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


def reconcile(state, hub, cfg=None, now=None):
    """Arbiter for shift.json vs bd vs live PIDs (design.md + F8 recovery).

    bd is the truth about TASKS; shift.json is the truth about PROCESSES. On a dispatcher
    restart (e.g. after kill -9) or drift:
      - a worker whose PID is still ALIVE is kept (adopted) -- no duplicate spawn;
      - a worker whose PID is DEAD (or absent) has its task returned to ready via a LEASE
        so it is re-served, unless bd already closed it.

    Lease safety (F8): a worker registered so recently that it is still within its lease
    TTL and whose PID we simply failed to read (a transient ps/lsof miss right after spawn)
    is NOT dropped -- we re-resolve the PID from its window tty first. Only a genuinely
    dead worker returns its task to ready. Idempotent: running it twice yields the same
    result (0 duplicates / 0 losses). Returns (state, dropped_task_ids).
    """
    cfg = cfg or {}
    now = time.time() if now is None else now
    lease_ttl = cfg.get("lease_ttl_seconds", 1800)
    alive = []
    dropped = []
    for w in state.get("workers", []):
        pid = w.get("pid")
        if pid and _pid_alive(pid):
            alive.append(w)
            continue
        # PID missing/dead: try to re-resolve it (race-free) before giving up. A worker
        # still inside its lease may just have had its PID unread at spawn time.
        repid = _reresolve_pid(w, now, lease_ttl)
        if repid is not None:
            w["pid"] = repid
            alive.append(w)
            continue
        # genuinely dead: return the task to ready (lease) unless bd already closed it.
        dropped.append(w.get("task"))
        task = beads.show(hub, w.get("task")) if w.get("task") else None
        if task and task.get("status") not in ("closed", "done"):
            beads.reopen(hub, w.get("task"))
    state["workers"] = alive
    return state, dropped


def _reresolve_pid(worker, now, lease_ttl):
    """Best-effort re-resolve a worker's live PID from its window tty / project cwd (F8).

    Returns a live PID or None. Prefers the window tty (race-free); falls back to cwd
    matching. Only consults the tty when the worker is still within its lease TTL (an old
    worker with a dead PID is really dead)."""
    started = worker.get("started_epoch")
    within_lease = (started is not None) and ((now - float(started)) <= lease_ttl)
    if within_lease and worker.get("tab_id"):
        pid = spawn.pid_on_window(worker.get("tab_id"), retries=1, delay=0)
        if pid and _pid_alive(pid):
            return pid
    project = worker.get("project", "")
    if project:
        live = spawn.worker_pids(project)
        for p in live:
            if _pid_alive(p):
                return p
    return None


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def start_prompt(project, slug, text):
    """The worker start prompt (en) -- resume pipeline or start phase 0 (design.md).

    ORC_RAW_PROMPT=1 spawns the worker with the raw task text instead of the pipeline
    wrapper. Used by the skeleton E2E (F2) to prove the SPAWN mechanism deterministically;
    real shifts use the pipeline wrapper so the conveyor's quality gates apply.
    ORC_PROMPT_OVERRIDE (with ORC_RAW_PROMPT=1) supplies a verbatim prompt -- used by the
    F14 loop-close E2E to make the worker also write its task STATE.md (the polled signal).
    """
    if os.environ.get("ORC_RAW_PROMPT") == "1":
        override = os.environ.get("ORC_PROMPT_OVERRIDE")
        return override if override else text
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

    # shift budget cap (F6): once the shift's total token spend is over the cap, do not
    # start new tasks (protect the weekly pool). Checked before admission/claim.
    if over_shift_cap(cfg, state):
        reason = S.PARK_SHIFT_BUDGET.format(
            spent=shift_spend(state), cap=cfg.get("shift_token_cap"))
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, reason)
        return False, "shift-budget-cap", state

    # admission + back-pressure (F5): RAM / usage-window / limit-string gate. Checked
    # BEFORE claiming so a task blocked by back-pressure is not left claimed-but-unspawned.
    adm_ok, adm_reason, adm_meta = admit(cfg, ready_count=1)
    if not adm_ok:
        park_reason = _park_reason_for_admission(cfg, adm_reason, adm_meta)
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, park_reason)
        return False, "admission: %s" % adm_reason, state

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
    # session = task_id: the worker's heartbeat hooks (F7) key their log/marker to this id
    # via ORC_SESSION, so the watchdog reads the same session the dispatcher spawned.
    ok, detail = spawn.spawn_terminal(project, cfg["claude_bin"], prompt, session=task_id)
    if not ok:
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_failed(state, task_id, "spawn failed: %s" % detail)
        return False, "spawn failed: %s" % detail, state

    # register worker. `detail` is the Terminal window id (spawn_terminal); we store it as
    # tab_id so the dispatcher can close the worker's window on completion and so status no
    # longer prints a bare `None` (consumer finding).
    tab_id = detail
    # Robust PID capture (F8 fix for the eval's `pid None`): resolve the window's tty and
    # read the process ON it (race-free), rather than relying on lsof cwd-matching right
    # after spawn (which loses to the shell's `cd`). Fall back to cwd-matching if needed.
    pid = spawn.pid_on_window(tab_id)
    if pid is None:
        pids = spawn.worker_pids(project)
        pid = pids[0] if pids else None
    shiftmod.add_worker(state, pid=pid, session=task_id, project=project,
                        task=task_id, phase="build", tokens_before=tokens_before,
                        tab_id=tab_id)
    return True, S.START_SPAWNED.format(id=task_id, project=project, tab=tab_id), state
