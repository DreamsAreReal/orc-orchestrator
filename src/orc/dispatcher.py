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
from . import config
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


def worker_progressed(worker):
    """External-fact gate for a DONE claim (B1, anti-reward-hacking / anti-Goodhart P6).

    A worker's STATE.md=DONE is trusted ONLY if the WORLD shows real forward motion since
    the worker started: a git commit newer than its `started_epoch`, or a changed/created
    artifact in the project (dirty tree beyond our own orc-managed files). This is the same
    `external_progress` oracle the watchdog already uses -- now wired into the completion
    path, which is exactly the moment a reward-hacking worker would lie (brief G1 / the
    Replit-class incident named in the brief). No external fact -> the DONE is NOT taken.

    Returns True if an external fact backs the claim. A missing project/started_epoch is
    treated as "no fact" (fail-closed: better to park a real DONE for inspection than to
    auto-close a fake one).
    """
    from . import watchdog
    project = worker.get("project")
    started = worker.get("started_epoch")
    if not project or started is None:
        return False
    return watchdog.external_progress(project, since_epoch=float(started),
                                      baseline_rev=worker.get("head_at_start"))


def poll_completions(state, hub, cfg=None):
    """Detect finished tasks by polling their STATE.md and close the loop (F14).

    For each active worker: read its task STATE.md; on a terminal status ->
      - done (WITH an external fact): `bd close`, mark_done in shift.json, close the
        worker's window (F15 backend);
      - done (WITHOUT an external fact): NOT closed -- parked "suspected fake-done" and
        the worker window kept, so a worker cannot close a task by merely writing DONE
        (B1 reward-hacking gate; brief G1 "DONE confirmed by external facts");
      - gate: `bd` blocked (park), mark_parked (waiting-on-you), window KEPT for the
        operator to answer the live gate (F9 owns the notification).
    bd is the truth about tasks; on a bd error we still repair shift.json so the newspaper
    reflects reality. Returns (state, [(task_id, kind)]) for the transitions applied.
    """
    cfg = cfg or {}
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
            # B1: a DONE claim is honored ONLY with an external fact (fresh commit /
            # changed artifact since the worker started). Otherwise it is a suspected
            # fake-done. BUT a STILL-ALIVE worker that just wrote DONE may not have flushed
            # its deliverable to disk yet (proven live in the G1 pipeline run: the poll
            # fired ~4s before the worker wrote factorial.py, causing a FALSE fake-done
            # park of a real, working deliverable). So: only PARK as suspected-fake-done a
            # worker that is DEAD (exited having produced no external fact -- the true
            # reward-hack). A live worker with no external fact yet is simply NOT-YET-DONE:
            # leave it in `workers` and re-poll next tick. This keeps the anti-reward-hack
            # wall intact (a dead worker that produced nothing is still parked) without
            # racing a worker mid-write.
            if not worker_progressed(w):
                if _pid_alive(w.get("pid")):
                    # Still working; the DONE is premature/unflushed. Do not finalize.
                    continue
                _safe_block(hub, task_id)
                shiftmod.mark_parked(state, task_id, S.PARK_SUSPECTED_FAKE_DONE)
                transitions.append((task_id, "suspected-fake-done"))
                continue
            _safe_close(hub, task_id)
            # per-task spend attribution (F6): tokens consumed = ccusage total at close
            # minus the total captured at claim. On this 1-worker machine one worker runs
            # at a time, so the whole-window delta is this task's spend (design.md).
            spent = task_spend(w)
            kind = done_kind(text)
            shiftmod.mark_done(state, task_id, kind=kind, spent=spent)
            # stop the lingering worker (frees RAM) and close its window. F15: on Ghostty
            # the window self-closes on process exit (0 husk); on Terminal best-effort.
            spawn.close_worker(cfg, w.get("tab_id"), session=w.get("session"))
            transitions.append((task_id, "done"))
        elif status == "gate":
            reason = S.PARK_ON_GATE
            _safe_block(hub, task_id)
            shiftmod.mark_parked(state, task_id, reason)
            # keep the worker window: the session waits live for the operator (F9). Fire a
            # macOS notification so the operator knows a decision is waiting; the full gate
            # card (scope/bar/authority/brief/cost) is in the newspaper.
            _notify_gate(cfg, hub, task_id)
            transitions.append((task_id, "gate"))
    return state, transitions


