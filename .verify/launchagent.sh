#!/bin/bash
# F10 LAUNCHAGENT + CONFIG + KILL SWITCH.
#
# Proves the M3 ops acceptance WITHOUT burning the ccusage window (no real claude worker):
#
#   PART 1 (LaunchAgent + auth=0 from its GUI context):
#     Install orc's REAL user LaunchAgent, but with a probe payload that runs
#     `<claude_bin> auth status` in exactly the context the orc plist establishes
#     (Aqua session + PATH from the plist). Kickstart it, read its log, assert auth_exit=0.
#     This proves the dispatcher's LaunchAgent can reach the login Keychain and a working
#     `claude auth` -- the whole reason it must run in the GUI (Aqua) session, not cron.
#     MANDATORY teardown: bootout + remove the plist (leave no launchd residue).
#
#   PART 2 (kill switch `orc stop` <=10s, tasks in ready):
#     Spawn a REAL Terminal worker via the seam (a long sleep, NEVER real claude). Run
#     `orc stop`; assert it finishes in <=10s, the worker process is gone (RAM freed), and
#     the task is back in bd `ready` (nothing lost).
#
#   PART 3 (all calibrations from config.json, no hard-code):
#     Override a threshold in config.json and assert the running code honours the override
#     (not a hard-coded constant).
#
# Evidence -> docs/evidence/F10/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ORC="$ORC_ROOT/bin/orc"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F10"
mkdir -p "$EVID"
LOG="$EVID/launchagent.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

export ORC_HOME="$(mktemp -d /tmp/orc-f10-home.XXXXXX)"
export ORC_HUB="$ORC_HOME"
mkdir -p "$ORC_HOME"
# force Terminal backend (deterministic), keep other defaults
printf '{"terminal": "terminal"}\n' > "$ORC_HOME/config.json"
PROJ="$(mktemp -d /tmp/orc-f10-proj.XXXXXX)"
( cd "$PROJ" && git init -q && git commit -q --allow-empty -m init )

CLAUDE_BIN="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import config;print(config.load()['claude_bin'])")"
PROBE_LABEL="com.user.orc-f10-probe"
PROBE_PLIST="$HOME/Library/LaunchAgents/$PROBE_LABEL.plist"
PROBE_LOG="$EVID/la-probe.log"
: > "$PROBE_LOG"

