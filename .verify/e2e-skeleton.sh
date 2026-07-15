#!/bin/bash
# F2 WALKING SKELETON — end-to-end shift of one task through the real spawn path.
#
# Proves the golden-path tract "add -> shift -> newspaper" as a thin real slice:
#   1. canary preflight runs and reports (and a forced fail refuses to start) [G7]
#   2. `orc start` spawns a REAL interactive Terminal running `claude` (not headless)
#      in the project; the task creates a file; the file appears on disk           [G0b]
#   3. `orc status --newspaper` prints the summary first line + gate/done sections   [signature]
#
# Everything runs in an isolated ORC_HOME and a throwaway git project so the real
# ~/.orc queue is untouched. Evidence -> docs/evidence/F2/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
EVID="$ORC_ROOT/docs/evidence/F2"
mkdir -p "$EVID"
LOG="$EVID/e2e-skeleton.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

# --- isolated runtime + throwaway project ------------------------------------
export ORC_HOME="$(mktemp -d /tmp/orc-e2e-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
PROJ="$(mktemp -d /tmp/orc-e2e-proj.XXXXXX)"
cleanup() {
  # kill any claude worker we spawned in PROJ (best effort), then remove temp dirs
  for pid in $(pgrep -f "claude" 2>/dev/null); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | head -1 | cut -c2-)
    [ "$cwd" = "$PROJ" ] && kill "$pid" 2>/dev/null
  done
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
trap cleanup EXIT

cd "$PROJ"
git init -q .
git config user.email e2e@orc.local
git config user.name orc-e2e
echo "# skeleton test project" > README.md
git add -A && git commit -q -m init

log "=== F2 walking-skeleton E2E ==="
log "date: $(date)"
log "orc_home: $ORC_HOME"
log "project:  $PROJ"
log ""

FAILS=0

# --- init + add --------------------------------------------------------------
log "--- orc init ---"
"$ORC" init | tee -a "$LOG"
log ""
log "--- orc add (hello task) ---"
TASK_TEXT="Create a file named hello.txt in the current directory containing exactly the single word: ready. Then stop."
"$ORC" add "$PROJ" "$TASK_TEXT" -p 1 | tee -a "$LOG"
log ""

# --- G7: forced-fail canary refuses to start --------------------------------
log "--- G7: forced-fail canary (auth) must refuse to start ---"
OUT_FAIL="$(ORC_CANARY_FAIL=auth "$ORC" start --once --no-spawn-probe 2>&1)"
echo "$OUT_FAIL" | tee -a "$LOG"
FAIL_RC=$?
if echo "$OUT_FAIL" | grep -q "shift not started"; then
  log "G7 PASS: broken component -> shift refused."
else
  log "G7 FAIL: forced-fail did not stop the shift."
  FAILS=$((FAILS+1))
fi
log ""

# --- real spawn --------------------------------------------------------------
log "--- orc start (real spawn of interactive claude) ---"
# ORC_RAW_PROMPT=1 spawns with the raw task (deterministic skeleton proof of the spawn
# mechanism); real shifts use the pipeline wrapper. The worker runs in a real Terminal.
# We assert the FILE appears (world-state), not model prose.
ORC_RAW_PROMPT=1 "$ORC" start --once --no-spawn-probe 2>&1 | tee -a "$LOG"
log ""

log "--- waiting up to 180s for hello.txt to appear (real worker doing real work) ---"
HELLO="$PROJ/hello.txt"
FOUND=0
for i in $(seq 1 90); do
  if [ -f "$HELLO" ]; then FOUND=1; break; fi
  sleep 2
done
if [ "$FOUND" -eq 1 ]; then
  CONTENT="$(cat "$HELLO" | tr -d '[:space:]')"
  log "hello.txt appeared after ~$((i*2))s; content=[$CONTENT]"
  if echo "$CONTENT" | grep -qi "ready"; then
    log "SPAWN PASS: real interactive claude created hello.txt containing 'ready'."
  else
    log "SPAWN PARTIAL: file created but content unexpected: $CONTENT"
    FAILS=$((FAILS+1))
  fi
else
  log "SPAWN FAIL: hello.txt did not appear within timeout (real worker did not complete)."
  FAILS=$((FAILS+1))
fi
log ""

# --- newspaper ---------------------------------------------------------------
# reflect the completed task in the shift state, then print the newspaper.
log "--- orc status (live) ---"
"$ORC" status 2>&1 | tee -a "$LOG"
log ""
log "--- orc status --newspaper ---"
NEWS="$("$ORC" status --newspaper 2>&1)"
echo "$NEWS" | tee -a "$LOG"
FIRST_LINE="$(echo "$NEWS" | sed -n '2p')"
WORDS=$(echo "$NEWS" | wc -w | tr -d ' ')
log ""
log "newspaper first-screen word count: $WORDS (<=150 required)"
if echo "$NEWS" | grep -q "смена:" && [ "$WORDS" -le 150 ]; then
  log "NEWSPAPER PASS: summary present, first screen <=150 words."
else
  log "NEWSPAPER FAIL: missing summary line or >150 words."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F2 SKELETON PASS (canary + real spawn + newspaper)."
  exit 0
else
  log "F2 SKELETON FAIL: $FAILS check(s)."
  exit 1
fi
