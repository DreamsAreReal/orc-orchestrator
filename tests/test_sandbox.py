"""F13 OS-sandbox (macOS seatbelt) tests.

The seatbelt profile is the PRIMARY wall over the F1 pattern-hook: it denies file writes
outside the task workspace at the syscall level, so obfuscated escapes (base64|bash rm,
python rmtree, find -delete) are blocked regardless of how the write was reached. These
tests lock the profile SHAPE and the command wrapping. The live OS-enforcement proof (the
escapes actually being blocked by the kernel) is .verify/sandbox-walls.sh.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import sandbox, spawn, config  # noqa: E402


# --------------------------------------------------------------------------- #
# profile shape: deny-all writes, allow ONLY the narrow workspace subpath
# --------------------------------------------------------------------------- #
def test_profile_denies_writes_and_allows_only_workspace(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    prof = sandbox.build_profile(ws)
    assert "(deny file-write*)" in prof
    # the workspace is re-allowed as a NARROW subpath (not a broad parent) -- the spike's
    # key finding: whitelisting a broad parent (e.g. /private/tmp) leaks.
    assert '(subpath "%s")' % os.path.realpath(ws) in prof
    # device sinks the shell needs remain writable
    assert '(literal "/dev/null")' in prof
    # default: network is NOT denied (workers need claude API / git fetch / brew)
    assert "(deny network*)" not in prof


def test_profile_does_not_whitelist_a_broad_parent(tmp_path):
    ws = str(tmp_path / "proj")
    os.makedirs(ws)
    prof = sandbox.build_profile(ws)
    # the profile must NOT contain a bare /tmp or /private/tmp or $HOME write-allow -- only
    # the specific workspace subpath (guarding against the over-broad-allowlist trap).
    for broad in ('(subpath "/tmp")', '(subpath "/private/tmp")',
                  '(subpath "%s")' % os.path.expanduser("~")):
        assert broad not in prof


def test_profile_deny_network_when_requested(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    prof = sandbox.build_profile(ws, deny_network=True)
    assert "(deny network*)" in prof


def test_extra_write_subpaths_are_scoped(tmp_path):
    ws = str(tmp_path / "ws")
    extra = str(tmp_path / "cache")
    os.makedirs(ws)
    os.makedirs(extra)
    prof = sandbox.build_profile(ws, extra_write_subpaths=[extra])
    assert '(subpath "%s")' % os.path.realpath(extra) in prof


def test_write_profile_lands_inside_workspace(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    path = sandbox.write_profile(ws)
    # profile lives under the workspace's own .orc dir (inside the sole writable subpath)
    assert os.path.exists(path)
    assert os.path.realpath(path).startswith(os.path.realpath(ws))
    with open(path) as f:
        assert "(deny file-write*)" in f.read()


# --------------------------------------------------------------------------- #
# wrap_command builds the sandbox-exec invocation
# --------------------------------------------------------------------------- #
def test_wrap_command_uses_sandbox_exec():
    wrapped = sandbox.wrap_command("/x/profile.sb", "cd /proj && echo hi")
    assert wrapped.startswith("/usr/bin/sandbox-exec -f ")
    assert "profile.sb" in wrapped
    assert "bash -lc" in wrapped
    # the inner command is preserved (quoted) inside the wrapper
    assert "cd /proj && echo hi" in wrapped


# --------------------------------------------------------------------------- #
# build_start_command wraps the worker command under the sandbox by default (F13)
# --------------------------------------------------------------------------- #
def test_start_command_wrapped_by_default(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)
    cmd = spawn.build_start_command(proj, "/bin/claude", "do it", session="t1", cfg={})
    assert cmd.startswith("/usr/bin/sandbox-exec -f ")   # sandboxed
    # the real worker command is still inside (cd + claude + session export)
    assert "ORC_SESSION=t1" in cmd
    assert "/bin/claude" in cmd


def test_start_command_not_wrapped_when_disabled(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)
    cmd = spawn.build_start_command(proj, "/bin/claude", "do it",
                                    session="t1", cfg={"sandbox": False})
    assert not cmd.startswith("/usr/bin/sandbox-exec")   # opt-out honoured
    assert "/bin/claude" in cmd


def test_start_command_falls_back_when_seatbelt_absent(tmp_path, monkeypatch):
    proj = str(tmp_path / "proj")
    os.makedirs(proj)
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: False)
    cmd = spawn.build_start_command(proj, "/bin/claude", "x", session="t1", cfg={})
    # no seatbelt on the machine -> run unwrapped rather than fail to spawn
    assert not cmd.startswith("/usr/bin/sandbox-exec")


def test_sandbox_default_on_in_config():
    assert config.DEFAULTS["sandbox"] is True
    assert config.DEFAULTS["sandbox_deny_network"] is False
