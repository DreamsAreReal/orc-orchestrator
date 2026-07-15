#!/usr/bin/env bash
# REVERIFY B2 (SSH / exfiltration) — independent, NOT builder's harness.
# Uses the REAL product-generated seatbelt profile + the REAL env-prefix, and attacks:
#   (a) read ~/.ssh key: direct + obfuscated (python open) + ssh-keygen -y
#   (b) SSH auth to github under the env-prefix (ssh -T) — key really usable?
#   (c) alt channels: scp / rsync -e ssh / GIT_SSH_COMMAND override inside the command
# WALL HELD = key not readable AND ssh cannot authenticate.
set -u
cd "$(dirname "$0")/../.." || exit 2
ROOT="$(pwd)"; export PYTHONPATH="$ROOT/src"

WS="$(mktemp -d /tmp/e3b2.XXXXXX)"; trap 'rm -rf "$WS"' EXIT

# generate the REAL product seatbelt profile (default deny_network=False, as shipped)
PROFILE="$(python3 - "$WS" <<'PY'
import sys
from orc import sandbox
print(sandbox.write_profile(sys.argv[1]))
PY
)"
echo "profile: $PROFILE"
echo "--- ssh-deny line in profile ---"; grep -i "ssh" "$PROFILE" || echo "(NO ssh deny line!)"
echo

# the REAL env-prefix the spawn path prepends
PREFIX="$(python3 - <<'PY'
from orc import worker_walls
print(worker_walls.push_neutralizing_export_prefix())
PY
)"
echo "--- env prefix ---"; echo "$PREFIX"; echo

# pick a real private key if one exists
KEY=""
for k in ~/.ssh/id_ed25519 ~/.ssh/id_rsa ~/.ssh/id_ecdsa; do
  [ -f "$k" ] && KEY="$k" && break
done
echo "target key: ${KEY:-<none found>}"; echo

sbx () { sandbox-exec -f "$PROFILE" bash -lc "$1"; }

echo "=== A. read ~/.ssh under sandbox (all should be BLOCKED) ==="
if [ -n "$KEY" ]; then
  echo -n "[direct cat]      "; sbx "cat '$KEY' >/dev/null 2>&1 && echo LEAK || echo blocked"
  echo -n "[python open]     "; sbx "python3 -c \"open('$KEY').read()\" >/dev/null 2>&1 && echo LEAK || echo blocked"
  echo -n "[b64 obfusc]      "; sbx "$(printf 'cat %q|base64' "$KEY" | base64) 2>/dev/null | base64 -d 2>/dev/null | bash 2>/dev/null && echo LEAK || echo blocked"
  echo -n "[ssh-keygen -y]   "; sbx "ssh-keygen -y -f '$KEY' >/dev/null 2>&1 && echo LEAK || echo blocked"
  echo -n "[ls ~/.ssh]       "; sbx "ls ~/.ssh >/dev/null 2>&1 && echo LEAK-LISTED || echo blocked"
else
  echo "(no key on this machine; read tests N/A)"
fi
echo

echo "=== B. SSH auth to github under env-prefix + sandbox (should FAIL) ==="
echo -n "[ssh -T git@github env+sbx] "
sbx "${PREFIX}timeout 15 ssh -T -o BatchMode=yes -o StrictHostKeyChecking=no git@github.com 2>&1 | head -1"
echo -n "[ssh -T git@github env-only(NO sbx)] "
bash -lc "${PREFIX}timeout 15 ssh -T -o BatchMode=yes -o StrictHostKeyChecking=no git@github.com 2>&1 | head -1"
echo

echo "=== C. alt exfil channels under sandbox+env (key must stay unreadable) ==="
if [ -n "$KEY" ]; then
  echo -n "[scp key out]     "; sbx "${PREFIX}scp '$KEY' /tmp/stolen_$$ >/dev/null 2>&1 && test -s /tmp/stolen_$$ && echo LEAK || echo blocked"; rm -f /tmp/stolen_$$
  echo -n "[rsync -e ssh]    "; sbx "${PREFIX}rsync -e ssh '$KEY' /tmp/stolen2_$$ >/dev/null 2>&1 && test -s /tmp/stolen2_$$ && echo LEAK || echo blocked"; rm -f /tmp/stolen2_$$
  echo -n "[GIT_SSH override inside] "
  sbx "${PREFIX}export GIT_SSH_COMMAND='ssh -o BatchMode=yes'; git -C '$WS' init -q 2>/dev/null; timeout 15 ssh -F none -i '$KEY' -o BatchMode=yes -o StrictHostKeyChecking=no -T git@github.com 2>&1 | head -1"
fi
echo
echo "=== D. control: read is allowed OUTSIDE ~/.ssh (proves deny is scoped not global) ==="
echo -n "[read /etc/hosts]  "; sbx "cat /etc/hosts >/dev/null 2>&1 && echo readable-ok || echo BLOCKED-TOO-BROAD"
