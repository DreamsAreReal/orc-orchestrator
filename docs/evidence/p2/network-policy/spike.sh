#!/usr/bin/env bash
# P2 spike: prove the network policy is REAL at the OS level (not a printed flag).
#   open  -> the worker's network syscalls are PERMITTED (socket/connect reach the network
#            stack; a closed loopback port yields ECONNREFUSED, i.e. the stack was reached).
#   deny  -> the worker's network syscalls are BLOCKED by seatbelt (deny network*) (EPERM),
#            so there is no exfiltration channel at all.
# Uses a loopback connect so the result is decisive and does not depend on an external host.
set -uo pipefail
cd "$(dirname "$0")/../../../.."
ROOT="$PWD"
WS="$(mktemp -d -t orc-net-spike)"

read -r -d '' PROBE <<'PY' || true
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 9))
        print("NET_OK_CONNECT")
    except ConnectionRefusedError:
        print("NET_OK_REACHED_STACK")
    except PermissionError:
        print("NET_BLOCKED_EPERM")
    except OSError as e:
        print("NET_OSERR:" + (e.strerror or str(e)))
    finally:
        s.close()
except PermissionError:
    print("NET_BLOCKED_SOCKET_EPERM")
PY

run_policy () {
  python3 - "$ROOT" "$WS" "$1" "$PROBE" <<'PY'
import sys, subprocess, shlex
sys.path.insert(0, sys.argv[1] + "/src")
from orc import sandbox, config
root, ws, pol, probe = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
deny = config.network_deny({"network_policy": pol})
prof = sandbox.write_profile(ws, deny_network=deny)
cmd = sandbox.wrap_command(prof, "python3 -c " + shlex.quote(probe))
out = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
print(out.stdout.strip())
PY
}

echo "=== policy OPEN (network permitted) ==="
OPEN_OUT="$(run_policy open)"; echo "  result: $OPEN_OUT"
echo "=== policy DENY (network cut) ==="
DENY_OUT="$(run_policy deny)"; echo "  result: $DENY_OUT"

echo "=== assertions ==="
FAIL=0
case "$OPEN_OUT" in
  NET_OK_*) echo "  [ok] OPEN: network syscalls permitted (reached the stack)" ;;
  *) echo "  [FAIL] OPEN: network unexpectedly blocked -> $OPEN_OUT"; FAIL=1 ;;
esac
case "$DENY_OUT" in
  NET_BLOCKED_*) echo "  [ok] DENY: network cut at the OS level -> $DENY_OUT" ;;
  *) echo "  [FAIL] DENY: network was NOT cut -> $DENY_OUT"; FAIL=1 ;;
esac
[ "$FAIL" = 0 ] && echo "P2 NETWORK-POLICY PASS" || { echo "P2 NETWORK-POLICY FAIL"; exit 1; }
