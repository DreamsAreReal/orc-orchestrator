"""Dispatcher-core tests (F4): mutex, preflight (dirty tree), re-validate, reconcile.

Uses real temp git repos for git-dependent checks and monkeypatched beads/spawn so no
real terminal is opened and no real queue is touched. Serialization (G3) is proven by
the mutex: a second same-project task is refused while the first worker is registered.
"""
import os
import sys
import subprocess

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import dispatcher, gitutil, shift as shiftmod  # noqa: E402
from orc import strings as S  # noqa: E402


def _repo(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    # need at least one commit for HEAD/log to exist
    (os.path.join(path, "README.md"))
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("x\n")
    subprocess.run(["git", "-C", path, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], check=True)
    return path


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def test_preflight_clean_repo_ok(tmp_path):
    repo = _repo(str(tmp_path / "clean"))
    ok, reason = dispatcher.preflight(repo)
    assert ok is True and reason is None


def test_preflight_not_a_repo_parks(tmp_path):
    plain = str(tmp_path / "plain")
    os.makedirs(plain)
    ok, reason = dispatcher.preflight(plain)
    assert ok is False and "not a git repository" in reason


def test_preflight_dirty_foreign_parks(tmp_path):
    repo = _repo(str(tmp_path / "dirty"))
    # human's uncommitted edit
    with open(os.path.join(repo, "human_wip.py"), "w") as f:
        f.write("half-written\n")
    ok, reason = dispatcher.preflight(repo)
    assert ok is False and "dirty" in reason and "human_wip.py" in reason


def test_preflight_dirty_only_our_settings_ok(tmp_path):
    repo = _repo(str(tmp_path / "ourdirty"))
    os.makedirs(os.path.join(repo, ".claude"))
    with open(os.path.join(repo, ".claude", "settings.json"), "w") as f:
        f.write("{}\n")
    # only our own artifact is dirty -> still safe to spawn
    ok, reason = dispatcher.preflight(repo)
    assert ok is True and reason is None


def test_preflight_dirty_only_task_state_ok(tmp_path):
    # F12 fix: a prior task's docs/tasks/<slug>/STATE.md (orc's loop-close artifact) left
    # the tree dirty -> a second task on the same project must NOT be parked as human WIP.
    repo = _repo(str(tmp_path / "taskdirty"))
    td = os.path.join(repo, "docs", "tasks", "prior-task")
    os.makedirs(td)
    with open(os.path.join(td, "STATE.md"), "w") as f:
        f.write("Status: DONE\n")
    os.makedirs(os.path.join(repo, ".orc"))
    with open(os.path.join(repo, ".orc", "sandbox.sb"), "w") as f:
        f.write("(version 1)\n")
    ok, reason = dispatcher.preflight(repo)
    assert ok is True and reason is None


def test_preflight_human_edit_still_parks_despite_task_state(tmp_path):
    # the ours-allowance must not mask a real human edit elsewhere in the tree.
    repo = _repo(str(tmp_path / "mixeddirty"))
    td = os.path.join(repo, "docs", "tasks", "prior-task")
    os.makedirs(td)
    with open(os.path.join(td, "STATE.md"), "w") as f:
        f.write("Status: DONE\n")
    with open(os.path.join(repo, "human_wip.py"), "w") as f:
        f.write("half-written\n")
    ok, reason = dispatcher.preflight(repo)
    assert ok is False and "human_wip.py" in reason


# --------------------------------------------------------------------------- #
# re-validate
# --------------------------------------------------------------------------- #
def test_revalidate_notes_product_change(tmp_path):
    repo = _repo(str(tmp_path / "revrepo"))
    os.makedirs(os.path.join(repo, "docs"))
    with open(os.path.join(repo, "docs", "brief.md"), "w") as f:
        f.write("v1\n")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "docs v1"], check=True)
    rev_at_add = gitutil.product_layer_rev(repo)
    task = {"id": "t1", "metadata": {"product_rev": rev_at_add}}
    # unchanged -> no note
    assert dispatcher.revalidate(repo, task) is None
    # now the product layer changes after the task was added
    with open(os.path.join(repo, "docs", "brief.md"), "w") as f:
        f.write("v2 changed scope\n")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "docs v2"], check=True)
    note = dispatcher.revalidate(repo, task)
    assert note is not None and "product layer" in note


def test_revalidate_skips_when_no_baseline(tmp_path):
    repo = _repo(str(tmp_path / "nobaseline"))
    task = {"id": "t1", "metadata": {}}  # older task, no product_rev
    assert dispatcher.revalidate(repo, task) is None


