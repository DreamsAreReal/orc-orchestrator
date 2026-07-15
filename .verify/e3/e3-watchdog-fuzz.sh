#!/bin/bash
# E3 watchdog + reward-hack fuzz: try to DEFEAT loop/silence detection and forge liveness.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
export ORC_HOME="$(mktemp -d /tmp/orc-e3wd.XXXXXX)"; export ORC_HUB="$ORC_HOME"
trap 'rm -rf "$ORC_HOME"' EXIT
PYTHONPATH="$PYSRC" python3 - <<'PY'
import time, os
from orc import watchdog as wd
now = int(time.time())

def beats(seq, start=None, step=30):
    start = start or (int(time.time()) - step*len(seq))
    return [(start+i*step, t, h) for i,(t,h) in enumerate(seq)]

cfg = {"loop_hash_k": 4, "restart_cap": 2}

# --- EVASION 1: alternating two-tool loop (hash flips A,B,A,B...) ---
seq = [("Bash","AAAA"),("Bash","BBBB")]*10
alt = beats(seq)
print("EVASION-1 alternating A/B/A/B x20 -> detect_loop:", wd.detect_loop(alt, 4),
      "(a spin-loop that flips between 2 identical calls is NOT caught)")

# --- EVASION 2: rotating three-tool loop ---
seq3 = [("Bash","AAAA"),("Bash","BBBB"),("Bash","CCCC")]*10
print("EVASION-2 A/B/C rotation x10 -> detect_loop:", wd.detect_loop(beats(seq3), 4))

# --- EVASION 3: k-1 identical then one different (stays under the k threshold) ---
seqk = [("Bash","AAAA")]*3 + [("Bash","ZZZZ")]
print("EVASION-3 3 identical + 1 different (k=4) -> detect_loop:", wd.detect_loop(beats(seqk), 4))

# --- LIVENESS FORGE: hold an in-flight marker to look 'busy' forever ---
sess = "e3-forge"
wd.mark_in_flight(sess, "Bash", now=now-100000)   # marker from long ago
busy_nobound, _ = wd.in_flight(sess)               # no staleness bound
busy_bound, _ = wd.in_flight(sess, now=now, max_tool_seconds=480)  # classify() uses silence*4
print("LIVENESS-FORGE stale marker (100000s old): busy(no-bound)=%s busy(8min-bound)=%s"
      % (busy_nobound, busy_bound))
# classify with a silent log but a freshly-refreshed marker:
wd.record_heartbeat(sess, "Bash", {"x":1}, now=now-10000)  # last beat 10000s ago (silent)
wd.mark_in_flight(sess, "Bash", now=now-60)                # marker only 60s old -> looks busy
v = wd.classify(sess, cfg, now=now, silence_seconds=120)
print("LIVENESS-FORGE silent-10000s + fresh-60s-marker -> classify:", v,
      "(worker refreshing its marker faster than 8min avoids silence kill indefinitely)")
PY
echo ""
echo "=== REWARD-HACK: fake DONE with NO artifact/commit -> does poll_completions close it? ==="
PYTHONPATH="$PYSRC" python3 - <<'PY'
import os, tempfile, subprocess
from orc import dispatcher as D
# 1. pure text classification: a STATE.md that just SAYS done
fake_state = "# STATE\n\n## Status\nDONE\n\n## Next step\n(none)\n"
print("detect_terminal_status on a STATE.md that only *claims* DONE:",
      D.detect_terminal_status(fake_state))
print(" -> poll_completions closes the bd task on THIS text alone; there is NO call to")
print("    external_progress / git-log / artifact check in the completion path.")
# grep proof: external_progress is referenced only in watchdog, never in poll_completions
import inspect
src = inspect.getsource(D.poll_completions)
print("external_progress mentioned in poll_completions? ->",
      "external_progress" in src or "git" in src or "head_commit" in src)
PY
echo ""
echo "=== done ==="
