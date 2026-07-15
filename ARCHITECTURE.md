# Architecture — what, why, and the reasoning behind each decision

This document is for anyone (human or AI agent) who needs to understand **why** `orc` is
built the way it is — not just what it does. The "what" is in [README.md](README.md).

## The one-sentence goal

Drop ~10 tasks into a queue in the morning; the Mac picks them up, runs each through the
`pipeline` quality conveyor to completion, and shows a "newspaper" of results — unattended,
reliably, safely, spending otherwise-idle Claude Max quota.

## Three layers

```
1. QUEUE      beads (bd)         atomic claim, dependency/ready semantics; a gate = a
                                 blocking task assigned to a human.
2. DISPATCHER src/orc/*.py       the glue we wrote: ready → claim → re-validate → preflight
              (python3-stdlib)   → project-mutex → spawn → monitor; admission/back-pressure,
                                 budget, watchdog, crash recovery, the newspaper.
3. WORKERS    real Claude Code   interactive `claude` in Terminal.app (NOT headless), each
                                 confined by an OS-sandbox + deny-walls, driven via the
                                 `pipeline` skill.
```

## Key decisions and the reasoning

### One worker at a time (not a parallel fleet)
The target machine has 8 GB RAM; a live Claude Code session uses ~2.5–8 GB. So the loop is a
**sequential shift**, not a swarm. `project-mutex` is therefore mostly moot on this box but
kept for correctness (one writer per repo — the invariant that a repo is only ever touched
by one worker). Scaling to parallel projects is a P2 that needs more RAM.

### Gate on real QUOTA, not on a clock — the load-bearing lesson
`ccusage` reports `remainingMinutes` = **time until the 5-hour block resets**, which is
NOT how much quota is left. Three separate places originally confused the two and each
misbehaved:
- **admission** parked tasks when `remaining_minutes < 5` ("window almost closed") even
  with 70% quota free — so it refused to work right before a fresh block opened;
- the **newspaper** reported "% of window consumed" = elapsed time, printing "99% used"
  when quota was full;
- **canary** failed the shift when the ccusage window was momentarily inactive (between
  blocks), sending a false "shift did not start" notification.

All three are fixed to the same rule: **spend quota while it exists; park ONLY on a real
CLI limit-string** (`session/weekly/opus "limit reached · resets…"`; 429/529 → retry).
`remaining_minutes` is now informational only. This is the single most important thing to
understand about the system.

### DONE requires a real, non-empty external fact (anti reward-hacking)
A worker cannot close a task by writing `Status: DONE` in `STATE.md`. The dispatcher
(`external_progress` in `dispatcher.py`/`gitutil.py`) accepts completion only on a **real
deliverable**: a git commit with a non-empty diff to a non-orc-managed file, or a non-empty
artifact file. Empty `touch`, `git commit --allow-empty`, or STATE-only → parked as
`suspected-fake-done`. This is the Replit-incident lesson (agents fake success); it survived
adversarial re-verification.

### OS-sandbox is the PRIMARY wall; the hook is secondary
A PreToolUse pattern-hook that greps for `rm -rf`/`git push` is trivially bypassed by
obfuscation (`base64|bash`, `python -c shutil.rmtree`, `find -delete`). So the real wall is
a **macOS seatbelt profile** (`sandbox.py`): file-writes are permitted ONLY inside the
project workspace; everything else is denied at the kernel, regardless of how the write was
reached. Proven with 10+ obfuscated escapes (sentinel survived). `~/.ssh` read is denied;
git push credentials are stripped from the worker env (HTTPS + SSH). The pipeline skill
tree (`~/.claude/skills`) is readable but not writable, so a worker runs the full conveyor
without being able to alter it. **Honest residual risk**: reads outside the project are
allowed and the network is on (Claude needs it) → content exfiltration is theoretically
possible. Mitigation: run trusted tasks, or `--offline` / `network_policy: deny`.

### The multiline-prompt spawn bug (why prompts go through a file)
A worker's start prompt is multiline and full of quotes/backticks (a gate task carries
STATE.md content). Inlining it into the `osascript do script "…"` argument breaks the
AppleScript/shell parse — the shell hangs at a `quote>` continuation and `claude` never
launches (an empty window). Fix: the prompt is written to `<project>/.orc/prompt-<session>.txt`
and read back with `claude "$(cat …)"`, so the launch command is a single line regardless of
prompt content. This is why you'll see a prompt file, not an inline prompt.

### Terminal.app, not Ghostty (a spike result)
The default terminal backend is Terminal.app because Ghostty 1.3.1's `-e` did NOT execute
the worker command (verified with a spike: 12+ variants, all opened an empty
window). Terminal executes reliably. The husk-window problem (empty window left after the
worker's shell exits) is solved by setting the Terminal profile's `shellExitAction=0` via
`orc setup` — done through `defaults`/plist (AppleScript profile-set is TCC-blocked).

### python3-stdlib, JSON config (ADR-0001/0002)
The dispatcher is state + process management + JSON parsing + testable logic → python
stdlib (json/subprocess/signal/argparse), zero external deps. `tomllib` is absent in the
system python 3.9.6, so config is JSON, not TOML. bash is used only for thin wrappers
(osascript spawn, LaunchAgent runner, .verify E2E scripts).

## Code map (`src/orc/`)

| File | Responsibility |
|---|---|
| `cli.py` | argparse CLI: init/add/start/status/stop/new-shift/daemon/setup/install |
| `dispatcher.py` | the loop core: claim → preflight → mutex → spawn → poll completions |
| `admission.py` | pre-spawn gate: ready>0, RAM, real limit-string (NOT window time) |
| `spawn.py` / `spawn_ghostty.py` | build the launch command; sandbox-wrap; prompt-file |
| `sandbox.py` | the seatbelt profile (workspace-write-only; ~/.ssh deny; skills read) |
| `worker_walls.py` | per-project `.claude/settings.json` deny-walls; secret-env strip |
| `watchdog.py` | heartbeat + loop/silence detect (incl. A/B/A/B), external-progress |
| `canary.py` | preflight checks (bd/auth/RAM hard; ccusage/notify informational) |
| `shift.py` | on-disk shift state (workers/parked/done/failed), atomic writes |
| `report.py` | the newspaper: real token spend, gate cards, fail section |
| `gitutil.py` | non-empty-commit / real-deliverable detection (anti reward-hack) |
| `beads.py` / `probes.py` / `notify.py` / `config.py` / `strings.py` | queue, ccusage/ram probes, macOS notify, config defaults, RU/EN strings |

## Tests

`python3 -m pytest tests/ -q` → 281 tests, python3-stdlib only, zero external deps. The
worker-spawn seam `ORC_SPAWN_CMD_OVERRIDE` lets tests drive worker output deterministically
without burning live Claude quota (the spawn/window/tty/kill path stays real).
