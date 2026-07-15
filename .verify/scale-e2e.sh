#!/usr/bin/env bash
# P2/3 -- SCALE E2E: prove the queue / serialization / newspaper AT VOLUME.
#
# Earlier E2Es (F12) proved 3 tasks / 2 projects one-by-one. This drives 8 tasks across
# 3 real git projects (2-3 per project, 2 gate tasks, mixed priorities) through the whole
# orc contour in ONE shift and PROVES, from shift.json + the log (external facts):
#   (1) SERIALIZATION: two tasks of the SAME project never run at once -- their
#       [started_epoch, ended_epoch] activity intervals do not overlap (project-mutex/F4/G3).
#   (2) GATE tasks spawn AFTER autonomous ones (pushed to the end of the queue, F4/ADR-0002).
#   (3) NEWSPAPER is correct at volume: "N done, M waiting, K failed; ~X tokens" -- the
#       numbers match the facts on disk.
#   (4) 0 DUPLICATES, 0 lost tasks: every task reaches a terminal status (done/parked-gate).
#   (5) REAL claude tasks produced non-empty artifacts (external fact); seam tasks ran too --
#       both kinds in the SAME shift.
#
# Window economy: MOST tasks run via the deterministic spawn seam (ORC_SPAWN_CMD_OVERRIDE)
# so the whole orc CONTOUR (spawn / window-id / tty / real PID / sandbox / detect / close /
# newspaper / serialize) stays 100% real while the in-tab program is deterministic; a small
# number run REAL claude (proving both live and seam coexist in one shift). The seam per task
# is chosen by a tiny dispatcher stub keyed to ORC_SESSION (the task id).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
ORC="$ROOT/bin/orc"
OUT="$ROOT/docs/evidence/scale-e2e"; mkdir -p "$OUT"
LOG="$OUT/scale-e2e.log"; : > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

# How many tasks run REAL claude (the rest run the deterministic seam). Default 1 to save the
# usage window; set REAL_TASKS=2 to prove two live claude workers. Both >=1 => real+seam mix.
REAL_TASKS="${REAL_TASKS:-1}"

# Isolated hub so the operator's real ~/.orc queue is never touched.
export ORC_HOME="$(mktemp -d /tmp/orc-scale-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
SCALE_DIR="$ORC_HOME/scale"; mkdir -p "$SCALE_DIR"
export ORC_SCALE_DIR="$SCALE_DIR"

P1="$HOME/Desktop/orc-scale-1"
P2="$HOME/Desktop/orc-scale-2"
P3="$HOME/Desktop/orc-scale-3"

cleanup() {
  "$ORC" stop >/dev/null 2>&1 || true
  rm -rf "$ORC_HOME"
}
trap cleanup EXIT

# Fresh, real git projects on the Desktop (git init each). Reset to the FIRST (init) commit
# if they already exist, discarding every orc commit from a previous run -- otherwise the
# seam's `git add -A && git commit` finds an identical, already-committed artifact, hits
# "nothing to commit", and (under `set -e`) never writes DONE. Resetting to init makes the
# test idempotent across repeated runs (the artifact is genuinely new each time).
mkproj() { # $1=path
  mkdir -p "$1"
  if [ ! -d "$1/.git" ]; then
    ( cd "$1" && git init -q && git config user.email orc@test && git config user.name orc \
        && git commit --allow-empty -qm "init" )
  else
    ( cd "$1" \
        && git config user.email orc@test && git config user.name orc \
        && root="$(git rev-list --max-parents=0 HEAD | tail -1)" \
        && git reset --hard -q "$root" \
        && git clean -fdxq )
  fi
}
mkproj "$P1"; mkproj "$P2"; mkproj "$P3"

# slugify mirror (cli._slugify): lowercase, non-alnum -> '-', first 6 words.
slug_of() { python3 -c "import re,sys; s=re.sub(r'[^a-z0-9]+','-',sys.argv[1].lower()).strip('-'); print('-'.join(s.split('-')[:6]) or 'task')" "$1"; }

log "=== P2/3 SCALE E2E (8 tasks / 3 projects / 2 gates / mixed priorities) ==="
log "hub (isolated): $ORC_HOME"
log "real claude tasks this run: $REAL_TASKS (the rest run the deterministic seam)"
log ""

"$ORC" init >>"$LOG" 2>&1

