"""Dispatcher recovery + lease TTL + robust PID capture tests (F8).

After a kill -9 the dispatcher restarts from shift.json (on disk, atomic) and reconciles:
live workers are adopted (no duplicate spawn), dead workers return their task to ready via
a lease. The eval's `pid None` finding is fixed by resolving the PID from the spawned
window's tty (race-free) instead of lsof cwd-matching right after spawn. Deterministic:
PIDs and window/tty resolution are injected; no real terminal is opened.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import dispatcher, shift as shiftmod, spawn, config  # noqa: E402


# --------------------------------------------------------------------------- #
# atomic shift.json survives a crash (tmp + rename)
# --------------------------------------------------------------------------- #
def test_shift_json_atomic_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    st = shiftmod._empty()
    shiftmod.add_worker(st, pid=1234, session="t1", project="/p", task="t1", tab_id="9")
    path = shiftmod.save(st)
    assert os.path.exists(path)
    # a crash cannot leave a half-written file: the tmp is renamed atomically
    assert not os.path.exists(path + ".tmp")
    reloaded = shiftmod.load()
    assert reloaded["workers"][0]["task"] == "t1"
    assert reloaded["workers"][0]["pid"] == 1234


# --------------------------------------------------------------------------- #
# reconcile after restart: live adopted, dead -> ready via lease, idempotent
# --------------------------------------------------------------------------- #
def test_reconcile_adopts_live_worker_no_duplicate(monkeypatch):
    st = shiftmod._empty()
    live = os.getpid()  # this process is alive
    st["workers"] = [{"pid": live, "task": "t1", "project": "/p",
                      "started_epoch": time.time()}]
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    st, dropped = dispatcher.reconcile(st, "hub", cfg={})
    assert dropped == []                       # live worker adopted, not re-served
    assert reopened == []                       # its task is NOT put back to ready
    assert len(st["workers"]) == 1


def test_reconcile_dead_worker_returns_task_to_ready(monkeypatch):
    st = shiftmod._empty()
    st["workers"] = [{"pid": 999999, "task": "t2", "project": "",
                      "started_epoch": time.time() - 5000, "tab_id": None}]
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "status": "in_progress"})
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [])
    st, dropped = dispatcher.reconcile(st, "hub", cfg={})
    assert dropped == ["t2"]
    assert reopened == ["t2"]                    # lease: task back to ready
    assert st["workers"] == []


def test_reconcile_is_idempotent(monkeypatch):
    st = shiftmod._empty()
    st["workers"] = [{"pid": 999999, "task": "t3", "project": "",
                      "started_epoch": time.time() - 5000}]
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "status": "open"})
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [])
    st, d1 = dispatcher.reconcile(st, "hub", cfg={})
    st, d2 = dispatcher.reconcile(st, "hub", cfg={})   # second pass: nothing left to drop
    assert d1 == ["t3"] and d2 == []                    # no double-drop, no duplicate
    assert reopened == ["t3"]                            # reopened exactly once


def test_reconcile_does_not_reopen_closed_task(monkeypatch):
    st = shiftmod._empty()
    st["workers"] = [{"pid": 999999, "task": "done1", "project": "",
                      "started_epoch": time.time() - 5000}]
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "status": "closed"})
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [])
    st, dropped = dispatcher.reconcile(st, "hub", cfg={})
    assert dropped == ["done1"]
    assert reopened == []                        # a task bd already closed is NOT re-served


# --------------------------------------------------------------------------- #
# lease safety: a within-lease worker with an unread PID is re-resolved, not dropped
# --------------------------------------------------------------------------- #
def test_reconcile_reresolves_pid_within_lease(monkeypatch):
    st = shiftmod._empty()
    # PID absent (unread at spawn), but the worker is fresh and has a window we can query
    st["workers"] = [{"pid": None, "task": "t4", "project": "/p", "tab_id": "42",
                      "started_epoch": time.time()}]
    live = os.getpid()
    monkeypatch.setattr(dispatcher.spawn, "pid_on_window",
                        lambda tab, retries=1, delay=0: live)
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    st, dropped = dispatcher.reconcile(st, "hub", cfg={"lease_ttl_seconds": 1800})
    assert dropped == []                         # re-resolved -> adopted, not dropped
    assert reopened == []
    assert st["workers"][0]["pid"] == live       # PID backfilled


def test_reconcile_drops_when_lease_expired(monkeypatch):
    st = shiftmod._empty()
    # old worker, PID dead, past the lease TTL -> genuinely dead, drop it
    st["workers"] = [{"pid": 999999, "task": "t5", "project": "/p", "tab_id": "42",
                      "started_epoch": time.time() - 99999}]
    called = []
    monkeypatch.setattr(dispatcher.spawn, "pid_on_window",
                        lambda *a, **k: called.append(1) or None)
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [])
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: None)
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "status": "in_progress"})
    st, dropped = dispatcher.reconcile(st, "hub", cfg={"lease_ttl_seconds": 1800})
    assert dropped == ["t5"]
    # past lease -> the window tty is not even consulted (worker is really dead)
    assert called == []


# --------------------------------------------------------------------------- #
# robust PID capture (F8 fix for the eval's `pid None`)
# --------------------------------------------------------------------------- #
def test_pid_on_window_prefers_claude(monkeypatch):
    monkeypatch.setattr(spawn, "window_tty", lambda wid: "/dev/ttys999")
    monkeypatch.setattr(spawn, "pids_on_tty", lambda tty: [111, 222])
    monkeypatch.setattr(spawn, "_first_claude_pid", lambda pids: 222)
    assert spawn.pid_on_window("42", retries=1, delay=0) == 222


def test_pid_on_window_falls_back_to_newest(monkeypatch):
    monkeypatch.setattr(spawn, "window_tty", lambda wid: "/dev/ttys999")
    monkeypatch.setattr(spawn, "pids_on_tty", lambda tty: [111, 500, 222])
    monkeypatch.setattr(spawn, "_first_claude_pid", lambda pids: None)
    assert spawn.pid_on_window("42", retries=1, delay=0) == 500  # max PID


def test_pid_on_window_none_when_no_process(monkeypatch):
    monkeypatch.setattr(spawn, "window_tty", lambda wid: None)
    monkeypatch.setattr(spawn, "pids_on_tty", lambda tty: [])
    assert spawn.pid_on_window("42", retries=2, delay=0) is None


def test_spawn_one_records_real_pid_via_window(tmp_path, monkeypatch):
    import subprocess
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
           "min_free_ram_mb": 400, "min_window_minutes": 5, "allow_no_sandbox": True}
    st = shiftmod._empty()
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: True)
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher, "revalidate", lambda p, t: None)
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    # F15: spawn_one routes through the backend selector; worker_pid resolves a real PID
    monkeypatch.setattr(dispatcher.spawn, "spawn_worker",
                        lambda cfg, p, c, prompt, session=None: (True, "77"))
    monkeypatch.setattr(dispatcher.spawn, "worker_pid",
                        lambda cfg, p, session, handle=None: 45678)
    task = {"id": "tp", "metadata": {"project": repo, "slug": "tp", "text": "x"}}
    ok, detail, st = dispatcher.spawn_one(cfg, "hub", st, task)
    assert ok is True
    assert st["workers"][0]["pid"] == 45678       # real PID captured (not None)
    assert st["workers"][0]["tab_id"] == "77"


# --------------------------------------------------------------------------- #
# P4 (E3 fix): `orc stop` is PID-anchored -- it kills the RECORDED worker PID even when
# tty resolution fails (stale/gone window on the Terminal backend), so no worker survives.
# --------------------------------------------------------------------------- #
def test_stop_kills_recorded_pid_when_tty_resolution_fails(tmp_path, monkeypatch):
    import subprocess
    import time as _t
    from orc import cli
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    # a REAL, killable child worker (its own process group so we can assert it dies)
    child = subprocess.Popen(["sleep", "30"])
    try:
        # record it in shift.json as an active worker with a tab_id whose tty WON'T resolve
        st = shiftmod._empty()
        shiftmod.add_worker(st, pid=child.pid, session="w1", project="/p",
                            task="t-stop", tab_id="9999")
        shiftmod.save(st)

        # simulate the degraded Terminal case: window_tty returns None (window gone/stale),
        # close_worker's tty kill therefore does nothing -> only the PID anchor can save us.
        monkeypatch.setattr(dispatcher.spawn, "close_worker",
                            lambda cfg, handle, session=None: {"killed": 0, "window_closed": False})
        monkeypatch.setattr(dispatcher.spawn, "window_tty", lambda wid: None)
        monkeypatch.setattr(dispatcher.spawn, "pids_on_tty", lambda tty: [])
        # bd reopen: no real hub here
        monkeypatch.setattr(dispatcher.beads, "show",
                            lambda hub, tid: {"id": tid, "status": "in_progress"})
        monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: None)

        class _Args:
            json = True
        rc = cli.cmd_stop(_Args())
        assert rc == 0

        # the recorded PID must be dead now (PID-anchored SIGKILL fired despite no tty).
        # child.poll() reaps the zombie so we observe the real termination (a killed but
        # unreaped child would still answer os.kill(pid,0) to its parent).
        deadline = _t.time() + 3
        exited = False
        while _t.time() < deadline:
            if child.poll() is not None:
                exited = True
                break
            _t.sleep(0.1)
        assert exited, "recorded worker PID survived `orc stop` (tty fallback only)"
        # a SIGKILL shows up as negative returncode -9
        assert child.returncode in (-9, -15), "worker not killed by signal (rc=%s)" % child.returncode
    finally:
        try:
            child.kill()
        except OSError:
            pass
