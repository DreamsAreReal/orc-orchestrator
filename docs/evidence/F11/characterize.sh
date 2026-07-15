#!/usr/bin/env bash
# Characterization harness for F11: captures deterministic behavior of
# doctor + scorecard + hooks(STATE detection) on both fixtures.
# Usage: characterize.sh <fixtures-root> <out-dir>
set -u
FIX="$1"; OUT="$2"
mkdir -p "$OUT"
BIN="$HOME/.claude/skills/pipeline/bin"
WA="$FIX/proj-a"          # standard docs/ layout
WB="$FIX/proj-b"          # tasks/<slug>/docs layout

echo "=== 1. doctor (system-file + line-cap self-check) ===" | tee "$OUT/doctor.txt"
"$BIN/pipeline-lint.sh" --doctor >>"$OUT/doctor.txt" 2>&1
echo "doctor_exit=$?" | tee -a "$OUT/doctor.txt"

echo "=== 2. scorecard on STANDARD layout (proj-a) ===" > "$OUT/scorecard-standard.txt"
"$BIN/pipeline-scorecard.sh" "$WA" >>"$OUT/scorecard-standard.txt" 2>&1
echo "scorecard_standard_exit=$?" >> "$OUT/scorecard-standard.txt"

echo "=== 3. scorecard on TASKS layout (proj-b) ===" > "$OUT/scorecard-tasks.txt"
"$BIN/pipeline-scorecard.sh" "$WB" >>"$OUT/scorecard-tasks.txt" 2>&1
echo "scorecard_tasks_exit=$?" >> "$OUT/scorecard-tasks.txt"

# 4b. LIVE hook detection: extract the actual glob list executed by the installed hook,
#     by importing the hook module's logic indirectly. We grep the live source's glob line
#     and eval exactly what is in the file right now.
echo "=== 4b. LIVE hook glob (exact lines from installed pipeline-hooks.py) ===" > "$OUT/hooks-live-detect.txt"
grep -n 'glob(' "$BIN/pipeline-hooks.py" >> "$OUT/hooks-live-detect.txt" 2>&1
for name in standard:$WA tasks:$WB; do
  lbl="${name%%:*}"; root="${name##*:}"
  found=$(cd "$root" && python3 - "$BIN/pipeline-hooks.py" <<'PY'
import sys, glob as _g, re, os
src = open(sys.argv[1]).read()
lines = src.splitlines()
# locate the FIRST 'states = (' block; consume lines until the outer paren re-balances
start = next(i for i,l in enumerate(lines) if 'states = (' in l)
block = []; depth = 0; began = False
for l in lines[start:]:
    block.append(l)
    depth += l.count('(') - l.count(')')
    if '(' in l:
        began = True
    if began and depth == 0:
        break
blocktext = "\n".join(block)
globs = re.findall(r'_g\.glob\("([^"]+)"\)', blocktext)
states=[]
for g in globs: states += _g.glob(g)
print(len(states), "globs="+repr(globs), states)
PY
)
  echo "layout=$lbl LIVE-hook-detects -> $found" >> "$OUT/hooks-live-detect.txt"
done

echo "characterization written to $OUT"
