"""Centralized user-facing strings for orc.

Deliverable-code language is English (brief: `UI language: en` for CLI/README/commits).
End-user digest / notifications / gate cards are Russian and live under the RU_* block.
Operational block-reasons emitted to the worker model (stderr of a PreToolUse hook) are
English: they are read by a Claude Code worker, not by the human operator.
"""

# --- Worker deny-wall block reasons (fed to the worker model via hook stderr, en) ---
WALL_GIT_PUSH = (
    "BLOCKED (sandbox wall): `git push` in any form is forbidden for workers. "
    "Publishing is an irreversible external action outside the task's authority. "
    "Prepare the change and report; the operator pushes."
)
WALL_RM_OUTSIDE = (
    "BLOCKED (sandbox wall): `rm -rf` outside the task workspace is forbidden. "
    "Workers may only delete inside their own workspace: {workspace}."
)
WALL_READ_SSH = (
    "BLOCKED (sandbox wall): reading ~/.ssh is forbidden. "
    "Workers have no access to private keys or host credentials."
)
WALL_READ_OTHER_PROJECT = (
    "BLOCKED (sandbox wall): reading files outside the task workspace / other "
    "projects is forbidden. Stay inside {workspace}."
)
WALL_WRITE_OUTSIDE = (
    "BLOCKED (sandbox wall): writing outside the task workspace is forbidden. "
    "Workers may only write inside {workspace}."
)

# --- Generator / CLI operational messages (en) ---
GEN_MERGED = "Merged deny-walls into existing settings: {path}"
GEN_CREATED = "Created worker settings with deny-walls: {path}"
GEN_ENV_STRIPPED = "Stripped {n} secret env var(s) from worker environment."
GEN_MCP_ALLOWLIST = "MCP allowlist applied: {servers}"

# --- orc CLI operational messages (en; read by the operator at the terminal) ---
ADD_CREATED = "added task {id} -> {project}"
ADD_BATCH_DONE = "added {n} task(s) to ready"
ERR_NO_TASK_TEXT = "error: task text is required"
ERR_PROJECT_MISSING = "error: project path does not exist: {project}"
# Hub terminology is consistent everywhere: the hub is the `~/.orc` directory; the beads
# queue lives inside it under `.beads/`. Messages name the hub path, not the inner dir.
ERR_HUB_MISSING = (
    "error: orc hub is not initialized. Run `orc init` first "
    "(creates the ~/.orc hub with its beads queue)."
)
ERR_BD_MISSING = "error: `bd` (beads) not found on PATH; install with `brew install beads`."
HUB_INITIALIZED = "orc hub initialized at {hub} (beads queue ready inside .beads/)"
HUB_ALREADY = "orc hub already initialized at {hub}"

START_CANARY_OK = "canary: all preflight checks passed; shift starting"
START_CANARY_FAIL = "canary: FAILED ({n} check(s)); shift not started"
START_NO_READY = "no ready tasks in the queue; nothing to start"
START_SPAWNED = "spawned worker for {id} in {project} (Terminal window id {tab})"

# --- dispatcher: preflight / re-validate / reconcile (en) ---
PARK_DIRTY_TREE = (
    "parked: project git tree is dirty and not ours "
    "(a human may be mid-edit): {paths}. Not spawning a worker on top of it."
)
PARK_PROJECT_MISSING = "parked: project path missing: {project}"
PARK_NOT_A_REPO = "parked: project is not a git repository: {project}"
REVALIDATE_NOTE = (
    "orc re-validate: the product layer (docs/) changed after this task's brief was "
    "approved (rev {rev}); the plan may be stale — confirm scope before building."
)
PARK_ON_GATE = "task reached a gate; waiting for your answer (see the gate card)"

# --- admission / back-pressure (F5) reasons (en; operator terminal) ---
PARK_LOW_RAM = (
    "parked: not enough free RAM to spawn a worker safely "
    "({ram} MB free < {min} MB). Waiting for memory to free up."
)
PARK_WINDOW_LOW = (
    "parked: the usage window is nearly closed ({rem} min left < {min} min). "
    "Waiting for the next 5-hour block."
)
PARK_LIMIT_SESSION = (
    "parked: session (5-hour) usage limit hit; resets {reset}. "
    "Holding the task until the window reopens."
)
PARK_LIMIT_WEEKLY = (
    "parked: weekly usage limit hit (shared across all models); resets {reset}. "
    "Deep back-pressure until the weekly window reopens."
)
DEGRADE_OPUS = (
    "note: Opus usage limit hit; resets {reset}. Only Opus is capped -- other models "
    "still work (degradation event, not a hard stop)."
)
RETRY_TRANSIENT = (
    "transient {kind} throttle; retrying without parking (not a usage-limit stop)."
)


# --- Canary check labels (en; operational preflight report) ---
CANARY_HEADER = "=== canary preflight ==="
CANARY_LINE_OK = "[ ok ] {name}: {detail}"
CANARY_LINE_FAIL = "[FAIL] {name}: {detail}"

# --- RU: shift report ("газета") + gate cards — user-facing digest (ru) ---
RU_REPORT_TITLE = "СМЕНА orc"
RU_REPORT_SUMMARY = "смена: {done} готово, {waiting} ждут тебя, {failed} упало; съедено {pct}% окна"
RU_REPORT_NO_SHIFT = "смена не запущена. Поставь задачи (`orc add`) и запусти (`orc start`)."
RU_REPORT_EMPTY = "смена пуста: задач в очереди нет."
RU_SECTION_QUEUED = "── в очереди (смена не запущена, `orc start`) ──"
RU_ROW_QUEUED = "  • {id}  {project}"
RU_ROW_RUNNING = "  ▸ {id}  {phase:<10} {status:<8} {mins}м  {tokens}"
RU_ROW_WAITING = "  ⏸ {id}  ждёт тебя: {reason}"
RU_ROW_DONE = "  ✓ {id}  готово"
RU_ROW_FAILED = "  ✗ {id}  упало: {reason}"
RU_POOL_LINE = "  пул: {pct}% окна, {mins_left} мин осталось, RAM {ram}"
RU_SECTION_GATES = "── ждут твоего решения ──"
RU_SECTION_RUNNING = "── в работе ──"
RU_SECTION_DONE = "── завершено ──"
RU_GATE_CARD = (
    "  ⏸ {id} — {title}\n"
    "     скоуп:      {scope}\n"
    "     планка:     {bar}\n"
    "     полномочия: {authority}\n"
    "     цена ошибки: {cost}\n"
    "     ТЗ: {brief_path}"
)
