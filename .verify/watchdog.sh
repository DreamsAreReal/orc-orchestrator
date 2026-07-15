#!/bin/bash
# F7 WATCHDOG -- loop / silence detection, the false-kill guard, and bounded recovery.
# All synthetic (no real claude): we forge heartbeat logs and an in-flight marker, exactly
# the wire artifacts the worker's hooks would write, and drive the watchdog against them.
#
# Acceptance proven here:
#   1. synthetic LOOP  (K identical hashes) is detected;
#   2. synthetic SILENCE (quiet past threshold, no in-flight marker) is detected;
#   3. CONTROL: a live long-running tool (in-flight marker held for > the silence
#      threshold) is classified OK, NOT silence -> 0 false kills;
#   4. recovery is bounded: kill only after an external check finds no progress; the
#      restart cap escalates instead of retrying forever.
# Evidence -> docs/evidence/F7/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F7"
mkdir -p "$EVID"
LOG="$EVID/watchdog.log"
: > "$LOG"

# isolated hub so the real ~/.orc heartbeat dir is untouched
export ORC_HOME="$(mktemp -d /tmp/orc-wd-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
cleanup() { rm -rf "$ORC_HOME" 2>/dev/null; }
trap cleanup EXIT

{
  echo "=== F7 watchdog: loop / silence / false-kill guard / bounded recovery ==="
  echo "date: $(date)"
  echo "orc_home: $ORC_HOME"
  echo ""
} | tee "$LOG"

PYTHONPATH="$PYSRC" python3 "$ORC_ROOT/.verify/_watchdog_check.py" 2>&1 | tee -a "$LOG"
OUT="$(cat "$LOG")"

FAILS=1
WB="$(echo "$OUT" | grep -o 'WD_FAILS=[0-9]*' | cut -d= -f2)"
[ -n "$WB" ] && FAILS="$WB"

{
  echo ""
  echo "=== RESULT ==="
  if [ "$FAILS" -eq 0 ]; then
    echo "F7 WATCHDOG PASS (loop + silence detected; long tool spared = 0 false kills; restart bounded, cap escalates)."
  else
    echo "F7 WATCHDOG FAIL: $FAILS check(s)."
  fi
} | tee -a "$LOG"

[ "$FAILS" -eq 0 ] && exit 0 || exit 1
