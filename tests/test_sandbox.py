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


def test_sandbox_gate_fail_closed(monkeypatch):
    # P5: the gate refuses when the sandbox would not be applied, allows when it will be
    # (or an explicit opt-out is set).
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: True)
    assert sandbox.sandbox_gate({"sandbox": True}) == (True, None)          # applied
    monkeypatch.setattr(sandbox, "sandbox_available", lambda: False)
    assert sandbox.sandbox_gate({"sandbox": True}) == (False, "unavailable")  # missing seatbelt
    assert sandbox.sandbox_gate({"sandbox": False}) == (False, "disabled")    # off, no opt-out
    # explicit operator opt-out -> allowed even with no seatbelt
    assert sandbox.sandbox_gate({"sandbox": False, "allow_no_sandbox": True}) == (True, None)
    assert sandbox.sandbox_gate({"sandbox": True, "allow_no_sandbox": True}) == (True, None)


def test_profile_denies_reading_ssh(tmp_path):
    # B2: the profile denies READING ~/.ssh at the syscall level so an obfuscated reader
    # cannot exfiltrate the private key (the profile is otherwise read-allow-default).
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    prof = sandbox.build_profile(ws)
    ssh_real = os.path.realpath(os.path.join(os.path.expanduser("~"), ".ssh"))
    assert '(deny file-read* (subpath "%s"))' % ssh_real in prof


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


# --------------------------------------------------------------------------- #
# Claude runtime scratch (F12 fix): the Bash tool needs ~/.claude/session-env etc.,
# but the worker's OWN enforcement (skills/agents/settings.json) stays non-writable.
# --------------------------------------------------------------------------- #
def test_profile_allows_claude_runtime_scratch(tmp_path):
    prof = sandbox.build_profile(str(tmp_path))
    home_claude = os.path.join(os.path.expanduser("~"), ".claude")
    # the dir whose absence broke the first real F12 shift MUST be writable now
    assert (home_claude + "/session-env") in prof
    assert (home_claude + "/shell-snapshots") in prof


def test_profile_does_not_allow_enforcement_paths(tmp_path):
    prof = sandbox.build_profile(str(tmp_path))
    home_claude = os.path.join(os.path.expanduser("~"), ".claude")
    # a worker must NOT be able to write its own walls: no broad ~/.claude allow, and none
    # of the enforcement subpaths appear as an ALLOWED write subpath.
    assert ('(subpath "%s"))' % home_claude) not in prof   # not the whole ~/.claude
    for enforce in ("/skills", "/agents", "/settings.json", "/CLAUDE.md"):
        assert ('(allow file-write* (subpath "%s%s"))'
                % (home_claude, enforce)) not in prof


def test_profile_read_allows_skills_but_write_denies_them(tmp_path):
    # P2: a pipeline worker must READ the FULL conveyor (~/.claude/skills: SKILL.md +
    # references/ + templates/ + agents/) yet must never WRITE it. Reads are allow-default
    # (no read-deny on skills, unlike ~/.ssh); the write-deny is EXPLICIT and last-wins.
    prof = sandbox.build_profile(str(tmp_path))
    skills = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "skills"))
    # explicit write-deny present ...
    assert ('(deny file-write* (subpath "%s"))' % skills) in prof
    # ... and NOT write-allowed anywhere
    assert ('(allow file-write* (subpath "%s"))' % skills) not in prof
    # ... and NOT read-denied (worker may read the conveyor)
    assert ('(deny file-read* (subpath "%s"))' % skills) not in prof
    # the write-deny for skills must come AFTER the workspace/runtime allows (last rule wins)
    assert prof.index('(deny file-write* (subpath "%s"))' % skills) > prof.index(
        '(allow file-write*\n  (subpath "%s"))' % os.path.realpath(str(tmp_path)))


def test_profile_runtime_paths_are_narrow_not_home(tmp_path):
    # None of the allowed write subpaths may be $HOME or a broad parent of it.
    prof = sandbox.build_profile(str(tmp_path))
    home = os.path.expanduser("~")
    assert ('(subpath "%s"))' % home) not in prof
    # workspace itself is still allowed
    assert ('(subpath "%s"' % os.path.realpath(str(tmp_path))) in prof
