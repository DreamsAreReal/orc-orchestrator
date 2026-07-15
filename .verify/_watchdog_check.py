"""F7 watchdog verification driver (invoked by .verify/watchdog.sh).

Forges the exact heartbeat / in-flight artifacts the worker hooks write and drives the
watchdog against them -- no real claude. Prints PASS/FAIL per check and WD_FAILS=<n>.
"""
import time

from orc import watchdog, shift as shiftmod, spawn, beads

cfg = {"loop_hash_k": 4, "restart_cap": 2}
bad = 0

# 1) synthetic LOOP: 4 identical Bash heartbeats
for i in range(4):
    watchdog.record_heartbeat("loop", "Bash", {"command": "npm test"}, now=1000 + i)
v_loop = watchdog.classify("loop", cfg, now=1010, busy=False)
print("1) LOOP verdict:", v_loop, "->", "PASS" if v_loop == "loop" else "FAIL")
bad += v_loop != "loop"

# 2) synthetic SILENCE: one heartbeat 300s ago, no in-flight marker
watchdog.record_heartbeat("quiet", "Bash", {"command": "x"}, now=5000)
v_sil = watchdog.classify("quiet", cfg, now=5300, silence_seconds=120)
print("2) SILENCE verdict:", v_sil, "->", "PASS" if v_sil == "silence" else "FAIL")
bad += v_sil != "silence"

# 3) CONTROL / false-kill guard: a live long tool. Mark in flight, then let 200s pass with
#    NO new heartbeat -- exactly a >=2-minute Bash call. The marker means busy -> OK.
watchdog.record_heartbeat("longtool", "Bash", {"command": "big build"}, now=6000)
watchdog.mark_in_flight("longtool", "Bash", now=6000)
busy, started = watchdog.in_flight("longtool", now=6000 + 200, max_tool_seconds=480)
v_long = watchdog.classify("longtool", cfg, now=6000 + 200, silence_seconds=120, busy=busy)
ok3 = (busy is True) and (v_long == "ok")
print("3) long tool (200s in flight): busy=%s verdict=%s -> %s"
      % (busy, v_long, "PASS (0 false kills)" if ok3 else "FAIL"))
bad += (not ok3)

# 4a) bounded recovery: stuck worker, NO external progress -> restart (under cap)
st = shiftmod._empty()
st["workers"] = [{"task": "t1", "session": "t1", "project": "/p", "tab_id": "9",
                  "started_epoch": time.time() - 300, "restarts": 0}]
spawn.close_window = lambda tab: {"killed": 1, "window_closed": True}
beads.reopen = lambda hub, tid: True
acts = watchdog.supervise(cfg, "hub", st, project_progress={"/p": False},
                          verdicts={"t1": "silence"})
ok4a = acts and acts[0]["action"] == "restart" and st["workers"] == []
print("4a) stuck + no progress -> action=%s -> %s"
      % (acts[0]["action"] if acts else None, "PASS" if ok4a else "FAIL"))
bad += (not ok4a)

# 4b) worker IS progressing (external check) -> spared, never killed
st2 = shiftmod._empty()
st2["workers"] = [{"task": "t2", "session": "t2", "project": "/p2", "tab_id": "8",
                   "started_epoch": time.time() - 300, "restarts": 0}]
acts2 = watchdog.supervise(cfg, "hub", st2, project_progress={"/p2": True},
                           verdicts={"t2": "loop"})
ok4b = acts2 and acts2[0]["action"] == "spared" and len(st2["workers"]) == 1
print("4b) looks loopy but real progress -> action=%s -> %s"
      % (acts2[0]["action"] if acts2 else None, "PASS" if ok4b else "FAIL"))
bad += (not ok4b)

# 4c) cap reached -> escalate, not another restart
st3 = shiftmod._empty()
st3["workers"] = [{"task": "t3", "session": "t3", "project": "/p3", "tab_id": "7",
                   "started_epoch": time.time() - 300, "restarts": 2}]
beads.set_status = lambda *a, **k: None
acts3 = watchdog.supervise(cfg, "hub", st3, project_progress={"/p3": False},
                           verdicts={"t3": "loop"})
ok4c = acts3 and acts3[0]["action"] == "escalate" and any(
    p["task"] == "t3" for p in st3["parked"])
print("4c) restart cap reached -> action=%s -> %s"
      % (acts3[0]["action"] if acts3 else None, "PASS" if ok4c else "FAIL"))
bad += (not ok4c)

print("WD_FAILS=%d" % bad)
