"""F14 tests: close the loop -- completion detection + newspaper catches up to DONE.

The consumer M1 report failed the checkpoint because a task was DONE on disk (STATE.md +
file) while `orc status --newspaper` still showed "0 done / in progress": the interactive
worker lingers and the dispatcher never detected completion. F14 makes the dispatcher poll
the task STATE.md and react to a terminal status. These tests cover the pure detector, the
poll transition (done -> bd close + shift.done + tab close; gate -> park), and the spawn
plumbing that records a real Terminal window id (fixing the `pid None` display).

STATE.md fixtures mirror the real (Russian) pipeline format; the Cyrillic literals below
are written as \\u escapes so this source file stays ASCII (EN-only code policy):
_STATUS is the "Status" field label, _PHASE the "Phase" label, _INPROG "in progress",
_GATE_WAIT "waits for the user's answer".

beads/spawn are monkeypatched so no real queue or terminal is touched (fast + hermetic).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import dispatcher, shift as shiftmod  # noqa: E402


# --- Russian STATE.md literals as ASCII \u escapes (matched DATA, not code) ----
_STATUS = "\u0421\u0442\u0430\u0442\u0443\u0441"
_PHASE = "\u0424\u0430\u0437\u0430"
_INPROG = "\u0432 \u0440\u0430\u0431\u043e\u0442\u0435"
_ARROW = "\u2192"
_GATE_WAIT = "\u0436\u0434\u0451\u0442 \u043e\u0442\u0432\u0435\u0442\u0430 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f"

DONE_REAL = (
    "# STATE - Create HELLO.txt\n"
    "- **%s**: 5 VERIFY %s DONE\n"
    "- **%s**: DONE\n" % (_PHASE, _ARROW, _STATUS)
)
IN_PROGRESS = "- **%s**: 4 BUILD\n- **%s**: %s\n" % (_PHASE, _STATUS, _INPROG)
GATE_STATE = "- **%s**: parked-on-gate\n- Gate: %s\n" % (_STATUS, _GATE_WAIT)
GATE_OUTRANKS = "- **%s**: parked-on-gate\nDONE-WAVE-1 also mentioned" % _STATUS


# --- pure detector: the real pipeline STATE.md formats ------------------------

@pytest.mark.parametrize("text,expected", [
    (DONE_REAL, "done"),
    ("- **%s**: DONE-WAVE-2" % _STATUS, "done"),
    ("- **%s**: BETA" % _STATUS, "done"),
    ("some phase note: 5 VERIFY -> DONE", "done"),   # no status field, phase line only
    (IN_PROGRESS, None),
    (GATE_STATE, "gate"),
    (GATE_OUTRANKS, "gate"),                          # gate marker outranks a DONE token
    ("", None),
    (None, None),
    ("nothing terminal here", None),
])
def test_detect_terminal_status(text, expected):
    assert dispatcher.detect_terminal_status(text) == expected


# --- poll_completions: the loop actually closes -------------------------------

def _write_state(project, slug, body):
    d = os.path.join(project, "docs", "tasks", slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "STATE.md"), "w", encoding="utf-8") as f:
        f.write(body)


def _register(project, slug, task_id, tab_id):
    state = shiftmod._empty()
    shiftmod.add_worker(state, pid=1234, session="window id %s" % tab_id,
                        project=project, task=task_id, tab_id=tab_id)
    return state


def _real_deliverable(project):
    """Create a real deliverable AFTER the worker started so the B1 external-fact gate
    (a DONE claim is trusted only with real progress on disk) sees genuine work. A worker
    that only wrote its own docs/tasks/STATE.md would NOT pass this -- see
    test_poll_done_without_external_fact_is_parked below."""
    with open(os.path.join(project, "deliverable.txt"), "w") as f:
        f.write("real work product\n")


def test_poll_no_state_leaves_worker_running(tmp_path, monkeypatch):
    proj = str(tmp_path)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    state = _register(proj, "slug1", "t-1", "5000")
    state, tr = dispatcher.poll_completions(state, "hub")
    assert tr == []
    assert len(state["workers"]) == 1


def test_poll_done_closes_loop(tmp_path, monkeypatch):
    proj = str(tmp_path)
    _write_state(proj, "slug1", DONE_REAL)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    closed_bd = []
    closed_win = []
    monkeypatch.setattr(dispatcher.beads, "close",
                        lambda hub, tid: closed_bd.append(tid) or True)
    # F15: the dispatcher now closes via the backend router close_worker(cfg, handle, session)
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, wid, session=None: closed_win.append(wid) or True)

    state = _register(proj, "slug1", "t-1", "5000")
    # B1: a DONE claim is honored only with a real external fact. The worker produced a
    # genuine deliverable after it started -> external_progress passes -> loop closes.
    _real_deliverable(proj)
    state, tr = dispatcher.poll_completions(state, "hub")

    assert tr == [("t-1", "done")]
    assert closed_bd == ["t-1"]            # bd task closed
    assert closed_win == ["5000"]          # worker's window closed by saved handle
    assert state["workers"] == []          # no longer active
    assert [d["task"] for d in state["done"]] == ["t-1"]   # shows in the newspaper


def test_poll_done_without_external_fact_is_parked(tmp_path, monkeypatch):
    """B1 reward-hacking gate: a worker that writes STATE.md=DONE but produced NO external
    fact (no commit, no changed/created deliverable -- only its own orc-managed STATE.md)
    must NOT close the task. It is parked 'suspected fake-done' and its bd task is blocked,
    not closed. This is the Replit-class incident named in the brief (G1: DONE confirmed by
    external facts, never the worker's self-report)."""
    proj = str(tmp_path)
    _write_state(proj, "slug1", DONE_REAL)   # only the orc-managed STATE.md, nothing else
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    closed_bd = []
    blocked = []
    closed_win = []
    monkeypatch.setattr(dispatcher.beads, "close",
                        lambda hub, tid: closed_bd.append(tid) or True)
    monkeypatch.setattr(dispatcher.beads, "set_status",
                        lambda hub, tid, st: blocked.append((tid, st)) or True)
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, wid, session=None: closed_win.append(wid) or True)
    # A true fake-done is a worker that has EXITED having produced nothing (a still-alive
    # worker may just be mid-write -- see test_poll_done_live_worker_no_fact_not_parked).
    monkeypatch.setattr(dispatcher, "_pid_alive", lambda pid: False)

    state = _register(proj, "slug1", "t-1", "5000")
    state, tr = dispatcher.poll_completions(state, "hub")

    assert tr == [("t-1", "suspected-fake-done")]
    assert closed_bd == []                          # bd task NOT closed
    assert ("t-1", "blocked") in blocked            # bd task blocked instead
    assert closed_win == []                         # worker window kept for inspection
    assert [d["task"] for d in state.get("done", [])] == []          # not "done"
    assert [p["task"] for p in state.get("parked", [])] == ["t-1"]   # parked for the operator


