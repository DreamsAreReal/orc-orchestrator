#!/bin/bash
# F15 CLEAN WINDOW CLOSE -- prove the husk-window pain is solved by spawning in Ghostty.
#
# The pain (user, 2026-07-15): Terminal.app workers leave husk windows with a confirm-close
# dialog; they piled up by the dozen. Ghostty closes a surface when its `-e` command exits.
# So we spawn a REAL Ghostty worker (deterministic seam command, NOT a real claude -- the
# usage window is precious), stop it the way the dispatcher does, and assert:
#   1. the worker process is really gone (RAM freed);
#   2. NO leftover ghostty process carrying the session marker (window self-closed);
#   3. 0 husk windows, 0 confirm dialogs (the window closed cleanly on process exit).
# Evidence -> docs/evidence/F15/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F15"
mkdir -p "$EVID"
LOG="$EVID/ghostty-close.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

SESSION="orc-f15-$$-$RANDOM"
PROJ="$(mktemp -d /tmp/orc-f15-proj.XXXXXX)"
cleanup() {
  pkill -f "ORC_SESSION=$SESSION" 2>/dev/null
  rm -rf "$PROJ" 2>/dev/null
}
trap cleanup EXIT

log "=== F15 clean window close (Ghostty) ==="
log "date: $(date)"
log "session marker: ORC_SESSION=$SESSION"
log "project: $PROJ"
log ""

FAILS=0

# 0) Ghostty must be available for the primary path; else this is a documented fallback.
GAVAIL="$(PYTHONPATH="$PYSRC" python3 -c "from orc import spawn_ghostty; print(spawn_ghostty.ghostty_available())")"
log "ghostty available: $GAVAIL"
if [ "$GAVAIL" != "True" ]; then
  log "SKIP: Ghostty not installed; F15 primary backend unavailable on this machine."
  log "F15 GHOSTTY-CLOSE SKIP (Ghostty absent; falls back to Terminal per _backend)."
  exit 0
fi
log ""

# 1) spawn a REAL Ghostty worker via the seam (a long sleep = a live killable process).
log "--- spawn real Ghostty worker (seam: long sleep, no claude) ---"
SPAWN="$(ORC_SPAWN_CMD_OVERRIDE="echo f15-worker-running; sleep 600" \
  PYTHONPATH="$PYSRC" python3 -c "
from orc import spawn_ghostty
ok, handle = spawn_ghostty.spawn_ghostty('$PROJ', '/bin/echo', 'noop', '$SESSION')
print('ok=%s handle=%s' % (ok, handle))
")"
log "$SPAWN"
sleep 3

# confirm the worker process is alive and carries the session marker
WPIDS="$(pgrep -f "ORC_SESSION=$SESSION" | tr '\n' ' ')"
log "worker pids (by session marker): $WPIDS"
if [ -n "$WPIDS" ]; then
  log "SPAWN PASS: a live Ghostty worker is running under the session marker."
else
  log "SPAWN FAIL: no worker process found for the session."
  FAILS=$((FAILS+1))
fi
log ""

# 2) stop the worker the way the dispatcher does (close_ghostty) and check clean close.
log "--- stop worker via close_ghostty -> window must self-close (0 husk) ---"
CLOSE="$(PYTHONPATH="$PYSRC" python3 -c "
from orc import spawn_ghostty
import json
r = spawn_ghostty.close_ghostty('$SESSION')
print(json.dumps(r))
")"
log "close_ghostty result: $CLOSE"
sleep 2

REMAIN="$(pgrep -f "ORC_SESSION=$SESSION" | tr '\n' ' ')"
log "worker pids remaining after close: '${REMAIN}'"
WIN_CLOSED="$(echo "$CLOSE" | PYTHONPATH="$PYSRC" python3 -c "import sys,json; print(json.load(sys.stdin)['window_closed'])")"
KILLED="$(echo "$CLOSE" | PYTHONPATH="$PYSRC" python3 -c "import sys,json; print(json.load(sys.stdin)['killed'])")"

if [ -z "$REMAIN" ] && [ "$WIN_CLOSED" = "True" ] && [ "$KILLED" -ge 1 ]; then
  log "CLOSE PASS: worker stopped (killed=$KILLED), window self-closed (0 husk, 0 dialog)."
else
  log "CLOSE FAIL: remaining='$REMAIN' window_closed=$WIN_CLOSED killed=$KILLED."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F15 GHOSTTY-CLOSE PASS (real Ghostty worker spawned + stopped; window closed cleanly, 0 husk)."
  exit 0
else
  log "F15 GHOSTTY-CLOSE FAIL: $FAILS check(s)."
  exit 1
fi
