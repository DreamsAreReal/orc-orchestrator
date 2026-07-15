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

# --- ATTACK: DONE with ZERO external fact -> must be PARKED, not closed ---
state, transitions = D.poll_completions(state, hub, cfg={})
print("transitions (fake-done):", transitions)
show = beads.show(hub, tid)
bd_status = show.get("status") if isinstance(show, dict) else show
done_ids = [d.get("task") for d in state.get("done", [])]
parked_ids = [p.get("task") for p in state.get("parked", [])]
head_after = os.popen("cd '%s' && git rev-parse HEAD" % proj).read().strip()
print("bd status after poll:", bd_status)
print("recorded DONE in shift.json:", tid in done_ids)
print("recorded PARKED (suspected-fake-done):", tid in parked_ids)
print("git HEAD before==after (no real work):", head_before == head_after)

if tid in done_ids or bd_status in ("closed", "done"):
    print("\n[REGRESSION FAIL] fake DONE was closed with ZERO external facts")
    rc = 1
elif tid in parked_ids:
    print("\n[B1 WALL HELD] fake DONE was PARKED (suspected-fake-done), bd NOT closed --")
    print("   DONE is confirmed by the world, not the worker's self-report.")
else:
    print("\n[REGRESSION FAIL] fake DONE was neither closed nor parked (state inconsistent)")
    rc = 1

# --- CONTROL: SAME task WITH a real new commit -> must be CLOSED (no false wall) ---
print("\n--- control: real deliverable + commit AFTER start ---")
beads.reopen(hub, tid); beads.claim(hub, tid)
with open(os.path.join(proj, "DELIVERABLE.txt"), "w") as f:
    f.write("real work product\n")
os.system("cd '%s' && git add -A && git commit -q -m 'real deliverable'" % proj)
state2 = {"workers": [{
    "task": tid, "project": proj, "slug": slug,
    "started_epoch": started, "tokens_before": None, "session": "e3rh", "tab_id": None,
}], "done": [], "parked": [], "failed": []}
state2, tr2 = D.poll_completions(state2, hub, cfg={})
show2 = beads.show(hub, tid)
bd2 = show2.get("status") if isinstance(show2, dict) else show2
done2 = [d.get("task") for d in state2.get("done", [])]
print("transitions (real work):", tr2, "| bd status:", bd2, "| recorded DONE:", tid in done2)
if tid in done2 and bd2 in ("closed", "done"):
    print("[CONTROL OK] real DONE (fresh commit) IS closed -- the wall does not block real work.")
else:
    print("[CONTROL FAIL] real DONE with a fresh commit was NOT closed -- false wall")
    rc = 1

sys.exit(rc)
PY