@pytest.mark.parametrize("trick", ["empty_touch", "allow_empty_commit"])
def test_poll_done_parks_trivial_bypasses(tmp_path, monkeypatch, trick):
    """B1 cycle-2: the two trivial bypasses the reverify found -- an EMPTY `touch out.txt`
    and a `git commit --allow-empty` (0 diff) -- are token imitations of a fact, not facts.
    poll_completions must PARK them (suspected fake-done), not close the task."""
    import subprocess
    import time as _t
    proj = str(tmp_path)
    subprocess.run(["git", "init", "-q", proj], check=True)
    subprocess.run(["git", "-C", proj, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    _write_state(proj, "slug1", DONE_REAL)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    monkeypatch.setattr(dispatcher.beads, "close", lambda hub, tid: pytest.fail("closed a fake"))
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda hub, tid, st: True)
    monkeypatch.setattr(dispatcher.spawn, "close_worker", lambda cfg, wid, session=None: True)
    # trivial-bypass fake-done is judged only after the worker has exited (dead PID).
    monkeypatch.setattr(dispatcher, "_pid_alive", lambda pid: False)

    state = _register(proj, "slug1", "t-1", "5000")
    _t.sleep(1.1)   # the worker's started_epoch is now < the trick's timestamp
    if trick == "empty_touch":
        subprocess.run(["touch", os.path.join(proj, "out.txt")], check=True)
    else:
        subprocess.run(["git", "-C", proj, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-q", "-m", "done"], check=True)

    state, tr = dispatcher.poll_completions(state, "hub")
    assert tr == [("t-1", "suspected-fake-done")]
    assert [p["task"] for p in state.get("parked", [])] == ["t-1"]


def test_poll_done_live_worker_no_fact_not_parked_then_closes(tmp_path, monkeypatch):
    """B1 race fix (found LIVE in the G1 pipeline run): a worker wrote STATE.md=DONE ~4s
    BEFORE it flushed its real deliverable to disk. The poll fired in that gap and the old
    code parked a real, working deliverable as 'suspected fake-done' -- permanently, since a
    parked task is dropped from `workers` and never re-polled. The fix: a STILL-ALIVE worker
    with no external fact yet is NOT-YET-DONE (left in `workers`, re-polled next tick), NOT a
    fake-done. Only a DEAD worker that produced nothing is the true reward-hack. This does
    NOT weaken the wall: the deliverable is still required before the task closes."""
    proj = str(tmp_path)
    _write_state(proj, "slug1", DONE_REAL)   # DONE claimed, but no deliverable on disk yet
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    monkeypatch.setattr(dispatcher.beads, "close",
                        lambda hub, tid: pytest.fail("closed without an external fact"))
    monkeypatch.setattr(dispatcher.beads, "set_status",
                        lambda hub, tid, st: pytest.fail("parked a still-live worker"))
    monkeypatch.setattr(dispatcher.spawn, "close_worker", lambda cfg, wid, session=None: True)
    # tick 1: the worker is ALIVE and has produced no external fact yet.
    monkeypatch.setattr(dispatcher, "_pid_alive", lambda pid: True)

    state = _register(proj, "slug1", "t-1", "5000")
    state, tr = dispatcher.poll_completions(state, "hub")
    assert tr == []                                   # no transition: not-yet-done
    assert [w["task"] for w in state["workers"]] == ["t-1"]   # still active, will re-poll
    assert state.get("parked", []) == []              # NOT parked as fake

    # tick 2: the worker has now flushed the real deliverable -> the loop closes normally.
    closed_bd = []
    monkeypatch.setattr(dispatcher.beads, "close",
                        lambda hub, tid: closed_bd.append(tid) or True)
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda hub, tid, st: True)
    _real_deliverable(proj)
    state, tr = dispatcher.poll_completions(state, "hub")
    assert tr == [("t-1", "done")]
    assert closed_bd == ["t-1"]
    assert [d["task"] for d in state["done"]] == ["t-1"]


def test_poll_gate_parks_and_keeps_window(tmp_path, monkeypatch):
    proj = str(tmp_path)
    _write_state(proj, "slug1", GATE_STATE)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    closed_win = []
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, wid, session=None: closed_win.append(wid) or True)
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda hub, tid, st: True)
    # F9: the gate branch also fires a notification; stub it here (tested in test_gate.py)
    monkeypatch.setattr(dispatcher, "_notify_gate", lambda cfg, hub, tid: True)

    state = _register(proj, "slug1", "t-1", "5000")
    state, tr = dispatcher.poll_completions(state, "hub")

    assert tr == [("t-1", "gate")]
    assert closed_win == []                 # gate window stays for the operator (F9)
    assert state["workers"] == []
    assert [p["task"] for p in state["parked"]] == ["t-1"]