# ---- the 8 tasks: (project, text, priority, kind[auto|gate], filename, word) --------------
# proj1: 3 tasks (2 auto + 1 gate)   proj2: 3 tasks (2 auto + 1 gate)   proj3: 2 tasks (auto)
# Priorities are mixed on purpose; gate tasks carry a high priority (0) to PROVE they are
# still ordered LAST despite priority (gate-last beats priority, F4/ADR-0002).
add_task() { # $1=proj $2=text $3=prio $4=kind $5=file $6=word  -> echoes "id slug"
  local proj="$1" text="$2" prio="$3" kind="$4"
  local out slug id
  slug=$(slug_of "$text")
  if [ "$kind" = "gate" ]; then
    out=$("$ORC" add "$proj" "$text" -p "$prio" --gate --scope "release scope" \
            --bar "operator approves" --cost "wrong thing shipped" --json 2>>"$LOG")
  else
    out=$("$ORC" add "$proj" "$text" -p "$prio" --json 2>>"$LOG")
  fi
  id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$out")
  echo "$id $slug"
}

# Declarative task table. Fields: proj|text|prio|kind|file|word
TASKS=(
  "$P1|scale one alpha task|2|auto|ALPHA.txt|alpha"
  "$P1|scale one beta task|1|auto|BETA.txt|beta"
  "$P1|scale one gate review|0|gate|-|-"
  "$P2|scale two gamma task|3|auto|GAMMA.txt|gamma"
  "$P2|scale two delta task|2|auto|DELTA.txt|delta"
  "$P2|scale two gate signoff|0|gate|-|-"
  "$P3|scale three epsilon task|1|auto|EPSILON.txt|epsilon"
  "$P3|scale three zeta task|4|auto|ZETA.txt|zeta"
)

log "--- orc add x${#TASKS[@]} (6 autonomous + 2 gate; mixed priorities) ---"
declare -a IDS SLUGS PROJS FILES WORDS KINDS
i=0
for row in "${TASKS[@]}"; do
  IFS='|' read -r proj text prio kind file word <<<"$row"
  read -r id slug < <(add_task "$proj" "$text" "$prio" "$kind" "$file" "$word")
  IDS[$i]="$id"; SLUGS[$i]="$slug"; PROJS[$i]="$proj"
  FILES[$i]="$file"; WORDS[$i]="$word"; KINDS[$i]="$kind"
  log "  added [$id] p=$prio $kind  slug=$slug  ($(basename "$proj"))"
  i=$((i+1))
done
NTASK=$i

# ---- per-task seam command files, keyed by task id (ORC_SESSION) --------------------------
# Autonomous seam: create a NON-EMPTY artifact, git-commit it (the commit is the external
# fact that gates DONE, B1), then write the task STATE.md terminal status = DONE.
# Gate seam: write the task STATE.md gate status and PARK (no work) -- exercises F9.
# REAL claude: the cmd file execs the real claude binary with a self-contained prompt file,
# so the spawn/tty/PID/sandbox/close path is identical; only THIS worker is a live model.
# Which autonomous tasks are "real": the first REAL_TASKS autonomous tasks (by add order).
CLAUDE_BIN="$(PYTHONPATH="$ROOT/src" python3 -c "from orc import config; print(config.load()['claude_bin'])")"
real_left="$REAL_TASKS"
declare -a IS_REAL
for j in $(seq 0 $((NTASK-1))); do
  id="${IDS[$j]}"; slug="${SLUGS[$j]}"; kind="${KINDS[$j]}"
  file="${FILES[$j]}"; word="${WORDS[$j]}"
  cmd="$SCALE_DIR/cmd-$id.sh"
  IS_REAL[$j]=0
  if [ "$kind" = "gate" ]; then
    cat > "$cmd" <<EOF
#!/usr/bin/env bash
set -e
mkdir -p "docs/tasks/$slug"
cat > "docs/tasks/$slug/STATE.md" <<'STATE'
# STATE
## Status
Status: waiting on gate
## Recap
Parked on a gate: needs operator approval before any work.
STATE
# stay alive on the tty until the dispatcher parks the gate + reaps this worker (F9).
sleep 600
EOF
  elif [ "$kind" = "auto" ] && [ "$real_left" -gt 0 ]; then
    # REAL claude worker: a tiny self-contained task that commits a non-empty artifact.
    IS_REAL[$j]=1
    real_left=$((real_left-1))
    PROMPT_FILE="$SCALE_DIR/prompt-$id.txt"
    cat > "$PROMPT_FILE" <<EOF
