#!/usr/bin/env bash
# SPIKE: does neutralizing git-push env make an OBFUSCATED `git push` fail by auth?
# We set up a repo with an HTTPS remote and try to push (a) with normal env (baseline:
# would attempt keychain auth), (b) with the orc worker's push-neutralizing env.
# The push command is reached via obfuscation (base64 | base64 -d | bash) so it also
# demonstrates the F1 pattern-hook is NOT what's stopping it -- the env is.
set -u
T=$(mktemp -d /tmp/orc-push-spike.XXXXXX)
cd "$T"
git init -q
git config user.email spike@x; git config user.name spike
echo hello > sentinel.txt
git add -A; git commit -qm init
# A remote we do NOT own; pushing requires GitHub auth. Never resolves to a real push
# because we have no write creds for it -- the point is HOW it fails (auth vs silent success).
git remote add origin https://github.com/orc-spike-nonexistent-9f3a/private-repo.git

echo "=== ENV combos under test ==="
# The worker's push-neutralizing env (candidate wall):
NEUTRAL=(
  GIT_TERMINAL_PROMPT=0          # never prompt for a password interactively
  GIT_ASKPASS=/usr/bin/false     # any askpass invocation returns failure, no password supplied
  GIT_CONFIG_NOSYSTEM=1          # ignore /etc/gitconfig helpers
  GIT_CONFIG_COUNT=1             # inject an inline config pair:
  GIT_CONFIG_KEY_0=credential.helper
  GIT_CONFIG_VALUE_0=            # empty -> disables osxkeychain helper for this process tree
)

run_push() { # $1=label ; rest=env assignments
  local label="$1"; shift
  echo "--- $label ---"
  # obfuscated push: 'git push -u origin HEAD' base64-encoded, decoded, piped to bash
  local payload; payload=$(printf 'git push -u origin HEAD' | base64)
  # run with a hard timeout so a credential prompt can't hang the spike
  ( env "$@" GIT_HTTP_LOW_SPEED_LIMIT=1 GIT_HTTP_LOW_SPEED_TIME=8 \
      bash -c "echo $payload | base64 -d | bash" ) >out.log 2>&1 &
  local pid=$!
  local waited=0
  while kill -0 $pid 2>/dev/null; do
    sleep 1; waited=$((waited+1))
    if [ $waited -ge 25 ]; then kill -9 $pid 2>/dev/null; echo "TIMEOUT (killed) -> treated as no clean push"; fi
  done
  wait $pid 2>/dev/null; local rc=$?
  echo "exit=$rc"
  echo "output:"; sed 's/^/  | /' out.log | head -12
  echo "verdict:"
  if [ $rc -eq 0 ] && grep -qiE 'main ->|new branch|writing objects|-> HEAD' out.log; then
    echo "  PUSH SUCCEEDED (WALL BREACHED)"
  else
    if grep -qiE 'Authentication failed|could not read Username|terminal prompts disabled|fatal: could not read|Invalid username or password|repository .* not found|remote: (Repository not found|Invalid)|fatal: unable to access|Permission denied' out.log; then
      echo "  BLOCKED by auth/no-cred (as intended)"
    else
      echo "  did not cleanly push; failure reason above"
    fi
  fi
  echo ""
}

run_push "NEUTRALIZED env (worker wall)" "${NEUTRAL[@]}"

echo "=== cleanup ==="
cd /; rm -rf "$T"; echo "removed $T"
