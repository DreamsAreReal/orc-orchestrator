"""Watchdog tests (F7): loop / silence detection, false-kill guard, bounded recovery.

Detection is fed synthetic heartbeat logs (no real worker). The critical guard -- a
legitimately long-running tool must NOT be killed -- is proven by the in-flight marker:
while a tool is in flight the worker is WORKING, so silence never fires, giving 0 false
kills on a >=2-minute Bash call. Recovery is bounded: a kill happens only after an
EXTERNAL post-condition check finds no real progress, and only up to the restart cap;
beyond it the task is escalated (parked), not silently retried forever.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import watchdog, shift as shiftmod, config  # noqa: E402


@pytest.fixture
def hb_home(tmp_path, monkeypatch):
    home = str(tmp_path / "orchome")
    monkeypatch.setenv("ORC_HOME", home)
    monkeypatch.setenv("ORC_HUB", home)
    config.ensure_home()
    return home


# --------------------------------------------------------------------------- #
# heartbeat + marker plumbing
# --------------------------------------------------------------------------- #
def test_record_heartbeat_writes_line_and_clears_marker(hb_home):
    watchdog.mark_in_flight("s1", "Bash", now=1000)
    assert watchdog.in_flight("s1")[0] is True
    watchdog.record_heartbeat("s1", "Bash", {"command": "ls"}, now=1001)
    beats = watchdog.read_heartbeats("s1")
    assert len(beats) == 1 and beats[0][1] == "Bash"
    # PostToolUse cleared the in-flight marker
    assert watchdog.in_flight("s1")[0] is False


def test_arg_hash_is_stable_and_order_independent():
    a = watchdog.arg_hash("Bash", {"command": "ls", "x": 1})
    b = watchdog.arg_hash("Bash", {"x": 1, "command": "ls"})
    c = watchdog.arg_hash("Bash", {"command": "pwd"})
    assert a == b and a != c


# --------------------------------------------------------------------------- #
# LOOP detection (K identical hashes)
# --------------------------------------------------------------------------- #
def test_detect_loop_when_last_k_hashes_identical():
    same = watchdog.arg_hash("Bash", {"command": "npm test"})
    beats = [(1000 + i, "Bash", same) for i in range(4)]
    assert watchdog.detect_loop(beats, k=4) is True


def test_no_loop_when_hashes_vary():
    beats = [
        (1000, "Bash", "aaa"),
        (1001, "Edit", "bbb"),
        (1002, "Bash", "aaa"),
        (1003, "Read", "ccc"),
    ]
    assert watchdog.detect_loop(beats, k=4) is False


def test_no_loop_below_k():
    beats = [(1000, "Bash", "aaa"), (1001, "Bash", "aaa")]
    assert watchdog.detect_loop(beats, k=4) is False


# --------------------------------------------------------------------------- #
# CYCLE detection (P1: short cycles detect_loop misses -- E3 fuzz)
# --------------------------------------------------------------------------- #
def test_detect_cycle_catches_alternation_ababab():
    """E3 EVASION-1: A/B/A/B flips between 2 identical calls; detect_loop can't see it
    (no K consecutive identical), but the window has only 2 distinct hashes -> a cycle."""
    seq = [("Bash", "AAAA"), ("Bash", "BBBB")] * 10
    beats = [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]
    assert watchdog.detect_loop(beats, k=4) is False            # strict detector misses it
    assert watchdog.detect_cycle(beats, window=8, max_unique=3) is True


def test_detect_cycle_catches_abc_rotation():
    """E3 EVASION-2: A/B/C rotation -- 3 distinct calls churning, still a cycle."""
    seq = [("Bash", "AAAA"), ("Bash", "BBBB"), ("Bash", "CCCC")] * 10
    beats = [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]
    assert watchdog.detect_loop(beats, k=4) is False
    assert watchdog.detect_cycle(beats, window=8, max_unique=3) is True


def test_detect_cycle_does_not_fire_on_real_varied_work():
    """A worker doing real work issues MANY distinct calls in a window -> NOT a cycle.
    (Guard against false positives at the pure-detector level.)"""
    seq = [("Edit", "e%d" % i) for i in range(8)]     # 8 distinct edits, all different
    beats = [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]
    assert watchdog.detect_cycle(beats, window=8, max_unique=3) is False


def test_detect_cycle_requires_full_window():
    """Do not fire early: a short log below the window length is not judged."""
    seq = [("Bash", "AAAA"), ("Bash", "BBBB")] * 2         # only 4 beats < window 8
    beats = [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]
    assert watchdog.detect_cycle(beats, window=8, max_unique=3) is False


def test_detect_cycle_disabled_by_config():
    seq = [("Bash", "AAAA"), ("Bash", "BBBB")] * 10
    beats = [(1000 + i, t, h) for i, (t, h) in enumerate(seq)]
    assert watchdog.detect_cycle(beats, window=1, max_unique=3) is False
    assert watchdog.detect_cycle(beats, window=8, max_unique=0) is False


def test_classify_reports_loop_on_alternating_cycle(hb_home):
    """End-to-end: an A/B/A/B spin-loop is now classified LOOP (was OK before P1)."""
    for i in range(12):
        cmd = "git status" if i % 2 == 0 else "git diff"
        watchdog.record_heartbeat("cyc", "Bash", {"command": cmd}, now=3000 + i)
    cfg = {"loop_hash_k": 4, "loop_cycle_window": 8, "loop_cycle_max_unique": 3}
    v = watchdog.classify("cyc", cfg, now=3020, busy=False)
    assert v == watchdog.VERDICT_LOOP


def test_supervise_spares_cyclic_worker_that_is_progressing(monkeypatch):
    """False-kill guard holds for cycles too: a cyclic-looking worker that ACTUALLY changed
    files on disk is spared by the external post-condition check, not killed."""
    st = _state_with_worker()
    closed = []
    monkeypatch.setattr(watchdog, "external_progress", lambda p, since_epoch=None: True)
    from orc import spawn as spawnmod
    monkeypatch.setattr(spawnmod, "close_worker",
                        lambda cfg, tab, session=None: closed.append(tab))
    actions = watchdog.supervise({"restart_cap": 2}, "hub", st,
                                 verdicts={"t1": watchdog.VERDICT_LOOP})
    assert actions[0]["action"] == "spared" and closed == []


def test_classify_reports_loop(hb_home):
    same = watchdog.arg_hash("Bash", {"command": "git status"})
    for i in range(4):
        watchdog.record_heartbeat("loopsess", "Bash", {"command": "git status"},
                                  now=2000 + i)
    v = watchdog.classify("loopsess", {"loop_hash_k": 4}, now=2010, busy=False)
    assert v == watchdog.VERDICT_LOOP


# --------------------------------------------------------------------------- #
# SILENCE detection -- and the false-kill guard (in-flight marker)
# --------------------------------------------------------------------------- #
def test_detect_silence_when_quiet_and_not_busy():
    beats = [(1000, "Bash", "aaa")]
    # 200s later, not busy -> silence past a 120s threshold
    assert watchdog.detect_silence(beats, busy=False, now=1200, silence_seconds=120) is True


def test_no_silence_within_threshold():
    beats = [(1000, "Bash", "aaa")]
    assert watchdog.detect_silence(beats, busy=False, now=1050, silence_seconds=120) is False


def test_long_running_tool_is_not_silence_zero_false_kills():
    # A worker started a Bash tool 5 minutes ago and has posted no heartbeat since (the
    # tool is still running). The in-flight marker means busy=True -> NOT silence, so a
    # live >=2-minute Bash call is never killed (acceptance F7).
    beats = [(1000, "Bash", "aaa")]
    assert watchdog.detect_silence(beats, busy=True, now=1000 + 300,
                                   silence_seconds=120) is False


def test_classify_silence_only_when_marker_absent(hb_home):
    # heartbeat 300s ago, no in-flight marker -> silence
    watchdog.record_heartbeat("q", "Bash", {"command": "x"}, now=1000)
    v = watchdog.classify("q", {"loop_hash_k": 4}, now=1300, silence_seconds=120)
    assert v == watchdog.VERDICT_SILENCE
    # now a tool goes in flight -> busy -> classify must NOT say silence
    watchdog.mark_in_flight("q", "Bash", now=1300)
    v2 = watchdog.classify("q", {"loop_hash_k": 4}, now=1400, silence_seconds=120)
    assert v2 == watchdog.VERDICT_OK


def test_stale_marker_does_not_mask_silence(hb_home):
    # a marker far older than the staleness bound (worker died mid-tool) is ignored so
    # silence still fires -- otherwise a crashed worker would look "busy" forever.
    watchdog.record_heartbeat("dead", "Bash", {"command": "x"}, now=1000)
    watchdog.mark_in_flight("dead", "Bash", now=1000)
    busy, _ = watchdog.in_flight("dead", now=1000 + 10000, max_tool_seconds=480)
    assert busy is False


# --------------------------------------------------------------------------- #
# external post-condition check (never trust self-report)
# --------------------------------------------------------------------------- #
def _repo(path):
    import subprocess
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True)
    with open(os.path.join(path, "f.txt"), "w") as f:
        f.write("a\n")
    subprocess.run(["git", "-C", path, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], check=True)
    return path


def test_external_progress_true_on_uncommitted_changes(tmp_path):
    repo = _repo(str(tmp_path / "wip"))
    with open(os.path.join(repo, "new.py"), "w") as f:
        f.write("real work\n")
    assert watchdog.external_progress(repo, since_epoch=time.time() - 3600) is True


def test_external_progress_false_when_nothing_changed(tmp_path):
    repo = _repo(str(tmp_path / "idle"))
    # committed long ago, no dirty files, and started_epoch is "now" -> no fresh progress
    assert watchdog.external_progress(repo, since_epoch=time.time() + 10) is False


def test_external_progress_false_on_empty_touch(tmp_path):
    """B1 cycle-2: an EMPTY `touch out.txt` is a token, not a deliverable -> no progress.
    (reverify found this bypassed the first fix, which accepted any foreign dirty file.)"""
    import subprocess
    repo = _repo(str(tmp_path / "touch"))
    since = time.time()               # worker starts AFTER the baseline commit
    time.sleep(1.1)
    subprocess.run(["touch", os.path.join(repo, "out.txt")], check=True)  # empty file
    assert watchdog.external_progress(repo, since_epoch=since) is False
    # control: a non-empty dirty file IS progress
    with open(os.path.join(repo, "real.txt"), "w") as f:
        f.write("x\n")
    assert watchdog.external_progress(repo, since_epoch=since) is True


def test_external_progress_false_on_allow_empty_commit(tmp_path):
    """B1 cycle-2: `git commit --allow-empty` (0 diff) is a token, not a deliverable."""
    import subprocess
    repo = _repo(str(tmp_path / "emptycommit"))
    since = time.time()
    time.sleep(1.1)  # the empty commit is strictly newer than `since`
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "done"], check=True)
    assert watchdog.external_progress(repo, since_epoch=since) is False


def test_external_progress_true_on_real_nonempty_commit(tmp_path):
    """Control: a commit that adds a NON-empty file IS real progress (no false wall)."""
    import subprocess
    repo = _repo(str(tmp_path / "realcommit"))
    since = time.time()
    time.sleep(1.1)
    with open(os.path.join(repo, "feature.py"), "w") as f:
        f.write("def f():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "real"], check=True)
    assert watchdog.external_progress(repo, since_epoch=since) is True


def test_external_progress_false_when_commit_only_touches_orc_managed(tmp_path):
    """A commit that only rewrites the worker's own docs/tasks/STATE.md is not a deliverable."""
    import subprocess
    repo = _repo(str(tmp_path / "stateonly"))
    since = time.time()
    time.sleep(1.1)
    sd = os.path.join(repo, "docs", "tasks", "s")
    os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "STATE.md"), "w") as f:
        f.write("# STATE\nStatus: DONE\n")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "state"], check=True)
    assert watchdog.external_progress(repo, since_epoch=since) is False


