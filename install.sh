#!/bin/bash
# orc — one-command setup. Idempotent: safe to re-run.
# Installs dependencies, initializes the hub, and configures the Terminal profile.
set -euo pipefail

say() { printf '\033[1m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$1"; }

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# --- prerequisites --------------------------------------------------------- #
[ "$(uname)" = "Darwin" ] || { warn "orc targets macOS (needs Terminal.app + Keychain + seatbelt)."; }

if ! command -v python3 >/dev/null; then warn "python3 not found — install it first."; exit 1; fi

if ! command -v brew >/dev/null; then
  warn "Homebrew not found. Install from https://brew.sh, then re-run."; exit 1
fi

say "Checking beads (bd) — the task queue…"
if ! command -v bd >/dev/null; then
  say "Installing beads via Homebrew…"; brew install beads
else say "  bd already installed: $(bd --version 2>/dev/null | head -1)"; fi

say "Checking ccusage — the limit gauge…"
if ! command -v ccusage >/dev/null && ! npx --no-install ccusage --version >/dev/null 2>&1; then
  warn "  ccusage not on PATH; orc will call it via 'npx ccusage@latest' (needs Node/npm)."
fi

say "Checking claude…"
CLAUDE_BIN="${CLAUDE_BIN:-/opt/homebrew/bin/claude}"
if [ ! -x "$CLAUDE_BIN" ]; then
  ALT="$(command -v claude || true)"
  [ -n "$ALT" ] && { warn "  claude not at $CLAUDE_BIN but found at $ALT — set claude_bin in ~/.orc/config.json"; } \
                 || warn "  claude not found — install Claude Code and log in (claude auth)."
fi

# --- initialize ------------------------------------------------------------ #
say "Initializing the orc hub (~/.orc)…"
bin/orc init

say "Configuring the Terminal profile so worker windows close cleanly…"
bin/orc setup || warn "  orc setup skipped/failed — husk windows may linger; see README."

# --- smoke ----------------------------------------------------------------- #
say "Running the test suite (stdlib only)…"
if python3 -m pytest tests/ -q >/tmp/orc-install-tests.log 2>&1; then
  say "  $(tail -1 /tmp/orc-install-tests.log)"
else warn "  some tests failed — see /tmp/orc-install-tests.log"; fi

cat <<'EOF'

==> orc is ready.

  bin/orc add ~/path/to/project "your task"     # queue a task (project must be a git repo)
  bin/orc start                                  # run the shift (spawns real Claude Code)
  bin/orc status --newspaper                     # see what got done
  bin/orc stop                                   # kill switch

Docs: README.md (usage) · ARCHITECTURE.md (why) · USAGE-RU.md (RU cheatsheet)
Optional autostart: bin/orc install
EOF
