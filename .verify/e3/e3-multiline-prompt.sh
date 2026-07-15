#!/bin/bash
# P0 REGRESSION: a MULTILINE worker prompt (apostrophes / backticks / single+double quotes /
# newlines -- a real GATE prompt, e.g. STATE.md content) must reach claude byte-exact and the
# launch command must NEVER hang the shell at a `quote>` continuation. Before the fix the
# prompt was inlined into the osascript `do script "..."` arg; a literal newline broke the
# AppleScript parse and claude never started (single-line prompts happened to work).
# Seam: printf stands in for claude and echoes the argument it received. No claude spawned.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
WS="$(mktemp -d /tmp/orc-e3ml.XXXXXX)"
trap 'rm -rf "$WS"' EXIT
rc=0

PYTHONPATH="$PYSRC" python3 - "$WS" <<'PY'
import sys, subprocess
from orc import spawn
ws = sys.argv[1]
prompt = ("Resume gate task. Write docs/tasks/s/STATE.md:\n"
          "## Status\nparked-on-gate\n"
          "It's a gate; don't push. Use `git commit` not 'git push'.\n"
          'Line with "double" and \'single\' quotes.\n'
          "Final line.")

rc = 0
for sandbox_on in (False, True):
    label = "sandbox=%s" % sandbox_on
    cmd = spawn.build_start_command(ws, "/usr/bin/printf", prompt,
                                    session="g1", cfg={"sandbox": sandbox_on})
    # 1) single-line launch command (no literal newline -> AppleScript do-script safe)
    if "\n" in cmd:
        print("[FAIL] %s: launch command contains a literal newline (breaks do script)" % label); rc = 1; continue
    # 2) the prompt is NOT inlined (read from a file)
    if "parked-on-gate" in cmd:
        print("[FAIL] %s: prompt is inlined in the command (quoting hazard)" % label); rc = 1; continue
    # 3) run it: printf must receive the FULL multiline prompt as ONE argument, no continuation
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    if r.stderr.strip():
        print("[FAIL] %s: shell error / continuation: %r" % (label, r.stderr[:120])); rc = 1; continue
    if r.stdout.strip() != prompt.strip():
        print("[FAIL] %s: prompt did NOT round-trip byte-exact" % label)
        print("   GOT:\n" + r.stdout); rc = 1; continue
    print("[OK] %s: multiline prompt round-trips byte-exact, single-line launch, 0 continuation" % label)

sys.exit(rc)
PY
rc=$?
echo ""
echo "=== done (rc=$rc) ==="
exit $rc
