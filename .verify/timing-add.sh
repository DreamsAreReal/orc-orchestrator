#!/bin/bash
# F3 / G11 — batch add of 10 tasks must land in the ready queue in <=5 minutes.
#
# Measures wall-clock from "paste 10 prepared lines into `orc add --batch`" to
# "10 tasks confirmed ready". Uses an isolated ORC_HOME + two throwaway projects so
# the real queue is untouched. Evidence -> docs/evidence/F3/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
EVID="$ORC_ROOT/docs/evidence/F3"
mkdir -p "$EVID"
LOG="$EVID/timing-add.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-timing-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
P1="$(mktemp -d /tmp/orc-timing-p1.XXXXXX)"
P2="$(mktemp -d /tmp/orc-timing-p2.XXXXXX)"
cleanup() { rm -rf "$ORC_HOME" "$P1" "$P2" 2>/dev/null; }
trap cleanup EXIT

for P in "$P1" "$P2"; do
  ( cd "$P" && git init -q . && echo x > README.md \
    && git -c user.email=t@t -c user.name=t add -A \
    && git -c user.email=t@t -c user.name=t commit -q -m init )
done

log "=== F3 G11 batch-add timing ==="
log "date: $(date)"
"$ORC" init | tee -a "$LOG"

# Prepared batch text (the operator would paste this).
BATCH="$(mktemp /tmp/orc-timing-batch.XXXXXX)"
{
  for i in 1 2 3 4 5; do echo "$P1: build feature $i for project one"; done
  for i in 6 7 8 9 10; do echo "$P2: build feature $i for project two"; done
} > "$BATCH"
log "--- 10 prepared lines ---"
cat "$BATCH" | tee -a "$LOG"

START=$(date +%s)
OUT="$("$ORC" add --batch < "$BATCH")"
END=$(date +%s)
echo "$OUT" | tee -a "$LOG"
ELAPSED=$((END - START))

# Confirm 10 tasks are ready.
READY=$(ORC_HUB="$ORC_HOME" /usr/bin/python3 -c \
  "import sys; sys.path.insert(0,'$ORC_ROOT/src'); from orc import beads; print(len(beads.ready('$ORC_HOME')))")
rm -f "$BATCH"

log ""
log "elapsed: ${ELAPSED}s (threshold 300s)"
log "ready tasks: $READY (expected 10)"
if [ "$READY" -eq 10 ] && [ "$ELAPSED" -le 300 ]; then
  log "G11 PASS: 10 tasks in ready in ${ELAPSED}s (<= 5 min)."
  exit 0
else
  log "G11 FAIL: ready=$READY elapsed=${ELAPSED}s."
  exit 1
fi
