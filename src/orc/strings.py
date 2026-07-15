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

NEW_SHIFT_DONE = "orc new-shift: cleared previous shift state; the ready queue is visible again."
NEW_SHIFT_WORKERS_LIVE = (
    "error: workers are still live; run `orc stop` first so their tasks return to ready "
    "before clearing the shift.")

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
# Sandbox fail-closed (P5): the OS-sandbox is the PRIMARY wall. If it is enabled but the
# machine has no `sandbox-exec` (unavailable), or it is disabled without an explicit
# operator opt-out, orc REFUSES to spawn an unsupervised worker rather than running it with
# no wall (fail-open). Set allow_no_sandbox=true in config to run without it deliberately.
PARK_SANDBOX_UNAVAILABLE = (
    "parked: OS-sandbox (seatbelt) is unavailable on this machine but the sandbox wall is "
    "required. Refusing to spawn an unsupervised worker without its primary wall. "
    "Install sandbox-exec or set allow_no_sandbox=true to run without it (not recommended)."
)
PARK_SANDBOX_DISABLED = (
    "parked: the OS-sandbox is disabled (sandbox=false) but allow_no_sandbox is not set. "
    "Refusing to spawn an unsupervised worker without its primary wall. "
    "Set allow_no_sandbox=true to run without the sandbox deliberately."
)
# Anti-reward-hacking (B1): a worker wrote DONE in STATE.md but left NO external fact
# (no new git commit, no changed/created artifact since it started). DONE is confirmed by
# facts in the world, never by the worker's self-report (brief G1 / Replit-class risk).
PARK_SUSPECTED_FAKE_DONE = (
    "parked: worker claims DONE but produced NO external fact "
    "(no new commit, no changed/created artifact since it started). "
    "Suspected fake-done -- inspect before trusting; DONE is confirmed by the world, "
    "not the worker's self-report."
)