def test_poll_bd_error_still_repairs_shift(tmp_path, monkeypatch):
    """bd close failing must not strand the worker as 'active' -- shift.json is repaired
    so the newspaper reflects reality (bd wins as truth, shift.json is fixed)."""
    proj = str(tmp_path)
    _write_state(proj, "slug1", DONE_REAL)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")

    def _boom(hub, tid):
        raise dispatcher.beads.BeadsError("bd down")
    monkeypatch.setattr(dispatcher.beads, "close", _boom)
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, wid, session=None: True)

    state = _register(proj, "slug1", "t-1", "5000")
    # B1: real external fact so the DONE is honored (the point of this test is the bd-error
    # repair, not the reward-hacking gate).
    _real_deliverable(proj)
    state, tr = dispatcher.poll_completions(state, "hub")

    assert tr == [("t-1", "done")]
    assert state["workers"] == []
    assert [d["task"] for d in state["done"]] == ["t-1"]


# --- spawn plumbing: a real window id, not None -------------------------------

def test_spawn_records_window_id_not_none(tmp_path, monkeypatch):
    """spawn_one must store the Terminal window id as tab_id (fixes consumer `pid None`)."""
    proj = str(tmp_path)
    # minimal git repo so preflight passes
    import subprocess
    subprocess.run(["git", "init", "-q", proj], check=True)
    subprocess.run(["git", "-C", proj, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)

    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: True)
    monkeypatch.setattr(dispatcher.beads, "show", lambda hub, tid: None)
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "prepare_worker_walls", lambda cfg, p: ("x", False))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 0)
    monkeypatch.setattr(dispatcher, "revalidate", lambda p, t: None)
    # admission (F5): keep RAM/window healthy so the gate admits deterministically
    monkeypatch.setattr(dispatcher.probes, "free_ram_mb", lambda: 4000)
    monkeypatch.setattr(dispatcher.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 200})
    # F15: spawn_one routes through the backend selector; mock spawn_worker + worker_pid
    monkeypatch.setattr(dispatcher.spawn, "spawn_worker",
                        lambda cfg, project, cbin, prompt, session=None, deny_network=None:
                        (True, "4242"))
    monkeypatch.setattr(dispatcher.spawn, "worker_pid",
                        lambda cfg, project, session, handle=None: 55555)

    # allow_no_sandbox: this test mocks the actual spawn; the sandbox fail-closed gate (P5)
    # is exercised in test_dispatcher, not here, so opt out to stay machine-independent.
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "terminal": "terminal",
           "allow_no_sandbox": True}
    task = {"id": "t-1", "metadata": {"project": proj, "slug": "s", "text": "x"}}
    state = shiftmod._empty()
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)

    assert ok
    w = state["workers"][0]
    assert w["tab_id"] == "4242"            # window id recorded, not None
    assert "4242" in detail                 # user-facing line shows a real id