def test_external_progress_false_on_empty_touch_non_git(tmp_path):
    """Non-git fallback: an empty file written after start is still not a deliverable."""
    import subprocess
    proj = str(tmp_path / "nongit")
    os.makedirs(proj)
    since = time.time() - 10
    subprocess.run(["touch", os.path.join(proj, "empty.txt")], check=True)
    assert watchdog.external_progress(proj, since_epoch=since) is False
    with open(os.path.join(proj, "real.txt"), "w") as f:
        f.write("content\n")
    assert watchdog.external_progress(proj, since_epoch=since) is True


# --------------------------------------------------------------------------- #
# bounded recovery: restart only after external check, cap -> escalate
# --------------------------------------------------------------------------- #
def _state_with_worker(task="t1", project="/p", restarts=0):
    st = shiftmod._empty()
    st["workers"] = [{"task": task, "session": task, "project": project,
                      "tab_id": "9", "started_epoch": time.time() - 300,
                      "restarts": restarts}]
    return st


def test_supervise_spares_worker_that_is_progressing(monkeypatch):
    st = _state_with_worker()
    closed = []
    monkeypatch.setattr(watchdog, "external_progress", lambda p, since_epoch=None: True)
    from orc import spawn as spawnmod
    monkeypatch.setattr(spawnmod, "close_worker",
                        lambda cfg, tab, session=None: closed.append(tab))
    actions = watchdog.supervise({"restart_cap": 2}, "hub", st,
                                 verdicts={"t1": watchdog.VERDICT_LOOP})
    assert actions[0]["action"] == "spared"
    assert closed == []                       # never killed
    assert len(st["workers"]) == 1            # still running


