# P2 fix: pipeline references reachable under the sandbox

## Symptom (first live pipeline run, 2026-07-15)
The sandboxed pipeline worker logged that `~/.claude/skills/pipeline/references/*` were
unreachable (see `docs/evidence/pipeline-live/02-task-STATE-run1.md` line 22 and
`05-task-STATE-run2.md` lines 8-9). The worker ran on `SKILL.md` invariants only, without
the full conveyor (references/, templates/, agents/).

## Root cause
Not the seatbelt sandbox (reads are allow-default there) but the **F1 PreToolUse read-wall**
(`worker_walls._inspect_file_tool`): it blocked the `Read` tool for ANY path outside the task
workspace, so `Read ~/.claude/skills/.../phase-1-research.md` returned exit 2 (blocked).

## Fix
1. `worker_walls`: the `Read` tool now allows paths under `~/.claude/skills` (READ-only). All
   other outside reads stay blocked; `~/.ssh` stays blocked; `Write`/`Edit` into skills stay
   blocked (a worker cannot edit its own conveyor).
2. `sandbox.build_profile`: adds an EXPLICIT last-wins `(deny file-write* (subpath
   ~/.claude/skills))` so the write-deny is unmistakable and cannot be re-opened by an extra
   writable subpath. Reads were already allow-default (no read-deny on skills).

## Proof (real seatbelt + real hook -- `spike.sh`, `spike.log`)
- READ `~/.claude/skills/pipeline/references/phase-1-research.md` under `sandbox-exec` -> RC 0,
  content read.
- WRITE `~/.claude/skills/pipeline/SKILL.md` under `sandbox-exec` -> "Operation not permitted";
  `grep INJECTED SKILL.md` = 0 (integrity held).
- F1 hook: Read-reference = allow (rc 0), Read `/etc/hosts` = block (rc 2), Write-into-skills
  = block (rc 2).

## Regression tests
- `tests/test_worker_walls.py`: read-reference-allowed, read-SKILL.md-allowed,
  read-other-outside-blocked, write-into-skills-blocked.
- `tests/test_sandbox.py`: profile read-allows skills but write-denies them (last-wins);
  not read-denied; not write-allowed.
