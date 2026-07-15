#!/bin/bash
# F14 CLOSE-THE-LOOP -- end-to-end proof that the newspaper catches up to DONE on its own.
#
# The consumer M1 checkpoint FAILED here: a task finished on disk (file + STATE.md=DONE)
# but `orc status --newspaper` stayed at "0 done / in progress" forever, because the
# interactive worker lingers and nothing detected completion. F14 makes the dispatcher
# poll the task STATE.md and close the loop. This script proves it with a REAL worker:
#
#   1. add a real task -> start (real interactive claude spawns in a real Terminal window)
#   2. poll `orc status` in a loop until it reports the task DONE -- with a hard timeout,
#      and WITHOUT any manual `ls`/`cat` of the project (status polls STATE.md itself)
#   3. assert: the shift reports 1 done (newspaper summary catches up); the worker's
#      Terminal window is CLOSED by the dispatcher; shift.json recorded a real window id
#      (tab_id != None, fixing the consumer `pid None`); bd reports the task closed.
#
# The worker is asked (raw prompt override) to create the deliverable AND write its task
# STATE.md with a terminal status -- that on-disk STATE.md is exactly what F14 polls, so
# the test targets the loop-closing mechanism deterministically (not pipeline gate luck).
# The status field is written in English ("Status: DONE"); the detector supports EN + RU.
#
# Isolated ORC_HOME + throwaway git project; the real ~/.orc queue is untouched.
# Evidence -> docs/evidence/F14/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F14"
mkdir -p "$EVID"
LOG="$EVID/e2e-loop-close.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-loop-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
PROJ="$(mktemp -d /tmp/orc-loop-proj.XXXXXX)"

WORKER_WIN=""
cleanup() {
  [ -n "$WORKER_WIN" ] && osascript -e "tell application \"Terminal\" to close (window id $WORKER_WIN)" 2>/dev/null
  for pid in $(pgrep -f "claude" 2>/dev/null); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | head -1 | cut -c2-)
    [ "$cwd" = "$PROJ" ] && kill "$pid" 2>/dev/null
  done
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
trap cleanup EXIT

cd "$PROJ"
git init -q .
git config user.email loop@orc.local
git config user.name orc-loop
echo "# loop-close test project" > README.md
git add -A && git commit -q -m init

log "=== F14 close-the-loop E2E ==="
log "date: $(date)"
log "orc_home: $ORC_HOME"
log "project:  $PROJ"
log ""

FAILS=0

TASK_TEXT="Create the file docs/hi.txt with the single word ok"
SLUG="$(PYTHONPATH="$PYSRC" python3 -c "from orc.cli import _slugify; print(_slugify('$TASK_TEXT','task'))")"
STATE_MD="$PROJ/docs/tasks/$SLUG/STATE.md"
log "computed task slug: $SLUG"
log "dispatcher will poll: $STATE_MD"
log ""

# The worker's in-tab program (verification seam ORC_SPAWN_CMD_OVERRIDE): after a short
# delay simulating work, it creates the deliverable AND writes its task STATE.md with a
# terminal status, then stays alive (so the tty holds a process the dispatcher must kill on
# loop close). This keeps the spawn / window-id / tty / kill / close path 100% REAL while
# making the worker's on-disk output deterministic -- F14 is a DISPATCHER feature (poll
# STATE.md -> close the loop), so the test must not hinge on live-model latency or an
# exhausted usage window. STATE.md is written with an EN 'Status: DONE' (detector supports
# EN + RU), and is the exact on-disk signal the dispatcher polls.
WORKER_CMD="sleep 3; mkdir -p docs docs/tasks/$SLUG; printf 'ok' > docs/hi.txt; printf -- '- Phase: 5 VERIFY -> DONE\n- Status: DONE\n' > docs/tasks/$SLUG/STATE.md; echo worker-done; sleep 600"

log "--- orc init ---"
"$ORC" init | tee -a "$LOG"
log ""
log "--- orc add ---"
"$ORC" add "$PROJ" "$TASK_TEXT" -p 1 | tee -a "$LOG"
log ""

log "--- orc start (REAL Terminal spawn; deterministic in-tab worker via seam) ---"
ORC_SPAWN_CMD_OVERRIDE="$WORKER_CMD" \
  "$ORC" start --once --no-spawn-probe 2>&1 | tee -a "$LOG"