# --- admission / back-pressure (F5) reasons (en; operator terminal) ---
PARK_LOW_RAM = (
    "parked: not enough free RAM to spawn a worker safely "
    "({ram} MB free < {min} MB). Waiting for memory to free up."
)
# REMOVED as a park reason (2026-07-15, user-found live bug): orc used to park a task
# when the 5-hour block was about to reset ("window is nearly closed, N min left"). That
# was WRONG -- `remaining_minutes` is the time until the block RESETS, not remaining quota,
# so a low value means fresh quota is imminent, not exhausted. It self-blocked the loop
# with ~70% quota free. Back-pressure now reacts only to real CLI limit-strings. This
# string is repurposed as a benign LOG note when ccusage returns no window telemetry (we
# ADMIT anyway -- no telemetry is not an exhausted pool).
WINDOW_NO_TELEMETRY = (
    "note: ccusage returned no active usage window (no telemetry). Admitting anyway -- "
    "a missing window is not an exhausted pool; real limits surface via CLI limit-strings."
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

# --- budget caps (F6) reasons (en; operator terminal) ---
PARK_TASK_BUDGET = (
    "parked: task exceeded its token budget ({spent} > cap {cap}). "
    "Stopped the worker to protect the weekly pool; review before resuming."
)
PARK_SHIFT_BUDGET = (
    "shift token cap reached ({spent} >= cap {cap}); not starting new tasks."
)

# --- watchdog (F7) escalation (en; operator terminal) ---
WATCHDOG_ESCALATE = (
    "parked: watchdog hit the restart cap ({cap}) after repeated {verdict}; "
    "escalating to you. The worker made no real progress (external check) -- "
    "inspect the task before resuming."
)


# --- Canary check labels (en; operational preflight report) ---
CANARY_HEADER = "=== canary preflight ==="
CANARY_LINE_OK = "[ ok ] {name}: {detail}"
CANARY_LINE_FAIL = "[FAIL] {name}: {detail}"
CANARY_LINE_WARN = "[WARN] {name}: {detail}"
# B2 opt-out is LOUD (not silent): running with allow_no_sandbox removes the OS-sandbox,
# which is the ONLY layer that blocks reading ~/.ssh + direct-ssh exfiltration (the env-only
# strip does NOT stop a direct `ssh`/`scp` -- proven in the reverify). The operator must be
# told at every shift start that the worker runs WITHOUT its exfiltration wall.
CANARY_NO_SANDBOX_DETAIL = (
    "OS-sandbox DISABLED (allow_no_sandbox=true): worker runs WITHOUT its primary wall -- "
    "reading ~/.ssh and SSH/network exfiltration are NOT blocked. Not for unattended runs.")

# --- RU: shift report ("газета") + gate cards — user-facing digest (ru) ---
# B2 opt-out warning, surfaced in the newspaper (ru; user-facing) so the operator sees the
# missing wall in the morning digest, not just in the start-time canary.
RU_NO_SANDBOX_WARN = (
    "  ! ВНИМАНИЕ: OS-песочница ОТКЛЮЧЕНА (allow_no_sandbox) — воркер БЕЗ главной стены:\n"
    "     чтение ~/.ssh и SSH/сетевая эксфильтрация НЕ заблокированы. Не для безнадзорных смен.")
RU_REPORT_TITLE = "СМЕНА orc"
# Summary reports the REAL shift spend (token/cost delta), NOT the block-reset timer. The
# old summary showed elapsed 5-hour-block time as if it were quota use -- misleading
# (2026-07-15, user-caught). {spend} is a pre-rendered clause (RU_SPEND_SHIFT_*) or empty.
# We show the ABSOLUTE spend, never an invented percent: ccusage does not know the Max x20
# subscription cap, so a "% of quota" would be fabricated.
RU_REPORT_SUMMARY = "смена: {done} готово, {waiting} ждут тебя, {failed} упало{spend}"
# Pre-rendered shift-spend phrases (honest absolute figures, not a percent of an unknown cap).
RU_SPEND_SHIFT_TOKENS = "потрачено ~{tokens} токенов за смену"
RU_SPEND_SHIFT_COST = "потрачено ~${cost} за смену"
RU_SPEND_UNKNOWN = "расход неизвестен (ccusage недоступен)"
RU_REPORT_NO_SHIFT = "смена не запущена. Поставь задачи (`orc add`) и запусти (`orc start`)."
RU_REPORT_EMPTY = "смена пуста: задач в очереди нет."
RU_SECTION_QUEUED = "── в очереди (смена не запущена, `orc start`) ──"
RU_ROW_QUEUED = "  • {id}  {project}"
RU_ROW_RUNNING = "  ▸ {id}  {phase:<10} {status:<8} {mins}м  {tokens}"
RU_ROW_WAITING = "  ⏸ {id}  ждёт тебя: {reason}"
# Done rows differentiate the terminal kind (design.md status vocabulary): plain DONE is
# finished; DONE-WAVE-N proposed another wave (not the end); BETA awaits your decision.
RU_ROW_DONE = "  ✓ {id}  готово{spend}"
RU_ROW_DONE_WAVE = "  ✓ {id}  готово (предложена волна){spend}"
RU_ROW_BETA = "  ◐ {id}  бета — ждёт твоего решения{spend}"
RU_SPEND_SUFFIX = "  ~{spent} ток."
RU_ROW_FAILED = "  ✗ {id}  упало: {reason}"
# Pool footer: HONEST labels. Left = real shift spend (tokens/USD). Middle = minutes until
# the LIMIT WINDOW RESETS -- a schedule timer, labelled as such (NOT "spent"; a low value
# means fresh quota is imminent). Right = free RAM.
RU_POOL_LINE = "  пул: {spend}; до сброса окна лимитов {mins_left} мин; RAM {ram}"
RU_SECTION_GATES = "── ждут твоего решения ──"
# Shown in place of a gate card's rich detail when bd is transiently unavailable: the
# newspaper degrades (prints what it can) instead of crashing (P8).
RU_GATE_CARD_DEGRADED = (
    "  ⏸ {id} — ждёт тебя: {reason}\n"
    "     (детали задачи временно недоступны: очередь bd не отвечает)"
)
RU_SECTION_RUNNING = "── в работе ──"
RU_SECTION_DONE = "── завершено ──"
RU_GATE_CARD = (
    "  ⏸ {id} — {title}\n"
    "     скоуп:      {scope}\n"
    "     планка:     {bar}\n"
    "     полномочия: {authority}\n"
    "     цена ошибки: {cost}\n"
    "     ТЗ: {brief_path}{irreversible}"
)
# Appended to a gate card when the decision touches an irreversible action: such gates
# are NEVER approved as part of a batch (design.md F9) -- the operator answers each alone.
RU_GATE_IRREVERSIBLE = (
    "\n     ! необратимое действие — решается ОТДЕЛЬНО, не в батче")

# --- F9 gate notification (ru; user-facing macOS notification) ---
NOTIFY_GATE_TITLE = "orc: задача ждёт твоего решения"
NOTIFY_GATE_BODY = "{id} — {title}. Открой газету (`orc status --newspaper`)."
NOTIFY_GATE_SUBTITLE = "гейт: {scope}"

# --- G7 canary-fail notification (ru; user-facing macOS notification) ---
# The whole point of the canary is "the shift silently did not start this morning and you
# don't know". On a canary failure we PUSH a notification so the operator finds out even
# in unattended mode (the newspaper won't catch up -- there is no shift).
NOTIFY_CANARY_TITLE = "orc: смена НЕ стартовала"
NOTIFY_CANARY_BODY = "канарейка упала ({n} провал(ов)): {checks}. Смена не запущена."
NOTIFY_CANARY_SUBTITLE = "проверь: {checks}"

# --- F10: LaunchAgent install / kill switch / setup (en; operator terminal) ---
LA_INSTALLED = "LaunchAgent installed and bootstrapped: {label} (plist {path})"
LA_UNINSTALLED = "LaunchAgent removed: {label}"
LA_NOT_LOADED = "LaunchAgent not loaded: {label}"
LA_STATUS_LOADED = "LaunchAgent loaded: {label} (last exit {code})"
LA_BOOTSTRAP_FAIL = "error: launchctl bootstrap failed: {err}"
STOP_NO_WORKERS = "orc stop: no active workers; nothing to stop."
STOP_DONE = "orc stop: stopped {n} worker(s) in {secs}s; their task(s) returned to ready."
STOP_TASK_REQUEUED = "  requeued to ready: {task}"
SETUP_PROFILE_DONE = (
    "orc setup: Terminal profile '{profile}' shellExitAction set to 0 "
    "(close window when the shell exits); previous value {old} backed up.")
SETUP_PROFILE_ALREADY = (
    "orc setup: Terminal profile '{profile}' already closes windows on exit "
    "(shellExitAction=0); nothing to change.")
SETUP_PROFILE_NONE = (
    "orc setup: could not resolve a Terminal profile to configure "
    "(is Terminal.app configured?). Skipped the husk-window fix.")
SETUP_LA_HINT = "orc setup: run `orc install` to autostart the dispatcher via LaunchAgent."