Do this tiny task, then stop. The working directory is this git project.
Step 1: create a file named $file whose only content is the single word: $word
Step 2: run exactly: git add -A && git commit -m "orc: add $file"
Step 3: ONLY IF step 2 succeeded, create docs/tasks/$slug/STATE.md with exactly:
# STATE
## Status
Status: DONE
## Recap
Added $file containing $word and committed it.
Do not ask any questions. Do not touch any other files. Stop after step 3.
EOF
    cat > "$cmd" <<EOF
#!/usr/bin/env bash
exec $CLAUDE_BIN "\$(cat $PROMPT_FILE)"
EOF
  else
    # deterministic seam autonomous worker.
    cat > "$cmd" <<EOF
#!/usr/bin/env bash
set -e
printf '%s' "$word" > "$file"
git add -A && git commit -qm "orc: add $file"
mkdir -p "docs/tasks/$slug"
cat > "docs/tasks/$slug/STATE.md" <<'STATE'
# STATE
## Status
Status: DONE
## Recap
Added $file containing $word and committed it (seam).
STATE
# stay alive on the tty until the dispatcher detects DONE+fact and reaps this worker.
sleep 600
EOF
  fi
  chmod +x "$cmd"
  [ "${IS_REAL[$j]}" = 1 ] && log "  task $id ($slug) -> REAL claude" || true
done

# The global spawn seam: dispatch to the per-task cmd file keyed by ORC_SESSION (task id).
# The cmd does its work (commit + STATE.md) then SLEEPS to stay alive on its tty until the
# dispatcher detects completion (STATE.md=DONE + external fact) and kills it via close_worker
# -- exactly like a real claude worker that finishes and waits at its prompt while the
# dispatcher reaps it. Staying alive (rather than exiting immediately) avoids racing
# reconcile's dead-worker lease against the completion poll (that race requeues an
# already-committed task and then wedges it, because its commit now predates the re-spawn).
# NB: the worker shell is launched by osascript `do script` and does NOT inherit this shell's
# env, so SCALE_DIR must be baked in as an ABSOLUTE path now; ONLY $ORC_SESSION stays a
# runtime variable (spawn.py exports it into the worker's inner command).
export ORC_SPAWN_CMD_OVERRIDE="exec bash '$SCALE_DIR/cmd-'\"\$ORC_SESSION\"'.sh'"

log ""
log "queued (order_ready view -- gate tasks must be LAST despite priority):"
PYTHONPATH="$ROOT/src" python3 - "$ORC_HUB" <<'PY' | tee -a "$LOG"
import sys
from orc import beads, dispatcher
hub = sys.argv[1]
ordered = dispatcher.order_ready(beads.ready(hub))
for t in ordered:
    meta = beads.task_meta(t)
    gate = "gate" in (t.get("labels") or []) or bool(meta.get("gate"))
    print("  %-10s p=%s %-4s %s" % (t.get("id"), t.get("priority"),
          "GATE" if gate else "auto", meta.get("slug")))
PY

# F6 real spend baseline (ccusage before the shift).
TOKENS_BEFORE=$(PYTHONPATH="$ROOT/src" python3 -c "from orc import probes; print(probes.total_tokens_now() or 0)")
log ""
log "--- running the dispatcher loop (serial, max_workers=1) ---"
DEADLINE=$(( $(date +%s) + 900 ))   # 15 min hard ceiling
TICK=0
GATE_SEEN=()   # plain indexed array (macOS bash 3.2 has no associative arrays)
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  TICK=$((TICK+1))
  ORC_DAEMON_ONCE=1 "$ORC" daemon >>"$LOG" 2>&1
  # answer each gate once (single human touch), just log it -- parked-on-gate IS terminal
  # for the shift.
  DONE_CNT=0; GATE_CNT=0
  for j in $(seq 0 $((NTASK-1))); do
    slug="${SLUGS[$j]}"; proj="${PROJS[$j]}"; kind="${KINDS[$j]}"
    st="$proj/docs/tasks/$slug/STATE.md"
    if [ "$kind" = "gate" ]; then
      if [ -f "$st" ] && grep -qi "waiting on gate" "$st"; then
        GATE_CNT=$((GATE_CNT+1))
        if [ -z "${GATE_SEEN[$j]:-}" ]; then
          log "  [tick $TICK] gate reached: ${SLUGS[$j]} -> operator answers it"
          GATE_SEEN[$j]=1
        fi
      fi
    else
      [ -f "$st" ] && grep -qi "Status: DONE" "$st" && DONE_CNT=$((DONE_CNT+1))
    fi
  done
  log "  [tick $TICK] auto-done=$DONE_CNT/6  gate-parked=$GATE_CNT/2"
  if [ "$DONE_CNT" = 6 ] && [ "$GATE_CNT" = 2 ]; then
    log "  all 8 tasks reached a terminal signal"; break
  fi
  sleep 5
