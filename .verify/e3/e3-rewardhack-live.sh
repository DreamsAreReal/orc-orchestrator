#!/bin/bash
# E3 reward-hack REGRESSION (B1 fix): a task whose STATE.md merely CLAIMS "DONE" with NO
# artifact and NO git commit must NOT be closed by the real dispatcher.poll_completions.
# Before the fix this closed the bd task + recorded "done" from the worker's self-report.
# After the fix, poll_completions requires an EXTERNAL fact (fresh commit / changed
# artifact since the worker started); with none it parks "suspected-fake-done" instead.
# Uses a real (isolated) bd hub and a real STATE.md on disk. No claude spawned.
#
# Also asserts the CONTROL: the SAME task WITH a real new commit IS closed (no false wall).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; PYSRC="$ROOT/src"
HUB="$(mktemp -d /tmp/orc-e3rh-hub.XXXXXX)"
PROJ="$(mktemp -d /tmp/orc-e3rh-proj.XXXXXX)"
trap 'rm -rf "$HUB" "$PROJ"' EXIT

PYTHONPATH="$PYSRC" python3 - "$HUB" "$PROJ" <<'PY'
import sys, os, time
from orc import beads, dispatcher as D, shift as shiftmod
hub, proj = sys.argv[1], sys.argv[2]
rc = 0

# real isolated bd hub
beads.init(hub)
tid = beads.create(hub, "e3 reward-hack task", priority=2)
beads.claim(hub, tid)
print("created + claimed bd task:", tid)

# make the project a real git repo with a CLEAN tree and NO new commits/artifacts
os.system("cd '%s' && git init -q && git config user.email w@x && git config user.name w "
          "&& git commit -q --allow-empty -m base" % proj)
head_before = os.popen("cd '%s' && git rev-parse HEAD" % proj).read().strip()

# the worker writes a STATE.md that ONLY CLAIMS done -- no deliverable, no commit
slug = "e3rh"
state_dir = os.path.join(proj, "docs", "tasks", slug)
os.makedirs(state_dir, exist_ok=True)
with open(os.path.join(state_dir, "STATE.md"), "w") as f:
    f.write("# STATE\n\n## Status\nDONE\n\n## Next step\nnothing, all done!\n")
print("worker wrote STATE.md claiming DONE; git HEAD unchanged, no deliverable file.")

# NB: the orc-managed docs/tasks/<slug>/STATE.md is NOT an external fact -- the fix's
# external_progress ignores our own artifacts (.claude/, docs/tasks/ are orc-managed),
# so a STATE.md that only says DONE cannot itself pass the wall.

# build the dispatcher shift state with one active worker for this task. started_epoch is
# NOW so external_progress judges commits/changes AFTER the worker started.
started = time.time()
state = {"workers": [], "done": [], "parked": [], "failed": []}
state["workers"].append({
    "task": tid, "project": proj, "slug": slug,
    "started_epoch": started, "tokens_before": None, "session": "e3rh", "tab_id": None,
})

# monkeypatch spawn.close_worker so we don't try to touch a terminal
import orc.spawn as sp
sp.close_worker = lambda *a, **k: {"killed": False, "window_closed": False}
if hasattr(D, "_worker_slug"):
    D._worker_slug = lambda hub, task_id, project, _slug=slug: _slug

def poll_once():
    """Run poll_completions once for `tid` with a fresh worker record; return (bd,done,parked)."""
    st = {"workers": [{"task": tid, "project": proj, "slug": slug,
                       "started_epoch": started, "tokens_before": None,
                       "session": "e3rh", "tab_id": None}],
          "done": [], "parked": [], "failed": []}
    st, _tr = D.poll_completions(st, hub, cfg={})
    info = beads.show(hub, tid)
    bd = info.get("status") if isinstance(info, dict) else info
    return bd, [d.get("task") for d in st.get("done", [])], [p.get("task") for p in st.get("parked", [])]

# --- ATTACK MATRIX: every "DONE" WITHOUT a real non-empty deliverable must be PARKED ---
# Cycle-2 reverify found two trivial bypasses of the first fix (external_progress accepted
# ANY foreign dirty file / ANY newer commit): an EMPTY `touch out.txt` and a
# `git commit --allow-empty` (0 diff). Both are token imitations of a fact, not facts.
def reset_task():
    # ensure the task is claimed and the tree is clean of prior artifacts (keep STATE.md)
    try:
        beads.reopen(hub, tid); beads.claim(hub, tid)
    except Exception:
        pass

fakes = [
    ("zero-artifact",       lambda: None),
    ("empty-touch",         lambda: open(os.path.join(proj, "out.txt"), "w").close()),
    ("allow-empty-commit",  lambda: os.system("cd '%s' && git commit -q --allow-empty -m done" % proj)),
]
for name, trick in fakes:
    reset_task()
    trick()
    bd, done_ids, parked_ids = poll_once()
    if tid in done_ids or bd in ("closed", "done"):
        print("[REGRESSION FAIL] fake DONE (%s) was CLOSED with no real deliverable" % name)
        rc = 1
    elif tid in parked_ids:
        print("[B1 WALL HELD] fake DONE (%s) PARKED (suspected-fake-done), bd=%s" % (name, bd))
    else:
        print("[REGRESSION FAIL] fake DONE (%s) neither closed nor parked" % name)
        rc = 1
    # clean the working tree for the next case (empty touch leaves a foreign file)
    os.system("cd '%s' && git checkout -q -- . 2>/dev/null; git clean -qfd "
              "-e docs/tasks >/dev/null 2>&1" % proj)

# --- CONTROL: DONE WITH a real NON-empty commit -> must be CLOSED (no false wall) ---
print("--- control: real NON-empty deliverable + commit AFTER start ---")
reset_task()
with open(os.path.join(proj, "DELIVERABLE.py"), "w") as f:
    f.write("def feature():\n    return 42\n")
os.system("cd '%s' && git add -A && git commit -q -m 'real deliverable'" % proj)
bd2, done2, parked2 = poll_once()
if tid in done2 and bd2 in ("closed", "done"):
    print("[CONTROL OK] real DONE (non-empty commit) IS closed -- wall does not block real work.")
else:
    print("[CONTROL FAIL] real DONE with a non-empty commit was NOT closed -- false wall (bd=%s)" % bd2)
    rc = 1

print("\n[REWARD-HACK REGRESSION %s]" % ("PASS" if rc == 0 else "FAIL"))
sys.exit(rc)
PY
