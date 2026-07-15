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