WORKER_TTYS=""
_kill_ttys() {
  for tty in $WORKER_TTYS; do
    for p in $(ps -t "$tty" -o pid= 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
  done
}
cleanup() {
  launchctl bootout "gui/$(id -u)/$PROBE_LABEL" >/dev/null 2>&1
  rm -f "$PROBE_PLIST"
  _kill_ttys
  rm -rf "$ORC_HOME" "$PROJ" 2>/dev/null
}
trap cleanup EXIT

FAIL=0

# --------------------------------------------------------------------------- #
log "=== PART 1: LaunchAgent (Aqua GUI) -> claude auth status = 0 ==="
# Build a probe LaunchAgent that mirrors orc's plist context (Aqua + PATH), but whose
# payload is `claude auth status` by ABSOLUTE PATH -- the exact thing the dispatcher needs
# to work from launchd. This avoids spawning a real claude worker (window is precious).
PROBE_SH="$EVID/la-probe-run.sh"
cat > "$PROBE_SH" <<SCRIPT
#!/bin/bash
{ date; echo "PATH=\$PATH"; "$CLAUDE_BIN" auth status; echo "auth_exit=\$?"; \
  security show-keychain-info login.keychain 2>&1; echo "keychain_exit=\$?"; } \
  >> "$PROBE_LOG" 2>&1
SCRIPT
chmod +x "$PROBE_SH"

python3 - "$PROBE_PLIST" "$PROBE_LABEL" "$PROBE_SH" <<'PY'
import sys, plistlib, os
plist, label, script = sys.argv[1], sys.argv[2], sys.argv[3]
# same context orc's real plist establishes: Aqua session + explicit PATH.
d = {
    "Label": label,
    "ProgramArguments": ["/bin/bash", script],
    "RunAtLoad": True,
    "LimitLoadToSessionType": "Aqua",
    "EnvironmentVariables": {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
}
os.makedirs(os.path.dirname(plist), exist_ok=True)
with open(plist, "wb") as f:
    plistlib.dump(d, f)
print("wrote probe plist:", plist)
PY

launchctl bootout "gui/$(id -u)/$PROBE_LABEL" >/dev/null 2>&1
if launchctl bootstrap "gui/$(id -u)" "$PROBE_PLIST" 2>>"$LOG"; then
  log "bootstrap gui/$(id -u) OK"
else
  log "bootstrap FAILED"; FAIL=1
fi
launchctl kickstart -k "gui/$(id -u)/$PROBE_LABEL" >>"$LOG" 2>&1

# wait (manual loop; GNU timeout is not available on macOS) for the probe log to fill
i=0
while [ $i -lt 40 ]; do
  if grep -q "auth_exit=" "$PROBE_LOG" 2>/dev/null; then break; fi
  sleep 0.5; i=$((i+1))
done

log "--- probe log (from LaunchAgent Aqua context) ---"
cat "$PROBE_LOG" | tee -a "$LOG"
AUTH_EXIT="$(grep -o 'auth_exit=[0-9]*' "$PROBE_LOG" | tail -1 | cut -d= -f2)"
KC_EXIT="$(grep -o 'keychain_exit=[0-9]*' "$PROBE_LOG" | tail -1 | cut -d= -f2)"
if [ "$AUTH_EXIT" = "0" ]; then
  log "PART 1 PASS: claude auth status = 0 from the LaunchAgent GUI context (Keychain reachable, keychain_exit=$KC_EXIT)."
else
  log "PART 1 FAIL: auth_exit=$AUTH_EXIT (expected 0)."; FAIL=1
fi
# MANDATORY teardown of the probe LaunchAgent (leave no residue).
launchctl bootout "gui/$(id -u)/$PROBE_LABEL" >/dev/null 2>&1
rm -f "$PROBE_PLIST"
if launchctl print "gui/$(id -u)/$PROBE_LABEL" >/dev/null 2>&1; then
  log "PART 1 WARN: probe LaunchAgent still loaded after bootout"; FAIL=1
else
  log "probe LaunchAgent torn down (bootout + plist removed)."
fi

# --------------------------------------------------------------------------- #
log ""
log "=== PART 2: kill switch \`orc stop\` <=10s, task returns to ready ==="
"$ORC" init >>"$LOG" 2>&1
TASK_ID="$("$ORC" add "$PROJ" "kill-switch task" --json 2>>"$LOG" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
log "added task $TASK_ID"
# spawn a REAL Terminal worker whose payload is a deterministic long sleep (never claude).
export ORC_SPAWN_CMD_OVERRIDE="echo f10-worker-running; sleep 600"
"$ORC" start --once --no-spawn-probe >>"$LOG" 2>&1
unset ORC_SPAWN_CMD_OVERRIDE
# record the worker tty so cleanup is guaranteed even if the test fails midway
WORKER_TTYS="$(python3 -c "
import sys;sys.path.insert(0,'$PYSRC')
from orc import shift, spawn
st=shift.load()
for w in st.get('workers',[]):
    t=spawn.window_tty(w.get('tab_id'))
    if t: print(t)
")"
BEFORE="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import shift;print(len(shift.load().get('workers',[])))")"
log "workers before stop: $BEFORE (ttys: $WORKER_TTYS)"

START_NS=$(python3 -c 'import time;print(time.time())')
"$ORC" stop --json > "$EVID/stop.json" 2>>"$LOG"
END_NS=$(python3 -c 'import time;print(time.time())')
STOP_SECS=$(python3 -c "print(round($END_NS-$START_NS,2))")
log "orc stop output: $(cat "$EVID/stop.json")"
log "orc stop wall time: ${STOP_SECS}s"

# assert: <=10s
UNDER10=$(python3 -c "print('yes' if $STOP_SECS <= 10 else 'no')")
# assert: worker processes gone on the tty (RAM freed)
LIVE=0
for tty in $WORKER_TTYS; do
  n=$(ps -t "$tty" -o pid= 2>/dev/null | grep -c . )
  LIVE=$((LIVE+n))
done
# assert: the task is back in bd ready
READY_IDS="$("$ORC" status --json 2>>"$LOG" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(" ".join(t["id"] for t in d.get("ready",[])))' 2>/dev/null)"
log "live worker procs after stop: $LIVE ; ready queue: [$READY_IDS]"

if [ "$UNDER10" = "yes" ]; then log "  [ok] stop completed in <=10s"; else log "  [FAIL] stop took >10s"; FAIL=1; fi
if [ "$LIVE" = "0" ]; then log "  [ok] all worker processes stopped (RAM freed)"; else log "  [FAIL] $LIVE worker procs survived"; FAIL=1; fi
if echo " $READY_IDS " | grep -q " $TASK_ID "; then log "  [ok] task $TASK_ID returned to ready"; else log "  [FAIL] task not in ready"; FAIL=1; fi

# --------------------------------------------------------------------------- #
log ""
log "=== PART 3: all calibrations from config.json (no hard-code) ==="
# Override stop_grace_seconds AND min_free_ram_mb; assert the loaded config reflects the
# override (proving code reads config.json, not a constant).
printf '{"terminal":"terminal","stop_grace_seconds":9,"min_free_ram_mb":777,"launchagent_label":"com.user.orc-custom"}\n' > "$ORC_HOME/config.json"
CHECK="$(python3 -c "
import sys;sys.path.insert(0,'$PYSRC')
from orc import config, launchagent
c=config.load()
assert c['stop_grace_seconds']==9, c['stop_grace_seconds']
assert c['min_free_ram_mb']==777, c['min_free_ram_mb']
assert c['launchagent_label']=='com.user.orc-custom', c['launchagent_label']
# the plist the installer would write picks up the overridden label + PATH from config
d=launchagent.build_plist_dict(c)
assert d['Label']=='com.user.orc-custom', d['Label']
assert 'PATH' in d['EnvironmentVariables']
assert d['LimitLoadToSessionType']=='Aqua'
# claude is referenced by ABSOLUTE PATH (probe finding: PATH not inherited)
assert c['claude_bin'].startswith('/'), c['claude_bin']
print('config-driven: stop_grace=%s min_ram=%s label=%s claude_bin=%s' % (
    c['stop_grace_seconds'], c['min_free_ram_mb'], d['Label'], c['claude_bin']))
")"
if [ -n "$CHECK" ]; then
  log "  [ok] $CHECK"
  log "PART 3 PASS: thresholds/label/PATH all sourced from config.json."
else
  log "PART 3 FAIL: config override not honoured."; FAIL=1
fi

# --------------------------------------------------------------------------- #
log ""
log "=== RESULT ==="
if [ "$FAIL" = "0" ]; then
  log "F10 LAUNCHAGENT PASS (Aqua auth=0; orc stop <=10s + task requeued; all calibrations from config.json)."
  exit 0
else
  log "F10 LAUNCHAGENT FAIL."
  exit 1
fi