def _notify_gate(cfg, hub, task_id):
    """Notify the operator that a task reached a gate and is waiting live (F9)."""
    from . import notify
    task = beads.show(hub, task_id) if task_id else None
    title = task.get("title") if task else task_id
    meta = beads.task_meta(task) if task else {}
    scope = (meta.get("gate_card") or {}).get("scope", "")
    try:
        return notify.notify_gate(cfg, task_id, title, scope)
    except Exception:
        return False


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
            spawn.close_worker(cfg, w.get("tab_id"), session=w.get("session"))
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
    ok, reason, meta = admission.admission_check(
        cfg, free_ram, window, ready_count, limit_text=limit_text, now=now)
    # No window telemetry is admitted (not parked) but logged so the operator sees the gap.
    if meta.get("window") == "no-telemetry":
        import sys as _sys
        _sys.stderr.write(S.WINDOW_NO_TELEMETRY + "\n")
    return ok, reason, meta


def _park_reason_for_admission(cfg, reason, meta):
    """Map an admission refusal reason key to an operator-facing park string (F5)."""
    if reason == "low-ram":
        return S.PARK_LOW_RAM.format(ram=probes.free_ram_mb(),
                                     min=cfg.get("min_free_ram_mb", 400))
    # NB: there is no longer a "window-low"/"window-inactive" park reason. Admission does
    # NOT park on the block-reset clock (fixed 2026-07-15); an absent window telemetry is
    # admitted-with-a-flag, not parked. Real back-pressure is the limit-string reasons.
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