done

# final poll so the newspaper catches up (bd close, stop workers, close windows).
ORC_DAEMON_ONCE=1 "$ORC" daemon >>"$LOG" 2>&1
"$ORC" stop >/dev/null 2>&1 || true
sleep 2

# snapshot shift.json for the interval/serialization proof.
cp "$ORC_HOME/shift.json" "$OUT/shift.json" 2>/dev/null || true

log ""
log "=== VERDICT (external facts on disk) ==="
FAIL=0

# (5) real + seam both ran: real artifacts + seam artifacts exist and are NON-EMPTY.
log ""
log "--- (5) real claude + seam artifacts (all non-empty; both kinds in one shift) ---"
REAL_OK=0; SEAM_OK=0
for j in $(seq 0 $((NTASK-1))); do
  [ "${KINDS[$j]}" = "gate" ] && continue
  proj="${PROJS[$j]}"; file="${FILES[$j]}"; word="${WORDS[$j]}"
  got="$(cat "$proj/$file" 2>/dev/null || true)"
  tag="seam"; [ "${IS_REAL[$j]}" = 1 ] && tag="REAL"
  if [ -s "$proj/$file" ] && [ "$got" = "$word" ]; then
    log "  PASS [$tag] $(basename "$proj")/$file = '$got' (non-empty)"
    [ "$tag" = "REAL" ] && REAL_OK=$((REAL_OK+1)) || SEAM_OK=$((SEAM_OK+1))
  else
    log "  FAIL [$tag] $(basename "$proj")/$file missing/empty/wrong (got '$got')"; FAIL=1
  fi
done
log "  real artifacts ok=$REAL_OK (expected $REAL_TASKS); seam artifacts ok=$SEAM_OK"
[ "$REAL_OK" -ge "$REAL_TASKS" ] || { log "  FAIL: not all REAL claude tasks produced artifacts"; FAIL=1; }
[ "$SEAM_OK" -ge 1 ] || { log "  FAIL: no seam artifacts (need real+seam in one shift)"; FAIL=1; }

# git commits back the artifacts (external fact, not a worker claim).
log ""
log "--- committed artifacts (git log) ---"
for P in "$P1" "$P2" "$P3"; do
  log "  $(basename "$P"): $(git -C "$P" log --oneline | grep -c 'orc: add') orc commit(s)"
  git -C "$P" log --oneline | grep 'orc: add' | sed 's/^/    /' | tee -a "$LOG" >/dev/null
done

# (4) 0 duplicates, 0 lost: every task terminal; done ids unique; parked = the 2 gates.
log ""
log "--- (4) 0 duplicates / 0 lost: every task reached a terminal status ---"
PYTHONPATH="$ROOT/src" python3 - "$OUT/shift.json" "$NTASK" <<'PY' | tee -a "$LOG"
import json, sys
shift = json.load(open(sys.argv[1]))
ntask = int(sys.argv[2])
done = shift.get("done", [])
parked = shift.get("parked", [])
failed = shift.get("failed", [])
done_ids = [d["task"] for d in done]
parked_ids = [p["task"] for p in parked]
# duplicates within done, and any task in both done and parked?
dup_done = len(done_ids) != len(set(done_ids))
overlap = set(done_ids) & set(parked_ids)
terminal = set(done_ids) | set(parked_ids) | {f["task"] for f in failed}
print("  done=%d parked=%d failed=%d  (terminal total=%d, expected %d)"
      % (len(done), len(parked), len(failed), len(terminal), ntask))
print("  duplicate done ids: %s" % ("YES -- FAIL" if dup_done else "none"))
print("  done&parked overlap: %s" % ("YES -- FAIL" if overlap else "none"))
lost = ntask - len(terminal)
print("  lost tasks (never reached terminal): %d" % lost)
ok = (not dup_done) and (not overlap) and (len(terminal) == ntask)
sys.exit(0 if ok else 3)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || { log "  FAIL: duplicate/lost/overlap detected"; FAIL=1; }

