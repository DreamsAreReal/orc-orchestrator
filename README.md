# orc — autonomous task-shift loop for Claude Code

Drop ~10 tasks into a queue in the morning; your Mac picks them up and drives each one
through the `pipeline` conveyor to the end — unattended, reliably, safely.

- **Queue**: [beads](https://github.com/steveyegge/beads) (`bd`) — atomic claim, ready
  semantics, a gate is a blocking task on a human.
- **Dispatcher** (this repo, python3-stdlib): `bd ready → claim → re-validate → preflight →
  project-mutex → spawn → monitor`, with admission/back-pressure, budget caps, a watchdog,
  crash recovery, and a live newspaper.
- **Workers**: REAL interactive Claude Code sessions in Terminal.app (not headless),
  under OS-sandbox + deny-wall boundaries.

## Requirements

- macOS with a GUI (Aqua) login session — the OAuth token lives in the login Keychain,
  which is only reachable from a GUI session (hence a **user LaunchAgent**, not cron).
- `brew install beads` (`bd`), `claude` at `/opt/homebrew/bin/claude`, `ccusage` on PATH.

## Quick start

```bash
bin/orc init                                  # create the ~/.orc hub (one beads queue)
bin/orc setup                                 # configure Terminal so husk windows close (see below)
bin/orc add ~/proj "do the thing"             # add a task (repeat, or use --batch from stdin)
bin/orc start                                 # canary preflight, then run the shift
bin/orc status --newspaper                    # the digest catches up to DONE on its own
bin/orc stop                                  # kill switch: stop all workers, requeue tasks
```

All calibration lives in `~/.orc/config.json` (thresholds, caps, denylist, MCP allowlist,
terminal backend, LaunchAgent label/PATH). There are **no hard-coded thresholds** in code —
`orc init` writes the defaults; edit the file to tune.

## Autostart via LaunchAgent (F10)

```bash
bin/orc install            # write + bootstrap the user LaunchAgent (autostart at login)
bin/orc install --uninstall  # bootout + remove it
```

The generated plist:

- runs in the **Aqua** session (`LimitLoadToSessionType = Aqua`) so the dispatcher can
  reach the login Keychain and a working `claude auth` (proven: `auth_exit=0`);
- sets an explicit **PATH** and calls `claude` by **absolute path** — LaunchAgents do
  **not** inherit the interactive shell PATH;
- `KeepAlive` only on `Crashed`, so a deliberate `orc stop` (clean exit) truly stops it.

The label, PATH and claude binary all come from `config.json`.

> Keeping the Mac awake for a long shift is **not** built into orc: `caffeinate` and
> similar conflict with the user's mouse. v1 is a daytime loop (the Mac stays awake while
> active); for a long unattended shift, set sleep behaviour yourself in System Settings.

## `orc setup` — reproducible husk-window fix

Terminal.app leaves an empty "husk" window after a worker's shell exits **unless** the
profile's `shellExitAction` is `0` (close the window when the shell exits). `orc setup`
makes this reproducible for any user: it edits the orc Terminal profile's `shellExitAction`
to `0` via `plistlib`, **backing up the previous value first** (under a private
`orcPrevShellExitAction` key) so the change is reversible:

```bash
bin/orc setup            # set shellExitAction=0 on the orc Terminal profile (with backup)
bin/orc setup --revert   # restore the profile's previous shellExitAction from the backup
```

The profile is the machine's default Terminal profile (override with `terminal_profile` in
`config.json`). Quit Terminal.app before running `orc setup` so it does not overwrite the
plist on exit; the edit is written to disk regardless. Killing the worker process (which
frees its RAM) always happens; the profile fix removes the leftover empty window.

## `orc stop` — kill switch (G10)

`orc stop` SIGTERMs every worker's session, waits up to `stop_grace_seconds`, then SIGKILLs
any survivor (bounded, typically well under 10s). Each stopped task is returned to `ready`
in bd so the next shift re-serves it — nothing is lost.

## Threat model — what is walled, what is not

orc runs REAL Claude Code workers **unattended**, under `--dangerously-skip-permissions`
(bypass), with the permissions of your user account. Be honest with yourself about what
that means before you point it at a project. This section says plainly what the walls
stop and what they do **not**.

**Closed at the kernel / check level (enforced, proven by tests):**

