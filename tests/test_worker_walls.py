"""Unit tests for orc.worker_walls: wall detection + settings generator (merge/env/MCP)."""
import os
import sys
import json
import subprocess

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import worker_walls as W  # noqa: E402
from orc import strings as S  # noqa: E402


# --------------------------------------------------------------------------- #
# Command inspection: git push / rm outside / ssh read
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd", [
    "git push",
    "git push origin main",
    "git push --force origin main",
    "git -C /repo push",
    "cd x && git push",
])
def test_git_push_blocked(cmd):
    assert W._inspect_bash(cmd, "/ws") == S.WALL_GIT_PUSH


@pytest.mark.parametrize("cmd", [
    "git status",
    "git commit -m x",
    "git commit -m 'push it real good'",  # 'push' inside a quoted arg → NOT a push
    "git pushd",  # 'pushd' is a distinct token, not the push subcommand
    "echo git push",  # not a git invocation at all (echo)
    "git status && echo push",  # 'push' after a separator, not a git subcommand
])
def test_git_non_push_allowed(cmd):
    assert W._inspect_bash(cmd, "/ws") is None


def test_rm_outside_workspace_blocked(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    reason = W._inspect_bash("rm -rf /Users/admin/Desktop/other", ws)
    assert reason is not None and "workspace" in reason


def test_rm_inside_workspace_allowed(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    assert W._inspect_bash("rm -rf " + os.path.join(ws, "build"), ws) is None
    # relative target resolves against workspace
    assert W._inspect_bash("rm -rf build/tmp", ws) is None


def test_rm_non_recursive_ignored(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    # plain rm of a single file outside is not the catastrophic wall we gate here
    assert W._inspect_bash("rm /tmp/x.txt", ws) is None


def test_rm_home_blocked(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    assert W._inspect_bash("rm -rf ~/Documents", ws) is not None


@pytest.mark.parametrize("cmd", [
    "cat ~/.ssh/id_rsa",
    "cat ~/.ssh/id_ed25519",
    "cp ~/.ssh/id_rsa /tmp/x",
    "less ~/.ssh/config",
    "grep secret ~/.ssh/known_hosts",
])
def test_ssh_read_blocked(cmd):
    assert W._inspect_bash(cmd, "/ws") == S.WALL_READ_SSH


def test_ssh_unrelated_allowed():
    assert W._inspect_bash("echo hello", "/ws") is None
    assert W._inspect_bash("cat README.md", "/ws") is None


# --------------------------------------------------------------------------- #
# File-tool inspection
# --------------------------------------------------------------------------- #
def test_read_ssh_via_read_tool_blocked():
    reason = W._inspect_file_tool("Read", {"file_path": os.path.expanduser("~/.ssh/id_rsa")},
                                  "/ws")
    assert reason == S.WALL_READ_SSH


def test_read_outside_workspace_blocked(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    other = str(tmp_path / "other" / "secret.txt")
    reason = W._inspect_file_tool("Read", {"file_path": other}, ws)
    assert reason is not None and "outside" in reason.lower()


def test_read_inside_workspace_allowed(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    inside = os.path.join(ws, "a.txt")
    assert W._inspect_file_tool("Read", {"file_path": inside}, ws) is None


def test_write_outside_workspace_blocked(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    other = str(tmp_path / "elsewhere.txt")
    reason = W._inspect_file_tool("Write", {"file_path": other}, ws)
    assert reason is not None and "outside" in reason.lower()


def test_write_inside_workspace_allowed(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    assert W._inspect_file_tool("Write", {"file_path": os.path.join(ws, "x.txt")}, ws) is None


# --------------------------------------------------------------------------- #
# Generator: merge (not overwrite), env strip, MCP allowlist
# --------------------------------------------------------------------------- #
def test_generate_fresh_has_walls():
    d = W.generate_worker_settings("/ws")
    pre = d["hooks"]["PreToolUse"]
    assert any("worker_walls" in h["command"]
               for blk in pre for h in blk["hooks"])
    assert d["env"]["ORC_WORKSPACE"] == W._real("/ws")
    assert d["enableAllProjectMcpServers"] is False
    assert d["enabledMcpjsonServers"] == []


def test_generate_merges_existing_user_rules():
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "user-own-hook"}]}
            ],
            "PostToolUse": [
                {"matcher": ".*", "hooks": [{"type": "command", "command": "heartbeat"}]}
            ],
        },
        "model": "opus",
        "customUserKey": {"keep": True},
    }
    d = W.generate_worker_settings("/ws", existing_settings=existing)
    cmds = [h["command"] for blk in d["hooks"]["PreToolUse"] for h in blk["hooks"]]
    assert "user-own-hook" in cmds  # user's rule preserved
    assert any("worker_walls" in c for c in cmds)  # our wall added
    assert d["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "heartbeat"  # untouched
    assert d["model"] == "opus"  # unrelated key preserved
    assert d["customUserKey"] == {"keep": True}  # unrelated key preserved


def test_generate_merge_idempotent():
    d1 = W.generate_worker_settings("/ws")
    d2 = W.generate_worker_settings("/ws", existing_settings=d1)
    n1 = sum(len(b["hooks"]) for b in d1["hooks"]["PreToolUse"])
    n2 = sum(len(b["hooks"]) for b in d2["hooks"]["PreToolUse"])
    assert n1 == n2  # our wall not duplicated on re-generate


def test_env_strip_removes_secrets():
    base = {
        "MY_API_KEY": "x", "GITHUB_TOKEN": "y", "AWS_SECRET_ACCESS_KEY": "z",
        "SOME_PASSWORD": "p", "PATH": "/bin", "HOME": "/Users/admin", "LANG": "en",
    }
    env, removed = W.stripped_env(base_env=base)
    assert "MY_API_KEY" in removed
    assert "GITHUB_TOKEN" in removed
    assert "AWS_SECRET_ACCESS_KEY" in removed
    assert "SOME_PASSWORD" in removed
    assert "PATH" in env and "HOME" in env and "LANG" in env  # non-secrets kept


def test_mcp_allowlist_default_empty():
    d = W.generate_worker_settings("/ws")
    assert d["enabledMcpjsonServers"] == []


def test_mcp_allowlist_from_config():
    d = W.generate_worker_settings("/ws", mcp_allowlist=["ctx7", "gh"])
    assert d["enabledMcpjsonServers"] == ["ctx7", "gh"]


def test_write_worker_settings_merge_on_disk(tmp_path):
    ws = str(tmp_path / "proj")
    os.makedirs(os.path.join(ws, ".claude"))
    # pre-existing user settings
    pre = {"model": "sonnet", "hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "user-hook"}]}]}}
    with open(os.path.join(ws, ".claude", "settings.json"), "w") as f:
        json.dump(pre, f)
    path, merged = W.write_worker_settings(ws)
    assert merged is True
    on_disk = json.load(open(path))
    assert on_disk["model"] == "sonnet"  # preserved
    cmds = [h["command"] for blk in on_disk["hooks"]["PreToolUse"] for h in blk["hooks"]]
    assert "user-hook" in cmds and any("worker_walls" in c for c in cmds)


def test_write_worker_settings_fresh(tmp_path):
    ws = str(tmp_path / "proj2")
    path, merged = W.write_worker_settings(ws)
    assert merged is False
    assert os.path.exists(path)
    on_disk = json.load(open(path))
    assert on_disk["env"]["ORC_WORKSPACE"] == W._real(ws)


# --------------------------------------------------------------------------- #
# Hook subprocess: exit 2 on a blocked call, exit 0 on an allowed one
# --------------------------------------------------------------------------- #
def _run_hook(payload, workspace):
    env = dict(os.environ)
    env["ORC_WORKSPACE"] = workspace
    root = os.path.join(os.path.dirname(__file__), "..", "src")
    code = ("import sys; sys.path.insert(0, r'%s'); "
            "from orc.worker_walls import hook; hook()" % os.path.abspath(root))
    p = subprocess.run([sys.executable, "-c", code],
                       input=json.dumps(payload), capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


def test_hook_blocks_git_push(tmp_path):
    ws = str(tmp_path)
    rc, err = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}, ws)
    assert rc == 2
    assert "git push" in err.lower()


def test_hook_allows_safe_bash(tmp_path):
    ws = str(tmp_path)
    rc, err = _run_hook({"tool_name": "Bash", "tool_input": {"command": "echo ok"}}, ws)
    assert rc == 0


def test_hook_blocks_ssh_read(tmp_path):
    ws = str(tmp_path)
    rc, err = _run_hook({"tool_name": "Bash", "tool_input": {"command": "cat ~/.ssh/id_rsa"}}, ws)
    assert rc == 2
    assert "ssh" in err.lower()


# --------------------------------------------------------------------------- #
# G0c: git-push capability removal from the worker environment
# --------------------------------------------------------------------------- #
def test_push_neutralizing_env_disables_credentials():
    env = W.push_neutralizing_git_env()
    # never prompt, askpass always fails, keychain helper disabled inline
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "/usr/bin/false"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""  # empty -> osxkeychain off
    # returns a copy (mutating the result must not corrupt the module constant)
    env["GIT_TERMINAL_PROMPT"] = "1"
    assert W.push_neutralizing_git_env()["GIT_TERMINAL_PROMPT"] == "0"


def test_push_neutralizing_prefix_shell_shape():
    pfx = W.push_neutralizing_export_prefix()
    for k in ("GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "GIT_CONFIG_NOSYSTEM",
              "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        assert ("export %s=" % k) in pfx
    # each assignment terminated so it can be prepended before `cd ... && ...`
    assert pfx.strip().endswith(";")


def test_start_command_carries_push_wall(tmp_path):
    from orc import spawn
    cmd = spawn.build_start_command(str(tmp_path), "/bin/claude", "do it",
                                    session="s1", cfg={"sandbox": False})
    assert "GIT_TERMINAL_PROMPT=0" in cmd
    assert "GIT_ASKPASS=/usr/bin/false" in cmd
    assert "credential.helper" in cmd
    # push wall precedes the session export and the cd
    assert cmd.index("GIT_TERMINAL_PROMPT") < cmd.index("ORC_SESSION")
    assert cmd.index("ORC_SESSION") < cmd.index("cd ")


def test_worker_env_git_config_disables_keychain(tmp_path):
    # Run `git config credential.helper` under exactly the exported worker env and assert
    # the osxkeychain helper is no longer in effect (empty value from the inline config).
    from orc import spawn
    cmd = spawn.build_start_command(str(tmp_path), "/bin/true", "x",
                                    session="pw", cfg={"sandbox": False})
    exports = cmd.split(" && ")[0]  # the export prefix, before cd
    r = subprocess.run(["bash", "-c", exports + "; git config credential.helper"],
                       capture_output=True, text=True)
    # value is empty (helper disabled); no 'osxkeychain' leaks through
    assert "osxkeychain" not in r.stdout.strip()
