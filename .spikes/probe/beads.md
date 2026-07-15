# Beads (bd) Queue Cycle Test Results

**Status: SUCCESS** ✓

## Installation
- **Method**: `brew install beads`
- **Version**: 1.1.0 (Homebrew)
- **Dependency**: dolt 2.1.10 (auto-installed)
- **Setup time**: ~2 minutes

## Cycle Test Summary

### Test Setup
```bash
cd /Users/admin/orchestrator/.spikes/probe/beads-test
bd init
```

### Create Tasks
```bash
bd create "Task A: prepare data"      # beads-test-57q
bd create "Task B: intermediate step" # beads-test-gr8
bd create "Task C: final step"        # beads-test-h6p
```

### Create Dependency (C blocked by A)
```bash
bd dep add beads-test-h6p beads-test-57q
# Output: beads-test-h6p (Task C) depends on beads-test-57q (Task A) (blocks)
```

### Check Ready Before Closing A
```bash
bd ready --json
# Result: C NOT in ready list (blocked by open A) ✓
# Result: B IS in ready list (no dependencies) ✓
```

### Atomic Claim
```bash
bd update beads-test-57q --claim
# Output: ✓ Updated + status changed to IN_PROGRESS
# bd show beads-test-57q confirms: [◐ IN_PROGRESS]
```

### Close A and Verify C Becomes Ready
```bash
bd close beads-test-57q
bd ready --json
# Result: C NOW in ready list (A is closed) ✓
# Result: dependency metadata preserved in JSON ✓
```

## Key Findings

| Aspect | Result |
|--------|--------|
| Installation | Brew: working, 1.1.0 |
| Initialization | `bd init` creates .beads/, CLAUDE.md, hooks |
| Task creation | `bd create <title>` works, auto-ID |
| Dependencies | `bd dep add <dependent> <blocker>` (order: dependent first) |
| Ready query | `bd ready --json` filters correctly by dependencies |
| Atomic claim | `bd update <id> --claim` sets assignee + status in one op |
| Dependency blocking | `ready` respects blocking: blocked tasks hidden until blocker closed |
| Cycle detection | `bd dep` prevents cycles ("cycle" error on violation) |

## Gotchas

1. **Link order**: `bd dep add C A` means "C depends on A", NOT the reverse.
   - Use `bd dep <blocker> --blocks <blocked>` for clarity.
2. **ready output**: Includes full dependency tree when `--json` (not just IDs).
3. **Claim idempotence**: `--claim` is safe to run multiple times (idempotent).

## Commands Reference

| Command | Purpose |
|---------|---------|
| `bd init` | Initialize workspace, create .beads/ |
| `bd create <title>` | Create task, return auto-ID |
| `bd dep add <C> <A>` | C depends on A (C blocked until A done) |
| `bd ready --json` | List tasks with no blocking dependencies |
| `bd update <id> --claim` | Atomically assign + mark in_progress |
| `bd close <id>` | Close task, unblock dependents |
| `bd show <id>` | Show task + dependencies |

## Conclusion

**Beads queue cycle is executable on Mac.** All core operations (init, create, dependencies, ready, claim, close) work as specified. Dependency blocking is enforced at query time. Ready to integrate into orchestrator loop.