def test_record_revalidate_note_writes_state(tmp_path):
    repo = _repo(str(tmp_path / "noterepo"))
    path = dispatcher.record_revalidate_note(repo, "my-slug", "scope changed")
    assert path and os.path.exists(path)
    assert "scope changed" in open(path).read()
    assert path.endswith(os.path.join("docs", "tasks", "my-slug", "STATE.md"))


# --------------------------------------------------------------------------- #
# mutex / ordering (serialization G3)
# --------------------------------------------------------------------------- #
def test_mutex_refuses_second_same_project(tmp_path, monkeypatch):
    repo = _repo(str(tmp_path / "proj"))
    # allow_no_sandbox: this test mocks the spawn and asserts the mutex, not the sandbox
    # wall (P5 gate is covered by test_spawn_one_fail_closed_* below).
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
           "allow_no_sandbox": True}
    state = shiftmod._empty()

    claimed = []
    spawned = []
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "show", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    # admission (F5): healthy RAM/window so the gate admits (mutex is the thing under test)
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    # F15: spawn_one routes through the backend selector
    monkeypatch.setattr(dispatcher.spawn, "spawn_worker",
                        lambda cfg, p, c, prompt, session=None, deny_network=None:
                        (spawned.append(p) or (True, "tab 1")))
    monkeypatch.setattr(dispatcher.spawn, "worker_pid",
                        lambda cfg, p, session, handle=None: 99999)

    task_a = {"id": "a", "metadata": {"project": repo, "slug": "a", "text": "A"}}
    task_b = {"id": "b", "metadata": {"project": repo, "slug": "b", "text": "B"}}

    ok1, d1, state = dispatcher.spawn_one(cfg, "hub", state, task_a)
    assert ok1 is True and len(state["workers"]) == 1

    # second same-project task must be refused (mutex) -> not claimed, not spawned
    ok2, d2, state = dispatcher.spawn_one(cfg, "hub", state, task_b)
    assert ok2 is False and "mutex" in d2
    assert claimed == ["a"]          # b never claimed
    assert spawned == [repo]          # only one spawn happened


def _spawn_gate_stubs(monkeypatch, spawned, claimed):
    """Common stubs so spawn_one reaches the sandbox gate deterministically."""
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "show", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    monkeypatch.setattr(dispatcher.spawn, "spawn_worker",
                        lambda cfg, p, c, prompt, session=None, deny_network=None:
                        (spawned.append(p) or (True, "t")))
    monkeypatch.setattr(dispatcher.spawn, "worker_pid",
                        lambda cfg, p, session, handle=None: 12345)


def test_spawn_one_fail_closed_when_sandbox_unavailable(tmp_path, monkeypatch):
    """P5: sandbox enabled (default) but seatbelt unavailable -> REFUSE to spawn (fail-closed),
    task parked, never claimed. An unattended worker must never run without its primary wall."""
    from orc import sandbox as sandboxmod
    repo = _repo(str(tmp_path / "proj"))
    claimed, spawned = [], []
    _spawn_gate_stubs(monkeypatch, spawned, claimed)
    monkeypatch.setattr(sandboxmod, "sandbox_available", lambda: False)  # no seatbelt
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal"}  # sandbox default on
    state = shiftmod._empty()
    task = {"id": "s", "metadata": {"project": repo, "slug": "s", "text": "x"}}
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "sandbox-fail-closed" in detail and "unavailable" in detail
    assert claimed == [] and spawned == []              # never claimed, never spawned
    assert any(p["task"] == "s" for p in state["parked"])


def test_spawn_one_fail_closed_when_sandbox_disabled(tmp_path, monkeypatch):
    """P5: sandbox=false without allow_no_sandbox -> REFUSE (fail-closed), parked, not claimed."""
    repo = _repo(str(tmp_path / "proj"))
    claimed, spawned = [], []
    _spawn_gate_stubs(monkeypatch, spawned, claimed)
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
           "sandbox": False}   # explicitly off, but no opt-out
    state = shiftmod._empty()
    task = {"id": "d", "metadata": {"project": repo, "slug": "d", "text": "x"}}
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "sandbox-fail-closed" in detail and "disabled" in detail
    assert claimed == [] and spawned == []
    assert any(p["task"] == "d" for p in state["parked"])