# Artifacts orc itself generates in a project are OUR own; a tree dirty only because of
# them is still "clean" from the operator's point of view, so a later task on the same
# project is not falsely parked as "a human may be mid-edit". dirty_paths uses -uall so
# untracked dirs are expanded; we still tolerate a collapsed ".claude/" / ".orc/" entry.
#   .claude/settings.json  -> the worker deny-walls we write (F1)
#   .orc/                  -> the seatbelt sandbox profile we write (F13)
#   docs/tasks/            -> the two-layer task mini-pipe workspace incl. the loop-close
#                             STATE.md the worker writes (F11/F14) -- orc-managed, never a
#                             human's mid-edit. This is what unblocked a same-project second
#                             task in the first real F12 shift (t1's STATE.md left the tree
#                             dirty and parked the gate task).
_OURS_PREFIXES = (
    ".claude/settings.json", ".claude/settings.json.tmp", ".claude/",
    ".orc/", "docs/tasks/",
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


def _dead_worker_finished(hub, worker):
    """True if a dead worker's task is DONE on disk AND backed by an external fact.

    Guards reconcile against requeuing a task that actually FINISHED between ticks (the
    worker wrote DONE + committed and exited before the next poll). Only returns True on a
    real, non-empty external fact -- a bare DONE with nothing produced is NOT finished (it
    falls through to the lease and poll_completions parks it as suspected-fake-done, so the
    B1 anti-reward-hacking wall stays intact)."""
    task_id = worker.get("task")
    project = worker.get("project")
    slug = _worker_slug(hub, task_id, project)
    if not (task_id and project and slug):
        return False
    text = read_task_state(project, slug)
    if detect_terminal_status(text) != "done":
        return False
    return worker_progressed(worker)


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
        # Dead PID, but the task may have FINISHED between ticks: a worker can write its
        # DONE STATE.md, produce its external fact (commit/artifact), and exit before the
        # dispatcher next polls. Requeuing such a task via the lease would duplicate work
        # and can wedge it (its commit now predates the re-spawn, so worker_progressed goes
        # False forever). So: if the task's STATE.md shows a terminal DONE backed by a real
        # external fact, KEEP the worker here so poll_completions (called right after
        # reconcile in the daemon tick) closes it properly. This does NOT weaken the B1
        # anti-reward-hacking wall: a dead worker with NO external fact still falls through
        # to the lease/requeue below (and poll_completions parks it suspected-fake-done).
        if _dead_worker_finished(hub, w):
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
    ORC_PROMPT_DIR (with ORC_RAW_PROMPT=1) points at a directory of per-slug prompt files
    (`<dir>/<slug>`); the matching file's contents become the worker prompt -- used by the
    F12 final E2E to drive several different real tasks in one shift. The spawn path stays
    100% real (real claude, real work); only WHICH prompt is chosen is seam-controlled.
    """
    if os.environ.get("ORC_RAW_PROMPT") == "1":
        pdir = os.environ.get("ORC_PROMPT_DIR")
        if pdir:
            pf = os.path.join(pdir, slug)
            if os.path.isfile(pf):
                try:
                    with open(pf) as fh:
                        return fh.read()
                except OSError:
                    pass
        override = os.environ.get("ORC_PROMPT_OVERRIDE")
        return override if override else text
    return (
        "Run this task THROUGH THE PIPELINE: invoke the `pipeline` skill (Skill tool) "
        "before doing any work -- do NOT do it raw. Two-layer workspace: your task lives "
        "under docs/tasks/%s/ (its own STATE.md), the product layer is docs/. If "
        "docs/tasks/%s/STATE.md already exists, RESUME the pipeline from it; otherwise "
        "start at phase 0 (the pipeline will offer a folded micro/lite mode at the gate "
        "for a tiny deliverable). Task: %s"
        % (slug, slug, text)
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

    # P5 fail-closed: the OS-sandbox is the PRIMARY wall of an unattended shift. If it would
    # NOT be applied (seatbelt unavailable, or sandbox disabled without an explicit opt-out)
    # REFUSE to spawn -- do not run a worker with no wall (fail-open). Checked before claim.
    from . import sandbox as sandboxmod
    sb_ok, sb_reason = sandboxmod.sandbox_gate(cfg)
    if not sb_ok:
        reason = (S.PARK_SANDBOX_UNAVAILABLE if sb_reason == "unavailable"
                  else S.PARK_SANDBOX_DISABLED)
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_parked(state, task_id, reason)
        return False, "sandbox-fail-closed: %s" % sb_reason, state

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
    # Capture the repo HEAD at start so the external-fact gate can recognize a fast
    # worker commit made in the SAME wall-clock second as the spawn while still excluding
    # this pre-existing baseline (P2/3 boundary fix; %ct has 1-second resolution).
    head_at_start = gitutil.head_rev(project) if gitutil.is_repo(project) else None
    # session = task_id: the worker's heartbeat hooks (F7) key their log/marker to this id
    # via ORC_SESSION, so the watchdog reads the same session the dispatcher spawned.
    # spawn_worker routes to the configured backend (F15): Ghostty (default, clean close)
    # or Terminal.app. `detail` is the backend handle stored as tab_id.
    # Per-task network policy: `orc add --offline` sets meta["offline"] to cut this worker's
    # network (deny) regardless of the shift-wide policy (config.network_deny resolves the
    # rest). A per-task offline flag can only TIGHTEN to deny, never loosen a shift-wide deny.
    deny_network = config.network_deny(cfg, task_offline=bool(meta.get("offline")))
    ok, detail = spawn.spawn_worker(cfg, project, cfg["claude_bin"], prompt,
                                    session=task_id, deny_network=deny_network)
    if not ok:
        beads.set_status(hub, task_id, "open")
        shiftmod.mark_failed(state, task_id, "spawn failed: %s" % detail)
        return False, "spawn failed: %s" % detail, state

    # register worker. `detail` is the backend handle (Terminal window id, or the Ghostty
    # session marker); stored as tab_id so the dispatcher can close the worker's window on
    # completion and so status no longer prints a bare `None` (consumer finding).
    tab_id = detail
    # Robust PID capture (F8): resolve via the backend (Ghostty: pgrep the session marker;
    # Terminal: the window tty) instead of lsof cwd-matching right after spawn.
    pid = spawn.worker_pid(cfg, project, task_id, handle=tab_id)
    shiftmod.add_worker(state, pid=pid, session=task_id, project=project,
                        task=task_id, phase="build", tokens_before=tokens_before,
                        tab_id=tab_id, head_at_start=head_at_start)
    return True, S.START_SPAWNED.format(id=task_id, project=project, tab=tab_id), state
