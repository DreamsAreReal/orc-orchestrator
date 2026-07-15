#!/bin/bash
# P1 regression: the watchdog loop detector now catches SHORT-CYCLE meltdowns that the
# strict K-consecutive-identical detector missed (E3 fuzz watchdog.py:201). A/B/A/B
# alternation and A/B/C rotation are now flagged as a loop, while a live worker doing
# real varied work (many distinct calls) is NOT falsely flagged.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
PYTHONPATH="$PYSRC" python3 - <<'PY'
import sys
from orc import watchdog as wd

WIN, MU, K = 8, 3, 4
fail = 0
def check(label, got, want):
    global fail
    ok = (got is want)
    print("%-56s got=%-5s want=%-5s %s" % (label, got, want, "OK" if ok else "*** FAIL"))
    if not ok:
        fail += 1

def beats(seq):
    return [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]

# EVASION-1: alternating A/B/A/B -- strict detector still misses, cycle detector catches.
alt = beats([("Bash", "AAAA"), ("Bash", "BBBB")] * 10)
check("EVASION-1 A/B/A/B  detect_loop (strict, misses)", wd.detect_loop(alt, K), False)
check("EVASION-1 A/B/A/B  detect_cycle (P1 catches)", wd.detect_cycle(alt, WIN, MU), True)

# EVASION-2: A/B/C rotation.
rot = beats([("Bash", "AAAA"), ("Bash", "BBBB"), ("Bash", "CCCC")] * 10)
check("EVASION-2 A/B/C    detect_loop (strict, misses)", wd.detect_loop(rot, K), False)
check("EVASION-2 A/B/C    detect_cycle (P1 catches)", wd.detect_cycle(rot, WIN, MU), True)

# LIVE varied work: WIN distinct calls -> NOT a cycle (no false kill at detector level).
live = beats([("Edit", "e%d" % i) for i in range(WIN)])
check("LIVE varied work (%d distinct) detect_cycle" % WIN, wd.detect_cycle(live, WIN, MU), False)

# Short log below the window is not judged (no early false fire).
short = beats([("Bash", "AAAA"), ("Bash", "BBBB")] * 2)
check("SHORT log (< window) detect_cycle", wd.detect_cycle(short, WIN, MU), False)

# classify() end-to-end: an alternating spin-loop is now VERDICT_LOOP.
import os, tempfile
os.environ["ORC_HOME"] = tempfile.mkdtemp(prefix="orc-p1wd-")
os.environ["ORC_HUB"] = os.environ["ORC_HOME"]
from orc import config
config.ensure_home()
for i in range(12):
    cmd = "git status" if i % 2 == 0 else "git diff"
    wd.record_heartbeat("cyc", "Bash", {"command": cmd}, now=3000 + i)
cfg = {"loop_hash_k": K, "loop_cycle_window": WIN, "loop_cycle_max_unique": MU}
v = wd.classify("cyc", cfg, now=3020, busy=False)
check("classify A/B/A/B live-log == VERDICT_LOOP", v == wd.VERDICT_LOOP, True)

print()
if fail:
    print("P1 LOOP-CYCLE FAIL (%d checks failed)" % fail)
    sys.exit(1)
print("P1 LOOP-CYCLE PASS (short cycles caught; live varied work spared)")
PY