def test_spawn_one_allows_explicit_no_sandbox_optout(tmp_path, monkeypatch):
    """P5: an operator MAY run without the sandbox deliberately (allow_no_sandbox=true) --
    the refusal is a fail-closed default, not a hard ban."""
    from orc import sandbox as sandboxmod
    repo = _repo(str(tmp_path / "proj"))
    claimed, spawned = [], []
    _spawn_gate_stubs(monkeypatch, spawned, claimed)
    monkeypatch.setattr(sandboxmod, "sandbox_available", lambda: False)
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
           "sandbox": False, "allow_no_sandbox": True}   # explicit, recorded opt-out
    state = shiftmod._empty()
    task = {"id": "o", "metadata": {"project": repo, "slug": "o", "text": "x"}}
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is True and spawned == [repo]             # spawns despite no sandbox (opt-out)


def test_spawn_one_parks_dirty_project(tmp_path, monkeypatch):
    repo = _repo(str(tmp_path / "dirtyproj"))
    with open(os.path.join(repo, "wip.txt"), "w") as f:
        f.write("mid-edit\n")
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": []}
    state = shiftmod._empty()
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    claimed = []
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    task = {"id": "d", "metadata": {"project": repo, "slug": "d", "text": "D"}}
    ok, reason, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "dirty" in reason
    assert claimed == []  # dirty tree -> never claimed
    assert any(p["task"] == "d" for p in state["parked"])


# --------------------------------------------------------------------------- #
# reconcile (arbiter: dead PID -> dropped + returned to ready)
# --------------------------------------------------------------------------- #
def test_reconcile_drops_dead_worker(monkeypatch):
    state = shiftmod._empty()
    # pid 999999 almost certainly dead; project empty so worker_pids returns []
    state["workers"] = [{"pid": 999999, "task": "t1", "project": ""}]
    reopened = []
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "status": "in_progress"})
    monkeypatch.setattr(dispatcher.beads, "reopen", lambda hub, tid: reopened.append(tid))
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [])
    state, dropped = dispatcher.reconcile(state, "hub")
    assert dropped == ["t1"] and state["workers"] == []
    assert reopened == ["t1"]  # task returned to ready via lease


def test_reconcile_keeps_live_worker(monkeypatch):
    state = shiftmod._empty()
    live_pid = os.getpid()  # this test process is alive
    state["workers"] = [{"pid": live_pid, "task": "t2", "project": "/p"}]
    state, dropped = dispatcher.reconcile(state, "hub")
    assert dropped == [] and len(state["workers"]) == 1


# --------------------------------------------------------------------------- #
# admission + back-pressure integration (F5): spawn_one respects the gate
# --------------------------------------------------------------------------- #
def _clean_task_cfg(repo):
    return {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
            "min_free_ram_mb": 400, "min_window_minutes": 5}, {
        "id": "z", "metadata": {"project": repo, "slug": "z", "text": "Z"}}


def test_spawn_one_parks_on_low_ram_never_claims(tmp_path, monkeypatch):
    repo = _repo(str(tmp_path / "lowram"))
    cfg, task = _clean_task_cfg(repo)
    state = shiftmod._empty()
    claimed = []
    spawned = []
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher.spawn, "spawn_terminal",
                        lambda *a, **k: spawned.append(1) or (True, "1"))
    # starve RAM below the threshold; window is healthy
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 100)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "admission" in detail and "low-ram" in detail
    assert claimed == [] and spawned == []          # never claimed, never spawned
    assert any(p["task"] == "z" for p in state["parked"])


def test_spawn_one_admits_when_ram_and_window_ok(tmp_path, monkeypatch):
    repo = _repo(str(tmp_path / "okram"))
    cfg, task = _clean_task_cfg(repo)
    state = shiftmod._empty()
    claimed = []
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    monkeypatch.setattr(dispatcher.spawn, "spawn_worker", lambda *a, **k: (True, "42"))
    monkeypatch.setattr(dispatcher.spawn, "worker_pid", lambda *a, **k: 12345)
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is True and claimed == ["z"]
    assert len(state["workers"]) == 1 and state["workers"][0]["tab_id"] == "42"


def test_spawn_one_parks_on_session_limit_string(tmp_path, monkeypatch):
    repo = _repo(str(tmp_path / "limitproj"))
    cfg, task = _clean_task_cfg(repo)
    state = shiftmod._empty()
    claimed = []
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    monkeypatch.setenv("ORC_LIMIT_TEXT",
                       "You've hit your session limit · resets 3:45pm")
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "limit-session" in detail
    assert claimed == []
    assert any("session" in p["reason"] for p in state["parked"])
