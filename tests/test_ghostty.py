"""Ghostty spawner tests (F15): clean window close, session-marker identity, backend route.

The pain: Terminal.app leaves husk windows on worker stop. Ghostty closes a surface when
its `-e` command exits, so we stop a worker by killing its (session-marked) process and the
window self-closes. These tests cover the identity/kill/route logic deterministically (no
real Ghostty window); the live clean-close proof is .verify/ghostty-close.sh.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import spawn_ghostty, spawn, config  # noqa: E402


# --------------------------------------------------------------------------- #
# inner command carries the session marker (the find/kill handle)
# --------------------------------------------------------------------------- #
def test_inner_command_exports_session_marker():
    cmd = spawn_ghostty.build_inner_command("/proj", "/bin/claude", "do it", "task-9")
    assert "ORC_SESSION=task-9" in cmd
    assert "cd /proj" in cmd
    assert "/bin/claude" in cmd


def test_inner_command_seam_override(monkeypatch):
    monkeypatch.setenv("ORC_SPAWN_CMD_OVERRIDE", "sleep 5; exit")
    cmd = spawn_ghostty.build_inner_command("/proj", "/bin/claude", "x", "s1")
    assert "sleep 5; exit" in cmd
    assert "ORC_SESSION=s1" in cmd
    assert "/bin/claude" not in cmd          # override replaces the claude launch


def test_session_marker_is_stable():
    assert spawn_ghostty._session_marker("abc") == "ORC_SESSION=abc"


# --------------------------------------------------------------------------- #
# find / stop by session marker
# --------------------------------------------------------------------------- #
def test_worker_pids_by_session_parses_pgrep(monkeypatch):
    import subprocess

    class R:
        stdout = "111\n222\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert spawn_ghostty.worker_pids_by_session("s") == [111, 222]


def test_pid_for_session_returns_first(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "worker_pids_by_session", lambda s: [777, 888])
    assert spawn_ghostty.pid_for_session("s") == 777
    monkeypatch.setattr(spawn_ghostty, "worker_pids_by_session", lambda s: [])
    assert spawn_ghostty.pid_for_session("s") is None


def test_close_ghostty_kills_and_reports_window_closed(monkeypatch):
    killed = []
    # first call: one live pid; after kill: none left -> window_closed True
    calls = {"n": 0}

    def fake_pids(session):
        calls["n"] += 1
        return [4242] if calls["n"] == 1 else []
    monkeypatch.setattr(spawn_ghostty, "worker_pids_by_session", fake_pids)
    monkeypatch.setattr(spawn_ghostty.os, "kill",
                        lambda pid, sig: killed.append((pid, sig)))
    result = spawn_ghostty.close_ghostty("s1")
    assert result["killed"] == 1
    assert result["window_closed"] is True           # no marked process left = window gone
    assert killed[0][0] == 4242


def test_close_ghostty_empty_session_is_noop():
    assert spawn_ghostty.close_ghostty("") == {"killed": 0, "window_closed": False}


# --------------------------------------------------------------------------- #
# backend selector: ghostty default, terminal fallback
# --------------------------------------------------------------------------- #
def test_backend_defaults_to_ghostty_when_available(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "ghostty_available", lambda: True)
    assert spawn._backend({}) == "ghostty"
    assert spawn._backend({"terminal": "ghostty"}) == "ghostty"


def test_backend_falls_back_to_terminal_when_ghostty_missing(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "ghostty_available", lambda: False)
    # ghostty requested but not installed -> terminal so spawns still work
    assert spawn._backend({"terminal": "ghostty"}) == "terminal"


def test_backend_explicit_terminal():
    assert spawn._backend({"terminal": "terminal"}) == "terminal"


def test_spawn_worker_routes_to_ghostty(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "ghostty_available", lambda: True)
    monkeypatch.setattr(spawn_ghostty, "spawn_ghostty",
                        lambda p, c, pr, s: (True, "ORC_SESSION=%s" % s))
    ok, handle = spawn.spawn_worker({"terminal": "ghostty"}, "/p", "/c", "pr", "t1")
    assert ok and handle == "ORC_SESSION=t1"


def test_close_worker_routes_to_ghostty(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "ghostty_available", lambda: True)
    seen = {}

    def _close(session):
        seen["s"] = session
        return {"killed": 1, "window_closed": True}
    monkeypatch.setattr(spawn_ghostty, "close_ghostty", _close)
    r = spawn.close_worker({"terminal": "ghostty"}, "handle", session="t1")
    assert r["window_closed"] is True and seen["s"] == "t1"


def test_worker_pid_routes_to_ghostty(monkeypatch):
    monkeypatch.setattr(spawn_ghostty, "ghostty_available", lambda: True)
    monkeypatch.setattr(spawn_ghostty, "pid_for_session", lambda s: 9090)
    assert spawn.worker_pid({"terminal": "ghostty"}, "/p", "t1") == 9090


def test_terminal_is_the_config_default():
    # R-M2 fix: the shipped default backend is Terminal.app because it reliably EXECUTES
    # the worker command. Ghostty 1.3.1 opens an empty window (`-e` never spawns the shell,
    # proven in .spikes/probe/ghostty-exec.md), so it must NOT be the default -- a default
    # that opens empty windows is worse than the husk it tried to fix. Ghostty stays opt-in.
    assert config.DEFAULTS["terminal"] == "terminal"
