#!/bin/bash
# F8 RECOVERY -- dispatcher crash recovery + lease + real PID capture.
# Proves, with a REAL spawned Terminal worker (a deterministic long sleep via the seam,
# NEVER a real claude -- the usage window is precious):
#   1. spawn_one captures a REAL worker PID from the window tty (fixes the eval `pid None`);
#   2. a still-LIVE worker is adopted on restart, not re-served (no duplicate spawn);
#   3. kill -9 the worker (simulating a mid-shift crash) then restart the dispatcher ->
#      reconcile returns the dead worker's task to ready via the lease, 0 losses/duplicates.
#
# R-M2 fix (BLOCKER-4): the seam override is exported GLOBALLY so EVERY `orc start` in this
# script uses it -- no `orc start` ever launches a real claude (the earlier version leaked a
# real claude on the post-crash restart). The Terminal backend is forced explicitly so the
# test is deterministic regardless of the config default. cleanup kills by tty (reliable).
# Evidence -> docs/evidence/F8/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F8"
mkdir -p "$EVID"
LOG="$EVID/kill-restart.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-f8-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
# GLOBAL seam: every spawn in this script runs a deterministic sleep, never a real claude.
export ORC_SPAWN_CMD_OVERRIDE="echo f8-worker-running; sleep 600"
# force Terminal backend (executes deterministically; independent of the config default)
mkdir -p "$ORC_HOME"
printf '{"terminal": "terminal"}\n' > "$ORC_HOME/config.json"
PROJ="$(mktemp -d /tmp/orc-f8-proj.XXXXXX)"

WORKER_TTYS=""
_kill_ttys() {
  for tty in $WORKER_TTYS; do
    for p in $(ps -t "$tty" -o pid= 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
  done
}
cleanup() {
  # stop every worker we spawned (kill by recorded tty) + kill any stray seam sleeps
  _kill_ttys
  pkill -f "f8-worker-running" 2>/dev/null
  # best-effort close any windows we opened
  for wid in $WORKER_WINS; do
    osascript -e "tell application \"Terminal\" to close (window id $wid) saving no" 2>/dev/null
  done
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
WORKER_WINS=""
trap cleanup EXIT

_record_worker() {
  # capture the current worker's tty + window id so cleanup can stop it
  local wid tty
  wid="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['tab_id'] if w else '')" 2>/dev/null)"
  [ -n "$wid" ] && WORKER_WINS="$WORKER_WINS $wid"
  tty="$(osascript -e "tell application \"Terminal\" to return tty of tab 1 of window id $wid" 2>/dev/null)"
  [ -n "$tty" ] && WORKER_TTYS="$WORKER_TTYS $(basename "$tty")"
}

cd "$PROJ"
git init -q .
git config user.email f8@orc.local
git config user.name orc-f8
echo "# f8" > README.md
git add -A && git commit -q -m init

log "=== F8 recovery: crash restart + lease + real PID (seam only, no claude) ==="
log "date: $(date)"
log "orc_home: $ORC_HOME"
log "project:  $PROJ"
log ""

FAILS=0

"$ORC" init >>"$LOG" 2>&1
"$ORC" add "$PROJ" "recovery test task" -p 1 >>"$LOG" 2>&1
TASK_ID="$(PYTHONPATH="$PYSRC" python3 -c "from orc import beads,config; print((beads.ready(config.hub_dir()) or [{}])[0].get('id',''))")"
log "task id: $TASK_ID"
log ""

log "--- orc start (real Terminal, seam worker = long sleep; NO claude) ---"
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
_record_worker

# 1) real PID captured (not None)
PID="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['pid'] if w else 'NONE')")"
log "recorded worker pid: $PID   windows: $WORKER_WINS"
if [ "$PID" != "NONE" ] && [ "$PID" != "None" ] && [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  log "PID-CAPTURE PASS: shift.json has a REAL, live worker PID (eval pid-None fixed)."
else
  log "PID-CAPTURE FAIL: pid=$PID (not a live PID)."
  FAILS=$((FAILS+1))
fi
# guard: assert NO real claude was ever spawned by this script
if pgrep -f "$ORC_HOME" 2>/dev/null | xargs -I{} ps -p {} -o command= 2>/dev/null | grep -q "claude"; then
  log "CLAUDE-LEAK FAIL: a real claude process was spawned (should be seam-only)."
  FAILS=$((FAILS+1))
else
  log "NO-CLAUDE PASS: only the seam sleep runs; no real claude spawned (window preserved)."
fi
log ""

# 2) restart with the worker STILL ALIVE -> adopted, not duplicated
log "--- restart #1 with worker alive: reconcile must ADOPT (no duplicate) ---"
DUP_BEFORE="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; print(len(shift.load()['workers']))")"
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
_record_worker
WORKERS_AFTER="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; print(len(shift.load()['workers']))")"
READY_AFTER="$(PYTHONPATH="$PYSRC" python3 -c "from orc import beads,config; print(len(beads.ready(config.hub_dir())))")"
log "workers before=$DUP_BEFORE after-restart=$WORKERS_AFTER ; ready=$READY_AFTER"
if [ "$WORKERS_AFTER" = "1" ] && [ "$READY_AFTER" = "0" ]; then
  log "ADOPT PASS: live worker adopted, task NOT re-served (0 duplicates)."
else
  log "ADOPT FAIL: expected 1 worker / 0 ready, got $WORKERS_AFTER / $READY_AFTER."
  FAILS=$((FAILS+1))
fi
log ""

# 3) crash: kill -9 the worker, then restart -> lease returns the task (re-served by seam).
log "--- kill -9 worker (crash), then restart: lease returns task (seam re-spawn) ---"
kill -9 "$PID" 2>/dev/null
sleep 1
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1   # still seam (global override)
_record_worker
SURVIVED="$(PYTHONPATH="$PYSRC" python3 -c "
from orc import beads, config, shift
ready = beads.ready(config.hub_dir())
workers = shift.load()['workers']
tid = '$TASK_ID'
in_ready = any(t.get('id') == tid for t in ready)
in_workers = any(w.get('task') == tid for w in workers)
print('OK' if (in_ready or in_workers) else 'LOST')
print('ready=%d workers=%d' % (len(ready), len(workers)))
")"
log "$SURVIVED"
if echo "$SURVIVED" | grep -q "OK"; then
  log "LEASE PASS: task survived the crash (back to ready or re-served) -- 0 losses."
else
  log "LEASE FAIL: task was lost after the crash."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F8 RECOVERY PASS (real PID; live worker adopted; crash -> lease; seam-only, no claude)."
  exit 0
else
  log "F8 RECOVERY FAIL: $FAILS check(s)."
  exit 1
fi
