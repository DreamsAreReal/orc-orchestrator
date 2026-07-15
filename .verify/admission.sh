#!/bin/bash
# F5 ADMISSION + BACK-PRESSURE -- prove the limit-string classifier and admission gate on
# the real CLI limit-string fixtures (tests/fixtures/limit-*.txt), deterministically and
# WITHOUT spawning a real worker. Evidence -> docs/evidence/F5/.
set -u

ORC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYSRC="$ORC_ROOT/src"
FIX="$ORC_ROOT/tests/fixtures"
EVID="$ORC_ROOT/docs/evidence/F5"
mkdir -p "$EVID"
LOG="$EVID/admission.log"
: > "$LOG"
log() { echo "$@" | tee -a "$LOG"; }

log "=== F5 admission + back-pressure ==="
log "date: $(date)"
log ""

FAILS=0

# 1) classify every fixture and assert the expected (kind, reaction) pair.
log "--- limit-string classification on real CLI fixtures ---"
RESULT="$(PYTHONPATH="$PYSRC" python3 - "$FIX" <<'PY'
import sys, datetime
from orc import admission
fixdir = sys.argv[1]
now = datetime.datetime(2026, 7, 15, 10, 0, 0)  # Wed 10:00, deterministic
expect = {
    "limit-session.txt": ("session", "park", True),
    "limit-weekly.txt":  ("weekly",  "park", True),
    "limit-opus.txt":    ("opus",    "degrade", True),
    "limit-429.txt":     ("429",     "retry", False),
    "limit-529.txt":     ("529",     "retry", False),
    "limit-none.txt":    (None,      None,    False),
}
bad = 0
for name, (ek, er, ereset) in expect.items():
    with open(f"{fixdir}/{name}", encoding="utf-8") as f:
        text = f.read()
    c = admission.classify_limit(text, now=now)
    if ek is None:
        ok = c is None
        got = "None"
    else:
        has_reset = c is not None and c.get("reset_epoch") is not None
        ok = c and c["kind"] == ek and c["reaction"] == er and has_reset == ereset
        got = f"{c['kind']}/{c['reaction']}/reset={c.get('reset_raw')}" if c else "None"
    mark = "PASS" if ok else "FAIL"
    if not ok:
        bad += 1
    print(f"  [{mark}] {name:20s} -> {got}")
print(f"CLASSIFY_FAILS={bad}")
PY
)"
echo "$RESULT" | tee -a "$LOG"
CB="$(echo "$RESULT" | grep -o 'CLASSIFY_FAILS=[0-9]*' | cut -d= -f2)"
[ "${CB:-1}" -ne 0 ] && FAILS=$((FAILS + CB))
log ""

# 2) admission gate decisions: ready/ram/window/limit signals.
log "--- admission gate decisions ---"
GATE="$(PYTHONPATH="$PYSRC" python3 - "$FIX" <<'PY'
import sys, datetime
from orc import admission
fixdir = sys.argv[1]
now = datetime.datetime(2026, 7, 15, 10, 0, 0)
cfg = {"min_free_ram_mb": 400, "min_window_minutes": 5}
win = {"active": True, "remaining_minutes": 120}
def sess():
    with open(f"{fixdir}/limit-session.txt", encoding="utf-8") as f: return f.read()
cases = [
    ("all green -> admit",           dict(free_ram_mb=2000, window=win, ready_count=3), True,  None),
    ("no ready -> refuse",           dict(free_ram_mb=2000, window=win, ready_count=0), False, "no-ready"),
    ("low ram -> refuse (no spawn)", dict(free_ram_mb=200,  window=win, ready_count=3), False, "low-ram"),
    ("window closed -> refuse",      dict(free_ram_mb=2000, window={"active":True,"remaining_minutes":2}, ready_count=3), False, "window-low"),
    ("session limit -> park",        dict(free_ram_mb=2000, window=win, ready_count=3, limit_text=sess(), now=now), False, "limit-session"),
]
bad = 0
for name, kw, eok, ereason in cases:
    ok, reason, meta = admission.admission_check(cfg, **kw)
    good = (ok == eok) and (reason == ereason)
    if not good: bad += 1
    print(f"  [{'PASS' if good else 'FAIL'}] {name:34s} ok={ok} reason={reason}")
print(f"GATE_FAILS={bad}")
PY
)"
echo "$GATE" | tee -a "$LOG"
GB="$(echo "$GATE" | grep -o 'GATE_FAILS=[0-9]*' | cut -d= -f2)"
[ "${GB:-1}" -ne 0 ] && FAILS=$((FAILS + GB))
log ""

log "=== RESULT ==="
if [ "$FAILS" -eq 0 ]; then
  log "F5 ADMISSION PASS (6/6 fixtures classified + admission gate correct on RAM/window/limit)."
  exit 0
else
  log "F5 ADMISSION FAIL: $FAILS check(s)."
  exit 1
fi
