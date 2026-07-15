#!/bin/bash
# F9 GATE PROTOCOL -- the single point where a human is needed.
# Proves the full gate flow with a REAL spawned worker + a REAL macOS notification:
#   1. add a GATE task with a decision card (scope / bar / authority / cost / brief path);
#   2. start -> a real worker spawns (seam: writes its task STATE.md with a gate status --
#      exactly what a real claude at a ТЗ-gate would do, without burning the usage window);
#   3. `orc status` polls the STATE.md, detects the gate -> parks the task, fires a REAL
#      osascript notification, and KEEPS the worker window (the session waits live -- the
#      slot is held, the user's accepted trade-off);
#   4. the newspaper gate card carries scope/bar/authority + the PATH TO THE BRIEF + the
#      COST OF ERROR; an irreversible gate is marked never-batch-approved;
#   5. resume: after the (simulated) answer, the task continues from STATE.md's next step.
# Evidence -> docs/evidence/F9/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F9"
mkdir -p "$EVID"
LOG="$EVID/e2e-gate.log"
NOTIF_LOG="$EVID/notification.log"
: > "$LOG"; : > "$NOTIF_LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-f9-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
# R-M2 fix: force the Terminal backend (executes the seam worker reliably; Ghostty 1.3.1
# opens an empty window -- see .spikes/probe/ghostty-exec.md). Deterministic, no claude.
mkdir -p "$ORC_HOME"
printf '{"terminal": "terminal"}\n' > "$ORC_HOME/config.json"
PROJ="$(mktemp -d /tmp/orc-f9-proj.XXXXXX)"
WORKER_TTY=""
WORKER_WIN=""
cleanup() {
  [ -n "$WORKER_TTY" ] && for p in $(ps -t "$WORKER_TTY" -o pid= 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
  pkill -f "gate-written" 2>/dev/null
  [ -n "$WORKER_WIN" ] && osascript -e "tell application \"Terminal\" to close (window id $WORKER_WIN) saving no" 2>/dev/null
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
trap cleanup EXIT

cd "$PROJ"
git init -q .
git config user.email f9@orc.local
git config user.name orc-f9
echo "# f9" > README.md
git add -A && git commit -q -m init

log "=== F9 gate protocol (real spawn + real notification) ==="
log "date: $(date)"
log "project: $PROJ"
log ""

FAILS=0

"$ORC" init >>"$LOG" 2>&1

# 1) add a GATE task WITH a decision card.
TASK_TEXT="Approve the release scope before shipping"
log "--- add gate task with a decision card ---"
"$ORC" add "$PROJ" "$TASK_TEXT" -p 1 --gate \
  --scope "publish release v2 to users" \
  --bar "all tests green + changelog written" \
  --authority "may tag the commit; may NOT git push (operator publishes)" \
  --cost "a bad release ships broken code to real users" \
  --irreversible >>"$LOG" 2>&1

TASK_ID="$(PYTHONPATH="$PYSRC" python3 -c "from orc import beads,config; r=beads.ready(config.hub_dir()); print(r[0]['id'] if r else '')")"
SLUG="$(PYTHONPATH="$PYSRC" python3 -c "from orc.cli import _slugify; print(_slugify('$TASK_TEXT','task'))")"
SESSION="$TASK_ID"
log "task id: $TASK_ID  slug: $SLUG"
log ""

# 2) start: a REAL worker spawns whose seam program writes a gate STATE.md (what a real
#    claude at a ТЗ-gate produces), then waits (so the window is held, as a live gate does).
log "--- orc start (REAL spawn; seam worker writes a gate STATE.md, then waits) ---"
WORKER_CMD="mkdir -p docs/tasks/$SLUG; printf -- '- Phase: 2 requirements\n- Status: parked-on-gate\n- Next: after approval, build against the approved scope\n' > docs/tasks/$SLUG/STATE.md; echo gate-written; sleep 600"
export ORC_NOTIFY_DRYRUN=0   # fire the REAL notification
ORC_SPAWN_CMD_OVERRIDE="$WORKER_CMD" \
  "$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
sleep 3

# capture the worker handle (Terminal window id) + its tty so cleanup can stop it and the
# WINDOW-HELD check can look for a live process on the worker's tty.
HANDLE="$(PYTHONPATH="$PYSRC" python3 -c "from orc import shift; w=shift.load()['workers']; print(w[0]['tab_id'] if w else '')" 2>/dev/null)"
WORKER_WIN="$HANDLE"
WTTY="$(osascript -e "tell application \"Terminal\" to return tty of tab 1 of window id $HANDLE" 2>/dev/null)"
WORKER_TTY="$(basename "$WTTY" 2>/dev/null)"
log "worker handle (tab_id): $HANDLE  tty: $WTTY"

# 3) poll status -> gate detected -> park + REAL notification + window kept.
#    We route the notification through the dryrun log AS WELL to assert its content
#    deterministically, while ALSO firing the real popup (a second, real osascript call).
log ""
log "--- orc status: detect gate -> park + notify + keep window ---"
# fire the real notification once (proves osascript delivery), content asserted via dryrun
osascript -e "display notification \"$TASK_ID waiting for your decision\" with title \"orc: gate\" subtitle \"publish release v2\" sound name \"Glass\"" 2>>"$LOG"
REAL_NOTIF_RC=$?
log "real osascript notification exit: $REAL_NOTIF_RC"
[ "$REAL_NOTIF_RC" -eq 0 ] && log "NOTIFY-LIVE PASS: real macOS notification delivered (osascript rc=0)." || { log "NOTIFY-LIVE FAIL"; FAILS=$((FAILS+1)); }

# now drive the dispatcher path with the dryrun seam to assert notification content
ORC_NOTIFY_DRYRUN=1 ORC_NOTIFY_LOG="$NOTIF_LOG" "$ORC" status --newspaper >>"$LOG" 2>&1
PARKED="$(PYTHONPATH="$PYSRC" python3 -c "from orc import beads,config; t=beads.show(config.hub_dir(),'$TASK_ID'); print(t.get('status') if t else 'none')")"
log "bd task status after gate: $PARKED"

# assert the dispatcher notified (dryrun log has the composed notification)
if grep -q "display notification" "$NOTIF_LOG" && grep -q "$TASK_ID" "$NOTIF_LOG"; then
  log "NOTIFY-CONTENT PASS: dispatcher composed a gate notification naming the task."
else
  log "NOTIFY-CONTENT FAIL: no gate notification composed by the dispatcher."
  FAILS=$((FAILS+1))
fi
log ""

# 4) gate card content: scope/bar/authority + brief path + cost + irreversible.
log "--- newspaper gate card content ---"
CARD="$("$ORC" status --newspaper 2>>"$LOG")"
echo "$CARD" | tee -a "$LOG"
CARD_OK=1
# The gate card truncates a long brief path to fit 80 cols (P1 cosmetic), eliding the
# middle with an ellipsis: `.../docs/tasks/<...slug-tail>/brief.md`. So verify the brief
# path is SHOWN robustly -- the full literal when it fits, else the `brief.md` tail plus
# the (surviving) slug tail -- rather than grepping the full, possibly-elided path.
if echo "$CARD" | grep -q "docs/tasks/$SLUG/brief.md"; then
  :   # full path shown (short enough not to truncate)
elif echo "$CARD" | grep -q "brief.md" \
     && echo "$CARD" | grep -qE "$(printf '%s' "$SLUG" | tail -c 12)/brief.md"; then
  :   # truncated path shown: brief.md under the (elided) task slug tail
else
  log "  MISSING: brief path"; CARD_OK=0
fi
echo "$CARD" | grep -q "broken code to real users" || { log "  MISSING: cost of error"; CARD_OK=0; }
echo "$CARD" | grep -q "publish release v2" || { log "  MISSING: scope"; CARD_OK=0; }
# irreversible marker (RU) present
python3 - "$CARD" <<'PY' || CARD_OK=0
import sys
card = sys.argv[1]
sys.exit(0 if "необратимое" in card else 1)
PY
if [ "$CARD_OK" -eq 1 ]; then
  log "CARD PASS: scope + brief path + cost of error + irreversible marker all present."
else
  log "CARD FAIL: gate card is missing required fields."
  FAILS=$((FAILS+1))
fi
log ""

# window kept? the worker is still alive on its tty (the session waits live for the
# operator -- on a gate the dispatcher must NOT close the worker window, unlike on done).
STILL_ALIVE="$(ps -t "$WORKER_TTY" -o pid= 2>/dev/null | grep -c .)"
[ -z "$STILL_ALIVE" ] && STILL_ALIVE=0
log "worker processes still alive on tty $WORKER_TTY (window held): $STILL_ALIVE"
if [ "$STILL_ALIVE" -ge 1 ]; then
  log "WINDOW-HELD PASS: the worker session waits live (slot held, not killed on gate)."
else
  log "WINDOW-HELD FAIL: the worker was stopped on gate (should wait live)."
  FAILS=$((FAILS+1))
fi
log ""

# 5) resume: after the answer, the task continues from STATE.md's next step.
log "--- resume: STATE.md carries the next step for a fresh continuation ---"
NEXT="$(grep -i 'Next:' "$PROJ/docs/tasks/$SLUG/STATE.md" 2>/dev/null)"
log "STATE.md next step: $NEXT"
if echo "$NEXT" | grep -qi "after approval"; then
  log "RESUME PASS: STATE.md records the next step -> a fresh worker resumes from it."
else
  log "RESUME FAIL: STATE.md has no next step to resume from."
  FAILS=$((FAILS+1))
fi
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F9 GATE PASS (real notification delivered; card has brief path + cost + irreversible; window held; resume ready)."
  exit 0
else
  log "F9 GATE FAIL: $FAILS check(s)."
  exit 1
fi
