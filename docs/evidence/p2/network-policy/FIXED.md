# P2: network policy -- convenient open/deny choice + per-task --offline

## What
The old knob was `sandbox_deny_network` (bool). P2 gives the operator a convenient choice:
- config `network_policy: "open"` (default) -- network open (claude needs it; installs work);
- config `network_policy: "deny"` -- network cut at the syscall level (max isolation, no
  exfiltration channel; for fully-local tasks with no dependency download / API);
- per-task `orc add <project> "<task>" --offline` -- deny for ONE task regardless of the
  shift policy (can only tighten, never loosen a shift-wide deny).

## How it flows
- `config.network_deny(cfg, task_offline)` resolves the effective deny: task_offline OR
  network_policy=="deny" OR the deprecated sandbox_deny_network alias.
- `cli add --offline` records `meta["offline"]=True` on the bead.
- `dispatcher.spawn_one` reads `meta["offline"]`, calls `config.network_deny`, passes
  `deny_network` to `spawn.spawn_worker` -> `build_start_command` -> `_maybe_sandbox` ->
  `sandbox.write_profile(deny_network=...)` -> the seatbelt profile gets `(deny network*)`.

## Proof at the OS level (spike.sh / spike.log)
Loopback connect under a real `sandbox-exec` profile:
- policy OPEN -> `NET_OK_REACHED_STACK` (socket/connect syscalls permitted).
- policy DENY -> `NET_BLOCKED_EPERM` (seatbelt `(deny network*)` blocks the syscall).

## Regression tests
- `tests/test_config.py`: default open; deny cuts; deprecated alias; per-task offline
  tightens; offline-only-tightens-never-loosens.
- `tests/test_sandbox.py`: open policy -> no `(deny network*)` in the spawn profile;
  deny policy and per-task deny_network=True -> `(deny network*)` present.
- `tests/test_cli.py`: `--offline` sets `meta["offline"]=True`; absent -> no offline meta.

## Docs
README "Threat model" section -> new "Network policy: open vs deny (and per-task --offline)"
subsection (open = theoretical exfiltration over open net; deny/--offline = no channel).