- **Destructive writes and any write outside the workspace** — blocked at the syscall
  level by the OS-sandbox (seatbelt). Obfuscated escapes (`base64 | bash`, `python
  shutil.rmtree`, `find -delete`) are stopped too, because the wall is on the write
  syscall, not on a command pattern.
- **Deleting files outside the workspace** — same seatbelt wall.
- **Stealing `~/.ssh`** — seatbelt denies `file-read*` on `~/.ssh` (private keys are
  unreadable even by an obfuscated reader).
- **Secret env vars** — `unset` on spawn (denylist), so `ANTHROPIC_API_KEY`, `AWS_*`,
  `GITHUB_TOKEN`, `*_SECRET`, … do not reach the worker. Claude's own OAuth (Keychain)
  is untouched.
- **Reward-hacking a "DONE"** — a task is closed ONLY on a real external fact (a non-empty
  commit or a changed/created deliverable since the worker started), never on the worker's
  self-report. Fake-done is parked, not trusted.
- **`git push`** — blocked over both HTTPS and SSH: the worker's env is stripped of push
  credentials (`GIT_ASKPASS=/usr/bin/false`, `credential.helper=''`, `GIT_SSH_COMMAND`/
  `SSH_AUTH_SOCK` neutralized), so any git process in the worker tree fails on auth. The
  operator pushes; the worker prepares and reports.
- **MCP servers** — the worker's allowlist is empty by default; nothing is enabled unless
  you add it to `config.json`.

**Still open (be honest):**

- **A worker CAN READ files outside the project.** The seatbelt confines *writes*, not
  reads (except `~/.ssh`). A worker can read any file your user can read. (One read is
  *intentional*: a pipeline worker reads the pipeline skill tree at `~/.claude/skills/**`
  — `SKILL.md`, `references/`, `templates/`, `agents/` — read-only, so it can run the full
  conveyor. That tree stays **un-writable**: a worker cannot edit its own conveyor.)
- **The network policy is your choice — open (default) or deny.** See below.

### Network policy: `open` vs `deny` (and per-task `--offline`)

The seatbelt cannot do a reliable per-host allowlist in a user session, so orc gives you a
convenient coarse choice instead of a fragile allowlist:

- **`network_policy: "open"` (default).** The worker's outbound network is open. Claude
  works normally (it needs the API), dependency installs / `git fetch` / brew all work.
  Threat-model cost: an agent under bypass with read access **could in theory exfiltrate**
  file contents over the open network (a raw `curl`/`nc` is not blocked; `git push` still
  is). Use this for the normal case where the task needs the network.
- **`network_policy: "deny"`.** The worker's network is **cut** at the syscall level
  (seatbelt `(deny network*)`). Maximum isolation: **no exfiltration channel at all.** The
  trade-off: the worker cannot download dependencies or reach any API besides the local
  Claude session it is already in — so use it only for **fully-local tasks** (offline
  refactors, local codegen/tests) that need no network. Set it in `~/.orc/config.json`.
- **Per-task `orc add <project> "<task>" --offline`.** Cuts the network for **one task**
  regardless of the shift-wide policy — handy when most tasks need the net but one is a
  local-only job you want fully isolated. A per-task `--offline` can only *tighten* to deny,
  never loosen a shift-wide `deny` back to open.

Proven at the OS level (`docs/evidence/p2/network-policy/spike.sh`): under `deny` a network
syscall returns `EPERM` (blocked by seatbelt); under `open` it reaches the network stack.

- **The sandbox is mandatory.** It is the primary wall. `allow_no_sandbox=true` removes it
  (no write wall, no `~/.ssh` read wall) — a loud `[WARN]` in the canary and a banner in
  the newspaper fire on every shift start when it is set. Do not run unattended with the
  sandbox off.

**Bottom line:** run tasks and projects **you trust**. The strong walls (write, delete,
`~/.ssh` read, push, secret env, reward-hack) are real and hold under obfuscation. If a task
is fully local, `deny` / `--offline` removes the exfiltration surface entirely; otherwise
the network is open, so treat sensitive projects with caution and keep the sandbox on.

## Verification

- `python3 -m pytest tests/` — unit tests (config, admission, watchdog, recovery, …).
- `bash .verify/launchagent.sh` — LaunchAgent auth=0, kill switch, config-driven calibration.
- `bash .verify/sandbox-walls.sh` — OS-sandbox blocks obfuscated escapes (F13).
- `bash .verify/e2e-loop-close.sh` — the full add → shift → newspaper loop closes.
