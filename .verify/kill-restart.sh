#!/bin/bash
# F8 RECOVERY -- dispatcher crash recovery + lease + real PID capture.
# Proves, with a REAL spawned Terminal worker (a deterministic long sleep via the seam,
# NOT a real claude -- the usage window is precious):
#   1. spawn_one captures a REAL worker PID from the window tty (fixes the eval `pid None`);
#   2. kill -9 the worker (simulating a mid-shift crash) then restart the dispatcher
#      (`orc start`) -> reconcile returns the dead worker's task to ready via the lease,
#      with 0 duplicates and 0 lost tasks;
#   3. a still-LIVE worker is adopted, not re-served (no duplicate spawn).
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
PROJ="$(mktemp -d /tmp/orc-f8-proj.XXXXXX)"
WORKER_WIN=""
cleanup() {
  # stop any worker we spawned + close its window
  if [ -n "$WORKER_WIN" ]; then
    TTY=$(osascript -e "tell application \"Terminal\" to return tty of tab 1 of window id $WORKER_WIN" 2>/dev/null)
    [ -n "$TTY" ] && for p in $(ps -t "$(basename "$TTY")" -o pid= 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
    osascript -e "tell application \"Terminal\" to close (window id $WORKER_WIN) saving no" 2>/dev/null
  fi
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
trap cleanup EXIT

cd "$PROJ"
git init -q .
git config user.email f8@orc.local
git config user.name orc-f8
echo "# f8" > README.md
git add -A && git commit -q -m init

log "=== F8 recovery: crash restart + lease + real PID ==="
log "date: $(date)"
log "orc_home: $ORC_HOME"
log "project:  $PROJ"
log ""

FAILS=0

"$ORC" init >>"$LOG" 2>&1
"$ORC" add "$PROJ" "recovery test task" -p 1 >>"$LOG" 2>&1
TASK_ID="$(PYTHONPATH="$PYSRC" python3 -c "import os; from orc import beads,config; print((beads.ready(config.hub_dir()) or [{}])[0].get('id',''))")"
log "task id: $TASK_ID"

# spawn a REAL worker whose in-tab program is a long sleep (a real killable process on a
# real tty), via the verification seam -- no claude, no window burn.
log ""
log "--- orc start (real Terminal, seam worker = long sleep) ---"
ORC_SPAWN_CMD_OVERRIDE="echo f8-worker-running; sleep 600" \
  "$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1

# 1) real PID captured (not None)
PID="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['pid'] if w else 'NONE')")"
WIN="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['tab_id'] if w else 'NONE')")"
WORKER_WIN="$WIN"
log "recorded worker pid: $PID   window: $WIN"
if [ "$PID" != "NONE" ] && [ "$PID" != "None" ] && [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  log "PID-CAPTURE PASS: shift.json has a REAL, live worker PID (eval pid-None fixed)."
else
  log "PID-CAPTURE FAIL: pid=$PID (not a live PID)."
  FAILS=$((FAILS+1))
fi
log ""

# 3) restart with the worker STILL ALIVE -> adopted, not duplicated
log "--- restart #1 with worker alive: reconcile must ADOPT (no duplicate) ---"
DUP_BEFORE="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; print(len(shift.load()['workers']))")"
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
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

# 2) simulate a crash: kill -9 the worker, then restart -> lease returns task to ready
log "--- kill -9 worker (crash), then restart: lease returns task to ready ---"
kill -9 "$PID" 2>/dev/null
sleep 1
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
# after reconcile the dead worker is dropped; its task is back to ready (or re-spawned).
DROPPED_OR_RESERVED="$(PYTHONPATH="$PYSRC" python3 -c "
from orc import beads, config, shift
ready = beads.ready(config.hub_dir())
workers = shift.load()['workers']
# the task must not be lost: it is either back in ready OR picked up by a fresh worker
tid = '$TASK_ID'
in_ready = any(t.get('id')==tid for t in ready)
in_workers = any(w.get('task')==tid for w in workers)
print('OK' if (in_ready or in_workers) else 'LOST')
print('ready=%d workers=%d' % (len(ready), len(workers)))
")"
log "$DROPPED_OR_RESERVED"
# grab the new window id if a fresh worker spawned, so cleanup stops it too
NEWWIN="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['tab_id'] if w else '')" 2>/dev/null)"
[ -n "$NEWWIN" ] && WORKER_WIN="$NEWWIN"
if echo "$DROPPED_OR_RESERVED" | grep -q "OK"; then
  log "LEASE PASS: task survived the crash (back to ready or re-served) -- 0 losses."
else
  log "LEASE FAIL: task was lost after the crash."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F8 RECOVERY PASS (real PID captured; live worker adopted; crash -> lease, 0 loss/dup)."
  exit 0
else
  log "F8 RECOVERY FAIL: $FAILS check(s)."
  exit 1
fi