# (1) SERIALIZATION: same-project activity intervals never overlap (project-mutex/F4/G3).
log ""
log "--- (1) SERIALIZATION: same-project [started,ended] intervals never overlap ---"
PYTHONPATH="$ROOT/src" python3 - "$OUT/shift.json" <<'PY' | tee -a "$LOG"
import json, sys, os
shift = json.load(open(sys.argv[1]))
# every terminal record carries the worker interval (shift.py mark_*).
recs = []
for kind in ("done", "parked", "failed"):
    for r in shift.get(kind, []):
        recs.append(r)
# group by project? shift records don't carry project; but per-task slug -> we grouped by
# the fact each record kept started/ended. Recover project via the worker snapshot is gone,
# so we prove the STRONGER global property on a 1-worker machine: NO TWO tasks (any project)
# overlap in activity -- which implies per-project non-overlap. max_workers=1 => strictly serial.
iv = []
for r in recs:
    s = r.get("started_epoch"); e = r.get("ended_epoch")
    if s is None or e is None:
        continue
    iv.append((s, e, r.get("task")))
iv.sort()
overlaps = []
for a in range(len(iv)):
    for b in range(a+1, len(iv)):
        s1,e1,t1 = iv[a]; s2,e2,t2 = iv[b]
        # overlap if one starts strictly before the other ends AND after the other starts.
        if s2 < e1 and s1 < e2:
            overlaps.append((t1,t2,round(min(e1,e2)-max(s1,s2),2)))
print("  activity intervals recorded: %d" % len(iv))
for s,e,t in iv:
    print("    %-12s [%.1f -> %.1f]  (%.1fs active)" % (t, s, e, e-s))
if overlaps:
    print("  OVERLAPS FOUND (serialization BROKEN):")
    for t1,t2,d in overlaps:
        print("    %s <> %s overlap %ss" % (t1,t2,d))
    sys.exit(4)
print("  NO overlapping intervals -> workers ran strictly serial (project-mutex holds).")
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || { log "  FAIL: overlapping activity intervals"; FAIL=1; }

# Mutex refusals in the daemon log: on a max_workers=1 machine the spawn loop stops after
# the first worker each tick, so it rarely even ATTEMPTS a same-project sibling in the same
# tick -> this count is often 0 and that is EXPECTED (the interval proof above is the
# authoritative one). We report it for information, not as a pass condition.
MUTEX=$(grep -c "project busy (mutex)" "$LOG" 2>/dev/null | tr -d '[:space:]'); MUTEX=${MUTEX:-0}
log "  (info) 'project busy (mutex)' daemon refusals in the log = $MUTEX (0 is normal at max_workers=1)"

# DIRECT project-mutex probe: force the busy condition (a same-project worker already in
# state) and assert spawn_one REFUSES the sibling with 'project busy (mutex)'. This proves
# the mutex gate itself fires (the daemon loop just rarely reaches it at 1 worker).
log "  direct mutex probe (force a same-project sibling; must be refused):"
PYTHONPATH="$ROOT/src" python3 - "$ORC_HUB" "$P1" <<'PY' | tee -a "$LOG"
import sys
from orc import dispatcher, shift as shiftmod, beads
hub, proj = sys.argv[1], sys.argv[2]
cfg = {"claude_bin": "/bin/true", "sandbox": False, "max_workers": 1}
state = shiftmod._empty()
# pretend a worker for this project is already active
shiftmod.add_worker(state, pid=99999, session="busy", project=proj, task="busy-task")
fake = {"id": "probe-task", "title": "probe",
        "metadata": {"project": proj, "slug": "probe", "text": "probe"}}
ok, detail, _ = dispatcher.spawn_one(cfg, hub, state, fake)
print("    spawn_one on a busy project -> ok=%s detail=%r" % (ok, detail))
import sys as _s
_s.exit(0 if (not ok and "mutex" in detail) else 7)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || { log "  FAIL: project-mutex did not refuse a same-project sibling"; FAIL=1; }

# (2) GATE tasks spawned AFTER autonomous ones. Prove from interval order: every gate's
# start is at/after the LAST autonomous task's start.
log ""
log "--- (2) gate tasks spawn AFTER autonomous (end of queue) ---"
PYTHONPATH="$ROOT/src" python3 - "$OUT/shift.json" "$ORC_HUB" <<'PY' | tee -a "$LOG"
import json, sys
from orc import beads
shift = json.load(open(sys.argv[1])); hub = sys.argv[2]
def is_gate(task_id):
    t = beads.show(hub, task_id) or {}
    meta = beads.task_meta(t)
    return "gate" in (t.get("labels") or []) or bool(meta.get("gate"))