def test_supervise_restarts_stuck_worker_no_progress(monkeypatch):
    st = _state_with_worker(restarts=0)
    closed, reopened = [], []
    from orc import spawn as spawnmod, beads
    monkeypatch.setattr(spawnmod, "close_worker",
                        lambda cfg, tab, session=None: closed.append(tab))
    monkeypatch.setattr(beads, "reopen", lambda hub, tid: reopened.append(tid))
    actions = watchdog.supervise(
        {"restart_cap": 2}, "hub", st,
        project_progress={"/p": False},        # external check: NO real progress
        verdicts={"t1": watchdog.VERDICT_SILENCE})
    assert actions[0]["action"] == "restart"
    assert closed == ["9"]                      # worker killed (RAM freed)
    assert reopened == ["t1"]                    # bd reopened -> fresh restart from STATE.md
    assert st["workers"] == []                   # dead worker record dropped


def test_supervise_escalates_when_cap_reached(monkeypatch):
    # already at the cap -> escalate (park), do NOT restart again
    st = _state_with_worker(restarts=2)
    from orc import spawn as spawnmod, beads
    monkeypatch.setattr(spawnmod, "close_worker", lambda cfg, tab, session=None: None)
    monkeypatch.setattr(beads, "reopen", lambda hub, tid: None)
    monkeypatch.setattr(beads, "set_status", lambda *a, **k: None)
    actions = watchdog.supervise(
        {"restart_cap": 2}, "hub", st,
        project_progress={"/p": False},
        verdicts={"t1": watchdog.VERDICT_LOOP})
    assert actions[0]["action"] == "escalate"
    assert any(p["task"] == "t1" and "restart cap" in p["reason"]
               for p in st["parked"])


def test_supervise_ignores_healthy_worker():
    st = _state_with_worker()
    actions = watchdog.supervise({"restart_cap": 2}, "hub", st,
                                 verdicts={"t1": watchdog.VERDICT_OK})
    assert actions == [] and len(st["workers"]) == 1


def test_can_restart_respects_cap():
    assert watchdog.can_restart({"restarts": 1}, {"restart_cap": 2}) is True
    assert watchdog.can_restart({"restarts": 2}, {"restart_cap": 2}) is False
