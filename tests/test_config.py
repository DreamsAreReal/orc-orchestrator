"""F10 config + LaunchAgent + kill switch + Terminal-profile setup tests.

Acceptance (features.md F10):
  - all calibrations come from config.json (no hard-coded threshold);
  - the LaunchAgent plist is Aqua, calls claude by ABSOLUTE PATH, sets PATH (not inherited);
  - `orc stop` stops workers and returns their tasks to ready (bounded);
  - `orc setup` sets the Terminal profile shellExitAction to 0 with a reversible backup.

Deterministic: no real terminal, LaunchAgent, or claude is touched here (the live proof is
.verify/launchagent.sh). These lock the pure logic against regression.
"""
import os
import sys
import json
import plistlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import config, launchagent, terminal_profile as tp  # noqa: E402
from orc import cli, shift as shiftmod, dispatcher  # noqa: E402


# --------------------------------------------------------------------------- #
# config.json is the single source of calibration truth (no hard-code)
# --------------------------------------------------------------------------- #
def test_defaults_expose_all_f10_knobs():
    for knob in ("launchagent_label", "launchagent_path", "stop_grace_seconds",
                 "poll_interval_seconds", "terminal_profile", "claude_bin",
                 "min_free_ram_mb", "min_window_minutes", "restart_cap",
                 "lease_ttl_seconds"):
        assert knob in config.DEFAULTS, knob


