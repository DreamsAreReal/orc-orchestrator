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
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": []}
    state = shiftmod._empty()

    claimed = []
    spawned = []
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "show", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    monkeypatch.setattr(dispatcher.spawn, "spawn_terminal",
                        lambda p, c, prompt: (spawned.append(p) or (True, "tab 1")))
    monkeypatch.setattr(dispatcher.spawn, "worker_pids", lambda p: [99999])

    task_a = {"id": "a", "metadata": {"project": repo, "slug": "a", "text": "A"}}
    task_b = {"id": "b", "metadata": {"project": repo, "slug": "b", "text": "B"}}

    ok1, d1, state = dispatcher.spawn_one(cfg, "hub", state, task_a)
    assert ok1 is True and len(state["workers"]) == 1

    # second same-project task must be refused (mutex) -> not claimed, not spawned
    ok2, d2, state = dispatcher.spawn_one(cfg, "hub", state, task_b)
    assert ok2 is False and "mutex" in d2
    assert claimed == ["a"]          # b never claimed
    assert spawned == [repo]          # only one spawn happened


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
