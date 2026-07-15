# AGENTS.md — orientation for an AI agent working on this repo

You're looking at **`orc`**: a CLI that runs a queue of tasks through Claude Code
autonomously on macOS. Read this first, then [ARCHITECTURE.md](ARCHITECTURE.md).

## Get oriented fast

- **What it is / how to use**: [README.md](README.md), [USAGE-RU.md](USAGE-RU.md) (RU).
- **Why it's built this way**: [ARCHITECTURE.md](ARCHITECTURE.md) — the "Key decisions" section
  explains the reasoning behind every non-obvious choice, plus a full code map.
- **Code**: [`src/orc/`](src/orc/) — see the code map in ARCHITECTURE.md. Tests in `tests/`.

## Run the tests

```bash
python3 -m pytest tests/ -q     # 281 tests, python3-stdlib only, no external deps
```
Deterministic end-to-end checks live in `.verify/*.sh` (security walls, loop-close, gate,
scale-shift). They use the `ORC_SPAWN_CMD_OVERRIDE` seam to avoid spending live Claude
quota — the real spawn/kill/window path is exercised, only the in-terminal program is stubbed.

## Non-negotiable invariants (do not regress these)

1. **Gate on real quota, not on the clock.** `ccusage remainingMinutes` is time-until-block-reset,
   NOT remaining quota. Park ONLY on a real CLI limit-string. (This bug bit three times.)
2. **DONE requires a real non-empty deliverable** — never accept `Status: DONE` text alone,
   empty `touch`, or `--allow-empty` commit. That's the anti-reward-hacking wall.
3. **The OS-sandbox is the primary security wall**, the pattern-hook is secondary (obfuscation
   bypasses greps). File-writes only inside the workspace; `~/.ssh` read denied; push creds stripped.
4. **One writer per repo** (project-mutex). Workers are real interactive `claude`, not headless.
5. **No hard-coded thresholds** — everything tunable lives in `~/.orc/config.json`.
6. Worker prompts go through a **file** (`.orc/prompt-*.txt`), never inlined (multiline breaks the shell).

## Conventions

- Language: code/comments/commits in **English**; user-facing strings (newspaper, notifications)
  in **Russian** (this is intentional — see `strings.py`, `RU_*`).
- Style: python3.9-compatible (no `match`, no 3.10+ typing), stdlib only.
- Every "it works" claim needs a real command→output as proof.
- Changing behaviour → add a regression test in `tests/`.