starts = {}
for kind in ("done","parked","failed"):
    for r in shift.get(kind, []):
        se = r.get("started_epoch")
        if se is not None:
            starts[r["task"]] = se
auto = {k:v for k,v in starts.items() if not is_gate(k)}
gate = {k:v for k,v in starts.items() if is_gate(k)}
last_auto = max(auto.values()) if auto else 0
print("  autonomous tasks: %d   gate tasks: %d" % (len(auto), len(gate)))
print("  last autonomous start epoch: %.1f" % last_auto)
bad = [k for k,v in gate.items() if v < last_auto]
for k,v in sorted(gate.items(), key=lambda x:x[1]):
    print("    gate %-12s start=%.1f  (>= last auto? %s)" % (k, v, v >= last_auto))
if bad:
    print("  FAIL: a gate started before the last autonomous task:", bad)
    sys.exit(5)
print("  All gate tasks started AFTER every autonomous task -> gate-last ordering holds.")
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || { log "  FAIL: a gate ran before an autonomous task"; FAIL=1; }

# (3) NEWSPAPER correct at volume: parse the digest, compare N/M/K to the facts. The
# newspaper is the product's Russian digest; we match its summary line via the product's own
# string constant (no hard-coded prose in this script) and check the numbers add up.
log ""
log "--- (3) newspaper is correct at volume ---"
"$ORC" status --newspaper 2>&1 | tee -a "$LOG"
# extract the summary line and its 3 numbers using the product's RU_REPORT_SUMMARY template.
"$ORC" status --newspaper 2>/dev/null > "$OUT/newspaper.txt"
PYTHONPATH="$ROOT/src" python3 - "$OUT/shift.json" "$OUT/newspaper.txt" <<'PY' | tee -a "$LOG"
import json, sys, re
from orc import strings as S
shift = json.load(open(sys.argv[1]))
text = open(sys.argv[2], encoding="utf-8").read()
# facts on disk
nd = len(shift.get("done", []))
nw = len(shift.get("parked", []))          # parked gate tasks = "waiting on you"
nf = len(shift.get("failed", []))
print("  facts: done=%d waiting=%d failed=%d" % (nd, nw, nf))
# build a regex from the product's own summary template so no prose is hard-coded here.
tmpl = S.RU_REPORT_SUMMARY  # "...: {done} ..., {waiting} ..., {failed} ...{spend}"
pat = re.escape(tmpl)
for name in ("done", "waiting", "failed"):
    pat = pat.replace(re.escape("{" + name + "}"), r"(?P<%s>\d+)" % name)
pat = pat.replace(re.escape("{spend}"), r".*")
m = re.search(pat, text)
if not m:
    print("  FAIL: summary line not found via product template"); sys.exit(6)
sd, sw, sf = int(m.group("done")), int(m.group("waiting")), int(m.group("failed"))
print("  summary line says: done=%d waiting=%d failed=%d" % (sd, sw, sf))
ok = (nd == sd == 6 and nw == sw == 2 and nf == sf == 0)
print("  newspaper numbers match facts and expected 6/2/0: %s"
      % ("YES" if ok else "NO -- FAIL"))
sys.exit(0 if ok else 6)
PY
[ "${PIPESTATUS[0]}" -eq 0 ] || { log "  FAIL: newspaper facts do not match"; FAIL=1; }

# F6 real spend delta (shows real claude burned; seam adds ~0).
TOKENS_AFTER=$(PYTHONPATH="$ROOT/src" python3 -c "from orc import probes; print(probes.total_tokens_now() or 0)")
DELTA=$(( TOKENS_AFTER - TOKENS_BEFORE ))
log ""
log "--- F6 real work-driven spend delta (ccusage) ---"
log "  ccusage total_tokens: before=$TOKENS_BEFORE after=$TOKENS_AFTER  delta=$DELTA"

log ""
if [ "$FAIL" -eq 0 ]; then
  log "=== P2/3 SCALE E2E PASS (8 tasks / 3 projects; serialization holds; gates last; newspaper matches; 0 dup/lost; real+seam in one shift) ==="
  exit 0
else
  log "=== P2/3 SCALE E2E FAIL ==="
  exit 1
fi