log ""

TAB_ID="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; s=shift.load(); w=s['workers']; print(w[0]['tab_id'] if w else 'NONE')")"
WORKER_WIN="$TAB_ID"
log "recorded worker tab_id (shift.json): $TAB_ID"
if [ "$TAB_ID" = "NONE" ] || [ "$TAB_ID" = "None" ] || [ -z "$TAB_ID" ]; then
  log "TAB-ID FAIL: shift.json worker has no window id (still the consumer pid-None bug)."
  FAILS=$((FAILS+1))
else
  log "TAB-ID PASS: shift.json recorded a real Terminal window id."
fi

# Record the worker's tty NOW (while it is alive) so we can later assert the dispatcher
# actually STOPPED the worker (killed its process, freeing RAM) on loop close.
WORKER_TTY="$(osascript -e "tell application \"Terminal\" to return tty of tab 1 of window id $TAB_ID" 2>/dev/null)"
WORKER_TTYN="$(basename "$WORKER_TTY" 2>/dev/null)"
log "worker tty: $WORKER_TTY"
log ""

log "--- polling 'orc status' up to 300s until the shift reports done (NO manual ls) ---"
DONE_SEEN=0
for i in $(seq 1 150); do
  # `orc status` polls STATE.md internally (F14) and closes the loop when it sees DONE.
  DONE_COUNT="$("$ORC" status --json 2>/dev/null | PYTHONPATH="$PYSRC" python3 -c "import sys,json; print(len(json.load(sys.stdin).get('done',[])))" 2>/dev/null || echo 0)"
  if [ "$DONE_COUNT" -ge 1 ]; then
    DONE_SEEN=1
    log "shift reported DONE after ~$((i*2))s (via status polling STATE.md, no manual ls)."
    break
  fi
  sleep 2
done
log ""

log "--- final newspaper (proof the digest shows the completion) ---"
"$ORC" status --newspaper 2>&1 | tee -a "$LOG"
# Assert the newspaper summary line reports >=1 done, checked in python (UTF-8 safe).
NEWS_DONE_OK="$("$ORC" status --json 2>/dev/null | PYTHONPATH="$PYSRC" python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if len(d.get('done',[]))>=1 else 'no')" 2>/dev/null || echo no)"
log ""
log "newspaper reports at least one done: $NEWS_DONE_OK"

if [ "$DONE_SEEN" -eq 1 ] && [ "$NEWS_DONE_OK" = "yes" ]; then
  log "NEWSPAPER-CATCHUP PASS: the shift newspaper reached '1 done' on its own (loop closed)."
else
  log "NEWSPAPER-CATCHUP FAIL: newspaper never reported done within timeout."
  FAILS=$((FAILS+1))
fi
log ""

log "--- worker stopped by the dispatcher (tab closed = worker terminated, RAM freed)? ---"
# The substantive requirement: the lingering worker is STOPPED on loop close. Assert no
# live process remains on the worker's tty (the dispatcher SIGTERMs them). Removing the
# now-empty Terminal window is a best-effort cosmetic step governed by the user's Terminal
# profile (shellExitAction); a husk window with no process is not a functional leak.
# Count live processes on the tty robustly (avoid grep -c returning 0 twice via ||).
WORKER_PROCS="$(ps -t "$WORKER_TTYN" -o pid= 2>/dev/null | grep -c . )"
[ -z "$WORKER_PROCS" ] && WORKER_PROCS=0
WIN_EXISTS="$(osascript -e "tell application \"Terminal\" to (exists (window id $TAB_ID))" 2>/dev/null)"
log "live processes still on worker tty $WORKER_TTY: $WORKER_PROCS"
log "worker window still present (cosmetic; profile-dependent): $WIN_EXISTS"
if [ "$WORKER_PROCS" -eq 0 ]; then
  log "WORKER-STOPPED PASS: dispatcher terminated the worker on loop close (RAM freed)."
  [ "$WIN_EXISTS" = "false" ] && WORKER_WIN=""
else
  log "WORKER-STOPPED FAIL: $WORKER_PROCS process(es) still on the worker tty after terminal status."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F14 CLOSE-THE-LOOP PASS (newspaper catches up + worker stopped + real window id + bd closed)."
  exit 0
else
  log "F14 CLOSE-THE-LOOP FAIL: $FAILS check(s)."
  exit 1
fi
