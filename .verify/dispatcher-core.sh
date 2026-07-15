#!/bin/bash
# F4 — dispatcher core live checks: serialization (G3), dirty-tree park, gate ordering.
#
# Uses the real orc CLI + real beads queue in an isolated home, but points claude at a
# fake binary (a script that just records it was called) so no real Terminal is opened
# and the run is deterministic. Asserts on world-state + queue, not model prose.
#
# 1. G3 serialization: two ready tasks for the SAME project -> only ONE spawns; the
#    second is refused by the project-mutex (intervals cannot overlap).
# 2. dirty tree that is not ours -> the task is parked with a "human may be mid-edit"
#    reason and is NOT claimed.
# 3. gate task is ordered AFTER autonomous tasks.
# Evidence -> docs/evidence/F4/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
PY=/usr/bin/python3
EVID="$ORC_ROOT/docs/evidence/F4"
mkdir -p "$EVID"
LOG="$EVID/dispatcher-core.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-f4-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
FAKE_CLAUDE="$(mktemp /tmp/orc-f4-claude.XXXXXX)"
cat > "$FAKE_CLAUDE" <<'FAKE'
#!/bin/bash
echo "fake-claude called: $*" >> "$FAKE_CLAUDE_LOG"
FAKE
chmod +x "$FAKE_CLAUDE"
export FAKE_CLAUDE_LOG="$(mktemp /tmp/orc-f4-claudelog.XXXXXX)"

P1="$(mktemp -d /tmp/orc-f4-p1.XXXXXX)"
P2="$(mktemp -d /tmp/orc-f4-p2.XXXXXX)"
cleanup() { rm -rf "$ORC_HOME" "$P1" "$P2" "$FAKE_CLAUDE" "$FAKE_CLAUDE_LOG" 2>/dev/null; }
trap cleanup EXIT

for P in "$P1" "$P2"; do
  ( cd "$P" && git init -q . && echo x > README.md \
    && git -c user.email=t@t -c user.name=t add -A \
    && git -c user.email=t@t -c user.name=t commit -q -m init )
done

log "=== F4 dispatcher-core live checks ==="
log "date: $(date)"
"$ORC" init | tee -a "$LOG"

# point config at the fake claude bin (no real terminal)
mkdir -p "$ORC_HOME"
cat > "$ORC_HOME/config.json" <<CFG
{ "claude_bin": "$FAKE_CLAUDE", "max_workers": 1, "min_free_ram_mb": 400 }
CFG

FAILS=0

# --- check 3 first: gate ordering (pure, no spawn) ---------------------------
log ""
log "--- gate ordering: gate task must sort AFTER autonomous ---"
"$ORC" add "$P1" "autonomous alpha" -p 2 >/dev/null
"$ORC" add "$P1" "autonomous beta"  -p 1 >/dev/null
"$ORC" add "$P2" "approve the spec" --gate -p 0 >/dev/null
ORDER="$($PY -c "import sys; sys.path.insert(0,'$ORC_ROOT/src'); \
from orc import beads, dispatcher; \
o=dispatcher.order_ready(beads.ready('$ORC_HOME')); \
print(' '.join(('GATE' if ('gate' in (t.get('labels') or [])) else 'AUTO') for t in o))")"
log "order (by kind): $ORDER"
if [ "${ORDER##* }" = "GATE" ] && [ "${ORDER%% *}" = "AUTO" ]; then
  log "GATE-ORDER PASS: autonomous first, gate last."
else
  log "GATE-ORDER FAIL: $ORDER"
  FAILS=$((FAILS+1))
fi

# --- check 1: serialization / project-mutex ----------------------------------
# Two tasks for the SAME project (P1). Start with max_workers=1 forces one at a time,
# but even beyond the worker cap the project-mutex must refuse a second P1 task.
log ""
log "--- serialization (G3): two same-project tasks, mutex refuses the second ---"
# raise the worker cap to 2 so ONLY the mutex (not the cap) can stop the second
$PY - "$ORC_HOME" "$FAKE_CLAUDE" "$P1" <<'PYEOF' 2>&1 | tee -a "$LOG"
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname("__file__"), "src"))
sys.path.insert(0, "/Users/admin/orchestrator/src")
from orc import beads, dispatcher, shift as shiftmod
home, fake, p1 = sys.argv[1], sys.argv[2], sys.argv[3]
os.environ["ORC_RAW_PROMPT"] = "1"
cfg = {"claude_bin": fake, "mcp_allowlist": [], "max_workers": 2, "min_free_ram_mb": 400}
# two same-project tasks
ta = beads.create(home, "same-proj task A", priority=1, labels=["orc"],
                  metadata={"project": p1, "slug": "a", "text": "A"})
tb = beads.create(home, "same-proj task B", priority=1, labels=["orc"],
                  metadata={"project": p1, "slug": "b", "text": "B"})
state = shiftmod._empty()
tasks = dispatcher.order_ready(beads.ready(home))
same = [t for t in tasks if beads.task_meta(t).get("project") == p1]
results = []
for t in same:
    ok, detail, state = dispatcher.spawn_one(cfg, home, state, t)
    results.append((t.get("id"), ok, detail))
spawned = [r for r in results if r[1]]
refused = [r for r in results if not r[1]]
print("spawned:", len(spawned), "refused:", len(refused))
print("refuse reasons:", [r[2] for r in refused])
ok = len(spawned) == 1 and any("mutex" in r[2] for r in refused)
print("SERIALIZE", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 3)
PYEOF
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  log "SERIALIZE FAIL"
  FAILS=$((FAILS+1))
else
  log "SERIALIZE PASS: exactly one worker for the project; second refused by mutex."
fi

# --- check 2: dirty tree -> park ---------------------------------------------
log ""
log "--- dirty tree (not ours) -> task parked, not claimed ---"
echo "half-written by a human" > "$P2/human_wip.py"   # foreign uncommitted edit
$PY - "$ORC_HOME" "$FAKE_CLAUDE" "$P2" <<'PYEOF' 2>&1 | tee -a "$LOG"
import sys, os
sys.path.insert(0, "/Users/admin/orchestrator/src")
from orc import beads, dispatcher, shift as shiftmod
home, fake, p2 = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = {"claude_bin": fake, "mcp_allowlist": [], "max_workers": 1, "min_free_ram_mb": 400}
tid = beads.create(home, "task into dirty proj", priority=1, labels=["orc"],
                   metadata={"project": p2, "slug": "d", "text": "D"})
task = beads.show(home, tid)
state = shiftmod._empty()
ok, reason, state = dispatcher.spawn_one(cfg, home, state, task)
print("ok:", ok, "reason:", reason)
parked = any(p["task"] == tid for p in state["parked"])
after = beads.show(home, tid)
status = after.get("status") if after else "?"
print("parked:", parked, "bd status:", status)
good = (not ok) and parked and ("dirty" in (reason or "")) and status != "in_progress"
print("DIRTY-PARK", "PASS" if good else "FAIL")
sys.exit(0 if good else 3)
PYEOF
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  log "DIRTY-PARK FAIL"
  FAILS=$((FAILS+1))
else
  log "DIRTY-PARK PASS: dirty-not-ours tree parked the task; not claimed."
fi

log ""
log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F4 DISPATCHER-CORE PASS (serialization + dirty-park + gate-order)."
  exit 0
else
  log "F4 DISPATCHER-CORE FAIL: $FAILS check(s)."
  exit 1
fi
