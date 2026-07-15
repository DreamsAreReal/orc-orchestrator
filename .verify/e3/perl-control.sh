#!/bin/bash
# Isolated with/without-sandbox control for the perl unlink case. Fresh sentinel each run.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
WS="$(mktemp -d /tmp/orc-e3perl-ws.XXXXXX)"
OUT="$(mktemp -d /tmp/orc-e3perl-out.XXXXXX)"   # OUT is a SIBLING, not under WS
SENT="$OUT/sentinel.txt"
cleanup(){ rm -rf "$WS" "$OUT" 2>/dev/null; }
trap cleanup EXIT
PROFILE="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.write_profile('$WS'))")"

echo "=== CONTROL-A: perl unlink WITHOUT sandbox (must delete -> proves perl works) ==="
echo ALIVE > "$SENT"
perl -e "unlink('$SENT')" 2>&1
[ -f "$SENT" ] && echo "  sentinel PRESENT (perl failed to delete?)" || echo "  sentinel DELETED (perl works, unsandboxed)"

echo "=== CONTROL-B: perl unlink UNDER orc sandbox (must be blocked -> sentinel survives) ==="
echo ALIVE > "$SENT"
WRAP="$(python3 -c "import sys;sys.path.insert(0,'$PYSRC');from orc import sandbox;print(sandbox.wrap_command('$PROFILE', sys.argv[1]))" "perl -e 'unlink(\"$SENT\")'")"
bash -c "$WRAP" 2>&1 | sed 's/^/    /'
if [ -f "$SENT" ]; then echo "  RESULT: sentinel SURVIVED -> perl unlink BLOCKED by sandbox [ok]"; RC=0
else echo "  RESULT: sentinel DELETED -> perl unlink LEAKED THROUGH SANDBOX [REAL LEAK]"; RC=1; fi

echo "=== CONTROL-C: OUT dir is genuinely OUTSIDE ws? ==="
echo "  WS=$WS"; echo "  OUT=$OUT"
case "$OUT/" in "$WS"/*) echo "  !! OUT is under WS -> test invalid";; *) echo "  OUT is a sibling, outside WS [ok]";; esac
exit ${RC:-0}