def test_config_json_overrides_defaults(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    os.makedirs(home)
    with open(home / "config.json", "w") as f:
        json.dump({"stop_grace_seconds": 9, "min_free_ram_mb": 777,
                   "launchagent_label": "com.user.custom"}, f)
    cfg = config.load()
    assert cfg["stop_grace_seconds"] == 9          # override, not the default 5
    assert cfg["min_free_ram_mb"] == 777           # override, not the default 400
    assert cfg["launchagent_label"] == "com.user.custom"
    # unspecified knobs still fall back to defaults
    assert cfg["min_window_minutes"] == config.DEFAULTS["min_window_minutes"]


# --------------------------------------------------------------------------- #
# P2: network policy -- a convenient open/deny choice + per-task --offline
# --------------------------------------------------------------------------- #
def test_network_policy_default_open():
    assert config.DEFAULTS["network_policy"] == "open"
    # the default resolves to network NOT denied (workers need the claude API / installs)
    assert config.network_deny(config.DEFAULTS) is False


def test_network_policy_deny_cuts_network():
    assert config.network_deny({"network_policy": "deny"}) is True
    # case-insensitive
    assert config.network_deny({"network_policy": "DENY"}) is True


def test_deprecated_sandbox_deny_network_alias_honoured():
    # back-compat: the old boolean still cuts the network (alias of network_policy=deny)
    assert config.network_deny({"sandbox_deny_network": True}) is True
    assert config.network_deny({"sandbox_deny_network": False}) is False


def test_per_task_offline_forces_deny_even_when_policy_open():
    # `orc add --offline` tightens a single task to deny regardless of the shift policy
    assert config.network_deny({"network_policy": "open"}, task_offline=True) is True


def test_offline_only_tightens_never_loosens():
    # an absent offline flag under an open policy stays open; a deny policy stays deny
    assert config.network_deny({"network_policy": "open"}, task_offline=False) is False
    assert config.network_deny({"network_policy": "deny"}, task_offline=False) is True


def test_malformed_config_falls_back_to_defaults(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    os.makedirs(home)
    (home / "config.json").write_text("{ this is not json")
    cfg = config.load()  # must never crash the dispatcher
    assert cfg["stop_grace_seconds"] == config.DEFAULTS["stop_grace_seconds"]


def test_write_default_config_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    path, created = config.write_default_config()
    assert created is True and os.path.exists(path)
    path2, created2 = config.write_default_config()
    assert created2 is False and path2 == path


# --------------------------------------------------------------------------- #
# LaunchAgent plist: Aqua session, PATH set, claude by absolute path
# --------------------------------------------------------------------------- #
def test_plist_is_aqua_with_path_and_absolute_program(tmp_path, monkeypatch):
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    cfg = config.load()
    d = launchagent.build_plist_dict(cfg)
    # Aqua = GUI session (the only context with Keychain / working `claude auth`).
    assert d["LimitLoadToSessionType"] == "Aqua"
    # LaunchAgents do NOT inherit the shell PATH -> it must be set explicitly.
    assert "PATH" in d["EnvironmentVariables"]
    assert "/opt/homebrew/bin" in d["EnvironmentVariables"]["PATH"]
    # program is referenced by absolute path (no reliance on cwd/PATH for the program).
    assert d["ProgramArguments"][1].endswith("/bin/orc")
    assert os.path.isabs(d["ProgramArguments"][1])
    assert d["RunAtLoad"] is True
    # KeepAlive only on crash, so a clean `orc stop` truly stops the daemon.
    assert d["KeepAlive"] == {"Crashed": True}


def test_plist_label_comes_from_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    os.makedirs(home)
    with open(home / "config.json", "w") as f:
        json.dump({"launchagent_label": "com.user.orc-xyz"}, f)
    cfg = config.load()
    d = launchagent.build_plist_dict(cfg)
    assert d["Label"] == "com.user.orc-xyz"
    assert launchagent.plist_path(cfg).endswith("com.user.orc-xyz.plist")


def test_claude_referenced_by_absolute_path(tmp_path, monkeypatch):
    # probe finding: claude is NOT on the LaunchAgent PATH by default -> call it absolutely.
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    cfg = config.load()
    assert os.path.isabs(cfg["claude_bin"])


def test_write_plist_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setenv("ORC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))  # redirect ~/Library/LaunchAgents
    cfg = config.load()
    path = launchagent.write_plist(cfg)
    assert os.path.exists(path)
    with open(path, "rb") as f:
        d = plistlib.load(f)
    assert d["Label"] == cfg["launchagent_label"]
    assert d["LimitLoadToSessionType"] == "Aqua"


# --------------------------------------------------------------------------- #
# `orc stop`: stops workers, returns tasks to ready, bounded (SIGKILL fallback)
# --------------------------------------------------------------------------- #
class _Args:
    json = True


def test_stop_no_workers(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    monkeypatch.setenv("ORC_HUB", str(home))
    os.makedirs(home)
    shiftmod.save(shiftmod._empty())
    rc = cli.cmd_stop(_Args())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stopped"] == []


def test_stop_kills_workers_and_requeues(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    monkeypatch.setenv("ORC_HUB", str(home))
    os.makedirs(home)
    # one registered worker in shift.json
    st = shiftmod._empty()
    shiftmod.add_worker(st, pid=4242, session="t1", project="/p", task="t1", tab_id="99")
    shiftmod.save(st)

    closed = []
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, handle, session=None: closed.append((handle, session)))
    # no live processes on the (fake) tty -> stop returns promptly, no SIGKILL needed
    monkeypatch.setattr(dispatcher.spawn, "window_tty", lambda h: "/dev/ttyFAKE")
    monkeypatch.setattr(dispatcher.spawn, "pids_on_tty", lambda t: [])
    # bd: task is open (not closed) -> it must be reopened (returned to ready)
    reopened = []
    monkeypatch.setattr(cli.beads, "show", lambda hub, tid: {"id": tid, "status": "in_progress"})
    monkeypatch.setattr(cli.beads, "reopen", lambda hub, tid: reopened.append(tid))

    rc = cli.cmd_stop(_Args())
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stopped"] == ["t1"]
    assert out["requeued"] == ["t1"]
    assert closed == [("99", "t1")]              # worker was closed via the backend
    assert reopened == ["t1"]                     # task returned to ready
    # shift.json is reset (no lingering worker records)
    assert shiftmod.load().get("workers") == []


def test_stop_does_not_reopen_already_closed_task(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("ORC_HOME", str(home))
    monkeypatch.setenv("ORC_HUB", str(home))
    os.makedirs(home)
    st = shiftmod._empty()
    shiftmod.add_worker(st, pid=1, session="done1", project="/p", task="done1", tab_id="1")
    shiftmod.save(st)
    monkeypatch.setattr(dispatcher.spawn, "close_worker", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.spawn, "window_tty", lambda h: None)
    monkeypatch.setattr(dispatcher.spawn, "pids_on_tty", lambda t: [])
    reopened = []
    monkeypatch.setattr(cli.beads, "show", lambda hub, tid: {"id": tid, "status": "closed"})
    monkeypatch.setattr(cli.beads, "reopen", lambda hub, tid: reopened.append(tid))
    cli.cmd_stop(_Args())
    out = json.loads(capsys.readouterr().out)
    assert out["stopped"] == ["done1"]
    assert reopened == []                          # a closed task is NOT reopened


# --------------------------------------------------------------------------- #
# `orc setup`: Terminal profile shellExitAction=0 with a reversible backup
# --------------------------------------------------------------------------- #
def _fake_terminal_plist(tmp_path, profile="Clear Dark", exit_action=2):
    path = tmp_path / "com.apple.Terminal.plist"
    data = {
        "Default Window Settings": profile,
        "Startup Window Settings": profile,
        "Window Settings": {profile: {"name": profile, "shellExitAction": exit_action}},
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)
    return str(path)


def test_resolve_profile_prefers_requested_then_default():
    data = {"Default Window Settings": "Pro",
            "Startup Window Settings": "Basic",
            "Window Settings": {"Pro": {}, "Basic": {}, "Clear Dark": {}}}
    assert tp.resolve_profile(data, requested="Clear Dark") == "Clear Dark"
    assert tp.resolve_profile(data, requested=None) == "Pro"
    assert tp.resolve_profile(data, requested="Nonexistent") == "Pro"  # falls to default
    assert tp.resolve_profile({"Window Settings": {}}, requested=None) is None


def test_setup_sets_close_on_exit_with_backup(tmp_path):
    path = _fake_terminal_plist(tmp_path, exit_action=2)  # 2 = keep window (husk cause)
    r = tp.set_close_on_exit(path, "Clear Dark")
    assert r["changed"] is True and r["old"] == 2
    d = tp._load(path)
    prof = d["Window Settings"]["Clear Dark"]
    assert prof["shellExitAction"] == 0            # now closes on exit (no husk)
    assert prof["orcPrevShellExitAction"] == 2     # previous value backed up


def test_setup_is_idempotent_when_already_zero(tmp_path):
    path = _fake_terminal_plist(tmp_path, exit_action=0)
    r = tp.set_close_on_exit(path, "Clear Dark")
    assert r["changed"] is False and r["old"] == 0
    # no backup key added when nothing changed
    assert "orcPrevShellExitAction" not in tp._load(path)["Window Settings"]["Clear Dark"]


def test_setup_revert_restores_previous_value(tmp_path):
    path = _fake_terminal_plist(tmp_path, exit_action=2)
    tp.set_close_on_exit(path, "Clear Dark")
    r = tp.revert(path, "Clear Dark")
    assert r["reverted"] is True and r["restored"] == 2
    prof = tp._load(path)["Window Settings"]["Clear Dark"]
    assert prof["shellExitAction"] == 2            # restored
    assert "orcPrevShellExitAction" not in prof    # backup consumed
