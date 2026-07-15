#!/bin/bash
# F13 OS-SANDBOX WALLS -- prove macOS seatbelt blocks the OBFUSCATED escapes that the F1
# pattern-hook cannot catch, at the OS level, using orc's OWN profile generator.
#
# The F1 deny-wall matches the Bash command string; obfuscation defeats it (the dangerous
# action never appears literally). This test builds orc's real seatbelt profile via
# orc.sandbox.write_profile(<workspace>) and runs each escape through
# orc.sandbox.wrap_command(...) -> `sandbox-exec -f <profile> bash -lc '<escape>'`. A
# sentinel lives in $HOME (OUTSIDE the workspace); the wall holds iff the sentinel survives.
#
# No real claude worker is spawned (the ccusage window is precious): the sandbox-exec
# wrapper + a probe action is the EXACT enforcement path a sandboxed worker's Bash tool
# takes. Spike: .spikes/probe/sandbox.md. Evidence -> docs/evidence/F13/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYSRC="$ORC_ROOT/src"
EVID="$ORC_ROOT/docs/evidence/F13"
mkdir -p "$EVID"
LOG="$EVID/sandbox-walls.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

# workspace the worker MAY write to; sentinel dir OUTSIDE it (a place a worker must never touch)
WS="$(mktemp -d /tmp/orc-f13-ws.XXXXXX)"
OUTDIR="$HOME/orc-f13-probe-out"
mkdir -p "$OUTDIR"
SENTINEL="$OUTDIR/sentinel.txt"

cleanup() { rm -rf "$WS" "$OUTDIR" 2>/dev/null; }
trap cleanup EXIT

if [ ! -x /usr/bin/sandbox-exec ]; then
  log "F13 SANDBOX FAIL: /usr/bin/sandbox-exec (seatbelt) not available on this machine."
  exit 1
fi

# Generate orc's REAL per-workspace seatbelt profile (the same call spawn._maybe_sandbox
# makes for a live worker).
PROFILE="$(python3 -c "
import sys;sys.path.insert(0,'$PYSRC')
from orc import sandbox
print(sandbox.write_profile('$WS'))
")"
log "generated profile: $PROFILE"
log "--- profile contents ---"
cat "$PROFILE" | tee -a "$LOG"
log ""

# run_escape <label> <shell-command> : run the command under orc's sandbox wrapper.
run_escape() {
  local label="$1"; local inner="$2"
  # wrap via orc.sandbox.wrap_command so we exercise the SHIPPED wrapper, not an ad-hoc one.
  local wrapped
  wrapped="$(python3 -c "
import sys;sys.path.insert(0,'$PYSRC')
from orc import sandbox
print(sandbox.wrap_command('$PROFILE', sys.argv[1]))
" "$inner")"
  log "=== $label ==="
  log "  inner : $inner"
  bash -c "$wrapped" >>"$LOG" 2>&1
}

FAIL=0
assert_survived() {
  local label="$1"
  if [ -f "$SENTINEL" ]; then
    log "  RESULT: sentinel SURVIVED -> $label BLOCKED by OS-sandbox [ok]"
  else
    log "  RESULT: sentinel DELETED -> $label LEAKED THROUGH (OS wall failed) [FAIL]"
    FAIL=1
    echo "ALIVE" > "$SENTINEL"   # restore for the next case
  fi
}

# --- Escape A: direct rm outside workspace -----------------------------------
echo "ALIVE" > "$SENTINEL"
run_escape "A direct rm outside ws" "rm -f '$SENTINEL'"
assert_survived "direct rm"

# --- Escape B: base64 | bash rm (obfuscated -- F1 hook cannot see the rm) -----
echo "ALIVE" > "$SENTINEL"
B64="$(printf "rm -f '%s'" "$SENTINEL" | base64)"
run_escape "B base64|bash rm outside ws" "echo '$B64' | base64 -d | bash"
assert_survived "base64|bash rm"

# --- Escape C: python shutil.rmtree outside workspace ------------------------
echo "ALIVE" > "$SENTINEL"
run_escape "C python shutil.rmtree outside ws" \
  "python3 -c \"import shutil; shutil.rmtree('$OUTDIR', ignore_errors=True)\""
assert_survived "python shutil.rmtree"

# --- Escape D: find outside ws -delete ---------------------------------------
echo "ALIVE" > "$SENTINEL"
run_escape "D find outside ws -delete" "find '$OUTDIR' -name 'sentinel.txt' -delete"
assert_survived "find -delete"

# --- Escape E: xargs rm (rm arrives via a pipe) ------------------------------
echo "ALIVE" > "$SENTINEL"
run_escape "E xargs rm outside ws" "echo '$SENTINEL' | xargs rm -f"
assert_survived "xargs rm"

# --- Escape F: write (not delete) outside ws, e.g. tamper ~/.ssh -------------
SSH_PROBE="$HOME/.ssh/orc_f13_probe"
run_escape "F write to ~/.ssh outside ws" "echo pwned > '$SSH_PROBE'"
if [ -f "$SSH_PROBE" ]; then
  log "  RESULT: wrote to ~/.ssh -> WRITE LEAKED [FAIL]"; FAIL=1; rm -f "$SSH_PROBE"
else
  log "  RESULT: ~/.ssh write BLOCKED by OS-sandbox [ok]"
fi

# --- Control: a legitimate write INSIDE the workspace must still work ---------
run_escape "control write inside ws" "echo ok > '$WS/inside.txt'"
if [ -f "$WS/inside.txt" ] && [ "$(cat "$WS/inside.txt")" = "ok" ]; then
  log "  RESULT: write inside workspace works [ok] (sandbox is not a brick wall)"
else
  log "  RESULT: could not write inside workspace -> profile too strict [FAIL]"; FAIL=1
fi

log ""
log "=== RESULT ==="
if [ "$FAIL" = "0" ]; then
  log "F13 SANDBOX PASS: obfuscated escapes (rm / base64|bash / python rmtree / find -delete / xargs rm) BLOCKED at the OS level; ~/.ssh write blocked; workspace write works. OS-sandbox is the primary wall over the F1 pattern-hook."
  exit 0
else
  log "F13 SANDBOX FAIL: at least one escape leaked through the OS wall."
  exit 1
fi
