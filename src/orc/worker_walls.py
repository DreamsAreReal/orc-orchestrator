#!/usr/bin/env python3
"""Worker sandbox deny-walls for orc.

Two responsibilities:

1. HOOK MODE (`worker_walls.py hook`): runs as a project-level PreToolUse hook in a
   worker's `.claude/settings.json`. Reads the hook JSON on stdin, decides whether the
   tool call crosses a sandbox boundary, and blocks it with **exit code 2** (stderr is
   fed back to the worker model). Exit 2 is the enforcement layer that is NOT bypassed by
   `bypassPermissions` — that is exactly what F1's negative spike proves on claude 2.1.193.

   Fail-CLOSED for the security-critical checks by design? No — see note below. The pipeline
   convention is fail-open (a broken hook must not brick a session). But a sandbox wall that
   fails open is not a wall. Compromise: parsing/introspection errors that leave us UNABLE to
   classify a command fall back to exit 0 (fail-open) ONLY for tool calls we do not recognize
   as dangerous; a recognized-dangerous pattern always blocks even if workspace resolution
   partially fails (we block on the pattern, not on a clean path proof).

2. GENERATOR MODE (`worker_walls.py gen <workspace>`): produces / merges the worker's
   `.claude/settings.json` (deny-walls hook wired in, env stripped, MCP allowlist), MERGING
   into any pre-existing settings instead of overwriting.

python 3.9-compatible: no match statement, no tomllib, no 3.10+ typing syntax.
"""
import sys
import os
import re
import json

from . import strings as S


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #
def _real(path):
    """Expand ~ and resolve to an absolute, symlink-free path (best effort)."""
    try:
        return os.path.realpath(os.path.expanduser(path))
    except Exception:
        return os.path.abspath(os.path.expanduser(path)) if path else path


def _is_inside(child, parent):
    """True if `child` is `parent` or lives under it (both already realpath'd)."""
    if not child or not parent:
        return False
    parent = parent.rstrip("/")
    if child == parent:
        return True
    return child.startswith(parent + "/")


def _ssh_dir():
    return _real("~/.ssh")


def _pipeline_skill_dir():
    """The pipeline skill tree (~/.claude/skills) the worker must be able to READ.

    A pipeline worker runs the FULL conveyor: it needs SKILL.md AND references/, templates/,
    agents/ under ~/.claude/skills to execute the phases/gates/templates -- not just the
    invariants in SKILL.md. The first live pipeline run surfaced that these were unreachable
    (the worker logged that the skill references were undreadable) because the read-wall
    blocks everything outside the task workspace. This subtree is READ-ONLY for the worker:
    it may READ it (to run the conveyor) but never WRITE it (a worker must not be able to
    edit its own conveyor). The read-allow is here (F1 hook); the write-deny is enforced by
    the seatbelt profile (deny file-write* is the default, ~/.claude/skills is not in the
    writable runtime subset) AND, for the Write/Edit tools, by this module keeping those
    workspace-only below."""
    return _real("~/.claude/skills")


# --------------------------------------------------------------------------- #
# Command-string inspection (Bash tool)
# --------------------------------------------------------------------------- #
RM_RECURSIVE_RE = re.compile(r"\brm\s+(-[a-zA-Z]*)*\b")
SSH_PATH_RE = re.compile(r"(^|[\s'\"=:])~?/?(\.ssh)(/|\b)")

# rm flags that make it recursive+forced (order-independent): needs at least r or f=danger.
_RM_DANGEROUS_FLAG = re.compile(r"^-[a-zA-Z]*[rRf][a-zA-Z]*$")

# Bash tokens that read file contents (used to catch `cat ~/.ssh/id_rsa`, etc.).
_READERS = {
    "cat", "less", "more", "head", "tail", "bat", "nl", "od", "xxd", "hexdump",
    "strings", "cp", "rsync", "scp", "grep", "egrep", "rg", "awk", "sed", "sort",
    "cut", "wc", "md5", "shasum", "openssl", "dd", "vi", "vim", "nano", "gpg",
}


def _split_tokens(cmd):
    """Rough shell tokenizer good enough for wall inspection (no full shlex quoting
    edge cases; on parse failure we return a coarse whitespace split)."""
    try:
        import shlex
        return shlex.split(cmd, comments=False, posix=True)
    except Exception:
        return cmd.split()


def _rm_targets_outside(cmd, workspace):
    """Return a blocking reason if the command is a recursive/forced rm whose targets
    escape the workspace, else None."""
    if not RM_RECURSIVE_RE.search(cmd):
        return None
    tokens = _split_tokens(cmd)
    # Only care about rm invocations; scan each `rm ... ` segment coarsely by re-splitting
    # on shell separators so `cd x && rm -rf /y` is handled.
    for segment in re.split(r"&&|\|\||;|\|", cmd):
        seg_tokens = _split_tokens(segment)
        if not seg_tokens or os.path.basename(seg_tokens[0]) != "rm":
            continue
        recursive = False
        targets = []
        for tok in seg_tokens[1:]:
            if tok.startswith("-") and tok != "--":
                if _RM_DANGEROUS_FLAG.match(tok):
                    recursive = True
                continue
            targets.append(tok)
        if not recursive:
            continue
        for tgt in targets:
            # Absolute, ~, or relative — resolve against a permissive cwd guess: if the
            # target resolves outside the workspace, block.
            resolved = _real(tgt if os.path.isabs(tgt) or tgt.startswith("~")
                             else os.path.join(workspace, tgt))
            if not _is_inside(resolved, workspace):
                return S.WALL_RM_OUTSIDE.format(workspace=workspace)
    return None


def _bash_reads_ssh(cmd):
    """True if a bash command appears to read ~/.ssh contents."""
    if not SSH_PATH_RE.search(cmd):
        return False
    tokens = _split_tokens(cmd)
    # If any reader command is present, OR a raw redirect/path references .ssh, block.
    for tok in tokens:
        base = os.path.basename(tok.split("=")[0])
        if base in _READERS:
            return True
    # Even without a known reader, a bare path to a private key is suspicious enough.
    if re.search(r"\.ssh/(id_|identity|.*_rsa|.*_ed25519|.*_ecdsa|known_hosts|config)",
                 cmd, re.IGNORECASE):
        return True
    return False


def _git_push_in(cmd):
    """True if any shell segment is a `git ... push` invocation. Scoped per-segment and
    token-aware so `git commit -m "push it"` (push inside a quoted arg) does not match."""
    for segment in re.split(r"&&|\|\||;|\|", cmd):
        seg = segment.strip()
        if not seg:
            continue
        tokens = _split_tokens(seg)
        if not tokens:
            continue
        # git must be the segment's command: skip leading VAR=value assignments and
        # `env`/`command`/`sudo` prefixes, then require the command token to be `git`.
        k = 0
        while k < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[k]):
            k += 1
        while k < len(tokens) and os.path.basename(tokens[k]) in ("env", "command", "sudo", "nice", "nohup"):
            k += 1
        if k >= len(tokens) or os.path.basename(tokens[k]) != "git":
            continue
        git_idx = k
        # the subcommand is the first token after git that is not a global option/value
        j = git_idx + 1
        while j < len(tokens):
            tok = tokens[j]
            if tok.startswith("-"):
                # global options like -C, -c take a following value; -C <path>, -c k=v
                if tok in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                    j += 2
                else:
                    j += 1
                continue
            break
        if j < len(tokens) and tokens[j] == "push":
            return True
    return False


def _inspect_bash(cmd, workspace):
    """Return (reason) if a Bash command crosses a wall, else None."""
    if _git_push_in(cmd):
        return S.WALL_GIT_PUSH
    ssh = _bash_reads_ssh(cmd)
    if ssh:
        return S.WALL_READ_SSH
    rm = _rm_targets_outside(cmd, workspace)
    if rm:
        return rm
    return None


# --------------------------------------------------------------------------- #
# File-tool inspection (Read / Write / Edit)
# --------------------------------------------------------------------------- #
def _inspect_file_tool(tool_name, tool_input, workspace):
    path = tool_input.get("file_path") or tool_input.get("path")
    if not path:
        return None
    resolved = _real(path if os.path.isabs(path) or path.startswith("~")
                     else os.path.join(workspace, path))
    # ~/.ssh is always off-limits, even to Read.
    if _is_inside(resolved, _ssh_dir()):
        return S.WALL_READ_SSH
    if tool_name in ("Read",):
        # The pipeline skill tree (~/.claude/skills) is READ-allowed so the worker can run the
        # full conveyor (references/, templates/, agents/, SKILL.md). Reading it is safe; the
        # write-deny below + the seatbelt profile keep it un-writable (a worker cannot edit its
        # own conveyor).
        if _is_inside(resolved, _pipeline_skill_dir()):
            return None
        if not _is_inside(resolved, workspace):
            return S.WALL_READ_OTHER_PROJECT.format(workspace=workspace)
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        if not _is_inside(resolved, workspace):
            return S.WALL_WRITE_OUTSIDE.format(workspace=workspace)
    return None


# --------------------------------------------------------------------------- #
# Hook entrypoint
# --------------------------------------------------------------------------- #
def _stdin_json():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def hook():
    """PreToolUse hook: exit 2 blocks the tool call; stderr is fed to the worker model."""
    data = _stdin_json()
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    # Workspace boundary: env set by the generated settings.json, else hook cwd.
    workspace = _real(os.environ.get("ORC_WORKSPACE") or data.get("cwd") or os.getcwd())

    reason = None
    if tool_name == "Bash":
        reason = _inspect_bash(tool_input.get("command", "") or "", workspace)
    elif tool_name in ("Read", "Write", "Edit", "NotebookEdit"):
        reason = _inspect_file_tool(tool_name, tool_input, workspace)

    if reason:
        sys.stderr.write(reason + "\n")
        sys.exit(2)
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Generator: worker .claude/settings.json (merge, not overwrite)
# --------------------------------------------------------------------------- #
# Secret env-var denylist (default): stripped from the worker environment so prod
# credentials are unreachable by construction. Config may extend this.
DEFAULT_SECRET_DENYLIST = [
    r".*_API_KEY$", r".*_TOKEN$", r".*_SECRET$", r".*_PASSWORD$",
    r"^AWS_.*", r"^GITHUB_TOKEN$", r"^GH_TOKEN$", r"^OPENAI_API_KEY$",
    r"^ANTHROPIC_API_KEY$", r"^NPM_TOKEN$", r"^GOOGLE_.*_KEY$",
    r".*_ACCESS_KEY.*", r".*PRIVATE_KEY.*", r"^SLACK_.*_TOKEN$",
]


def _hook_command():
    """Command string that invokes THIS module's hook mode with a stable interpreter.
    Uses the package path so the worker settings.json is self-contained regardless of cwd."""
    module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src
    return ('python3 -c "import sys; sys.path.insert(0, r\'%s\'); '
            'from orc.worker_walls import hook; hook()"' % module_dir)


def build_walls_hook_block():
    """The PreToolUse hook block enforcing the sandbox walls."""
    cmd = _hook_command()
    return {
        "PreToolUse": [
            {
                "matcher": "Bash|Read|Write|Edit|NotebookEdit",
                "hooks": [{"type": "command", "command": cmd}],
            }
        ]
    }


def _merge_hooks(existing, walls):
    """Merge the walls PreToolUse block into an existing hooks dict without dropping
    the user's / pipeline's own hooks. Idempotent: does not duplicate our own block."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    pre = list(merged.get("PreToolUse", []))
    our_cmd = walls["PreToolUse"][0]["hooks"][0]["command"]
    already = any(
        any(h.get("command") == our_cmd for h in (blk.get("hooks") or []))
        for blk in pre
    )
    if not already:
        pre = pre + walls["PreToolUse"]
    merged["PreToolUse"] = pre
    return merged


def _merge_hook_events(existing, blocks):
    """Merge multiple hook-event blocks ({event: [block,...]}) into an existing hooks dict.

    Idempotent per command string: a block whose command already appears under its event
    is not duplicated. Preserves the user's / pipeline's own hooks (F7 heartbeat merge)."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    for event, new_blocks in (blocks or {}).items():
        cur = list(merged.get(event, []))
        existing_cmds = set()
        for blk in cur:
            for h in (blk.get("hooks") or []):
                existing_cmds.add(h.get("command"))
        for blk in new_blocks:
            cmds = [h.get("command") for h in (blk.get("hooks") or [])]
            if any(c in existing_cmds for c in cmds):
                continue
            cur.append(blk)
        merged[event] = cur
    return merged


def stripped_env(base_env=None, denylist=None):
    """Return a copy of the environment with secret-denylist vars removed."""
    env = dict(base_env if base_env is not None else os.environ)
    patterns = [re.compile(p) for p in (denylist or DEFAULT_SECRET_DENYLIST)]
    removed = [k for k in list(env) if any(p.match(k) for p in patterns)]
    for k in removed:
        env.pop(k, None)
    return env, removed


def secret_denylist(extra=None):
    """The effective secret env-var denylist (built-in patterns + config extras)."""
    return list(DEFAULT_SECRET_DENYLIST) + list(extra or [])


def secret_var_names(base_env=None, denylist=None):
    """Concrete env-var NAMES in `base_env` that match the secret denylist (sorted).

    Resolves the regex denylist against the actual environment so the spawn command can
    `unset` exactly the secret vars present, not the patterns. Never includes vars that are
    absent -- unset of a missing var is harmless but we keep the list tight.
    """
    env = dict(base_env if base_env is not None else os.environ)
    patterns = [re.compile(p) for p in (denylist or DEFAULT_SECRET_DENYLIST)]
    return sorted(k for k in env if any(p.match(k) for p in patterns))


def unset_secrets_export_prefix(base_env=None, denylist=None):
    """Shell `unset VAR ...;` prefix that removes secret env vars for the worker tree (P3).

    Applied ahead of the worker's inner spawn command so prod credentials passed to the
    dispatcher's environment (ANTHROPIC_API_KEY, AWS_*, *_SECRET, *_TOKEN, GITHUB_TOKEN,
    ...) are UNREACHABLE by the worker and every child it spawns -- the "env cleared by
    construction" guarantee the brief requires, enforced on the ACTUAL spawn path (not only
    in a printed counter). NB: this touches only ENVIRONMENT variables; the claude OAuth
    token lives in the macOS Keychain (a different mechanism) and is deliberately left
    intact so the worker can authenticate. Returns "" if no secret var is present.
    """
    names = secret_var_names(base_env=base_env, denylist=denylist)
    if not names:
        return ""
    import shlex as _shlex
    return "unset %s; " % " ".join(_shlex.quote(n) for n in names)


# Git-push capability removal (G0c + B2). The F1 PreToolUse hook blocks the literal
# `git push` token but is bypassed by obfuscation (base64|bash), and the F13 seatbelt
# sandbox only confines FILE WRITES (network stays on so the worker can reach the claude
# API / git fetch / brew). So an obfuscated `git push` would otherwise authenticate via
# the macOS Keychain (credential.helper=osxkeychain, HTTPS) OR the ~/.ssh key (SSH remote)
# and reach the remote. These env vars remove the push CAPABILITY at its root for BOTH
# transports: no credential can be supplied to any git process in the worker's tree, so a
# push fails by auth regardless of how it is reached.
#   HTTPS: GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=false + empty credential.helper.
#   SSH  : GIT_SSH_COMMAND/GIT_SSH=/usr/bin/false so `git push git@host:...` cannot invoke
#          ssh at all, and SSH_AUTH_SOCK="" detaches any ssh-agent so no agent key is
#          offered. (B2: E3 proved an obfuscated SSH push authenticated to github with the
#          worker's ~/.ssh key -- the HTTPS-only wall did not cover the SSH transport.)
# Proven by spike (docs/evidence/F13-push/ + docs/evidence/fix1/): obfuscated HTTPS push
# -> "could not read Username: terminal prompts disabled"; obfuscated SSH push -> ssh
# replaced by /usr/bin/false, no key offered; exit != 0, nothing pushed.
PUSH_NEUTRALIZING_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",       # never prompt for a username/password interactively
    "GIT_ASKPASS": "/usr/bin/false",  # any askpass call fails -> no password is ever supplied
    "GIT_CONFIG_NOSYSTEM": "1",       # ignore /etc/gitconfig credential helpers
    "GIT_SSH_COMMAND": "/usr/bin/false",  # git's ssh transport cannot run -> no SSH push
    "GIT_SSH": "/usr/bin/false",          # older git ssh var -> same, no SSH push
    "SSH_AUTH_SOCK": "",              # detach any ssh-agent -> no agent key is offered
    "GIT_CONFIG_COUNT": "2",          # inject inline config pairs (below):
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",         # empty -> disables the osxkeychain helper for this tree
    "GIT_CONFIG_KEY_1": "core.sshCommand",
    "GIT_CONFIG_VALUE_1": "/usr/bin/false",  # belt-and-braces: config-level ssh disabled too
}


def push_neutralizing_git_env():
    """Return a copy of PUSH_NEUTRALIZING_GIT_ENV (git-push credential capability removed)."""
    return dict(PUSH_NEUTRALIZING_GIT_ENV)


def push_neutralizing_export_prefix():
    """Shell `export k=v; ...` prefix that strips git-push credentials for the worker tree.

    Prepended to the worker's inner spawn command so every git subprocess the worker
    launches inherits a credential-less environment. Values are shell-quoted.
    """
    import shlex as _shlex
    return "".join(
        "export %s=%s; " % (k, _shlex.quote(v))
        for k, v in PUSH_NEUTRALIZING_GIT_ENV.items()
    )


def generate_worker_settings(workspace, mcp_allowlist=None, secret_denylist=None,
                             existing_settings=None):
    """Build the merged worker settings dict (deny-walls + MCP allowlist + ORC_WORKSPACE).

    Merges into `existing_settings` (a dict) if given, preserving user rules; never
    overwrites unrelated keys. Returns the merged dict.
    """
    walls = build_walls_hook_block()
    base = dict(existing_settings) if isinstance(existing_settings, dict) else {}

    base["hooks"] = _merge_hooks(base.get("hooks", {}), walls)
    # F7 watchdog heartbeat: PreToolUse marker + PostToolUse heartbeat, merged without
    # dropping the user's / pipeline's own hooks (idempotent per command string).
    from . import watchdog as _wd
    base["hooks"] = _merge_hook_events(base.get("hooks", {}), _wd.heartbeat_hook_blocks())

    # Also declare a permissions.deny set as defense-in-depth (honored in non-bypass
    # modes; the hook is the real wall under bypassPermissions).
    perms = dict(base.get("permissions", {}))
    deny = list(perms.get("deny", []))
    for rule in ("Bash(git push:*)", "Read(~/.ssh/**)"):
        if rule not in deny:
            deny.append(rule)
    perms["deny"] = deny
    base["permissions"] = perms

    # MCP allowlist: workers start with NO MCP servers unless explicitly allowed.
    allow = list(mcp_allowlist or [])
    base["enabledMcpjsonServers"] = allow
    base["enableAllProjectMcpServers"] = False

    # Record the workspace boundary for the hook via env in settings.
    envblk = dict(base.get("env", {}))
    envblk["ORC_WORKSPACE"] = _real(workspace)
    base["env"] = envblk

    return base


def write_worker_settings(workspace, mcp_allowlist=None, secret_denylist=None):
    """Generate and write `<workspace>/.claude/settings.json`, merging if present.

    Returns (path, was_merged). Raises on I/O failure.
    """
    workspace = _real(workspace)
    settings_dir = os.path.join(workspace, ".claude")
    os.makedirs(settings_dir, exist_ok=True)
    path = os.path.join(settings_dir, "settings.json")

    existing = None
    was_merged = False
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
            was_merged = True
        except Exception:
            existing = None  # unreadable → treat as fresh, but do not crash

    merged = generate_worker_settings(
        workspace, mcp_allowlist=mcp_allowlist,
        secret_denylist=secret_denylist, existing_settings=existing,
    )
    # Atomic write (tmp + rename).
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path, was_merged


# --------------------------------------------------------------------------- #
# Module CLI
# --------------------------------------------------------------------------- #
def _main(argv):
    if not argv:
        sys.stderr.write("usage: worker_walls.py {hook|gen <workspace> [mcp...]}\n")
        return 2
    mode = argv[0]
    if mode == "hook":
        hook()  # exits
        return 0
    if mode == "gen":
        if len(argv) < 2:
            sys.stderr.write("usage: worker_walls.py gen <workspace> [mcp-server...]\n")
            return 2
        workspace = argv[1]
        mcp = argv[2:]
        path, merged = write_worker_settings(workspace, mcp_allowlist=mcp)
        msg = S.GEN_MERGED if merged else S.GEN_CREATED
        print(msg.format(path=path))
        _, removed = stripped_env()
        print(S.GEN_ENV_STRIPPED.format(n=len(removed)))
        if mcp:
            print(S.GEN_MCP_ALLOWLIST.format(servers=", ".join(mcp)))
        return 0
    sys.stderr.write("unknown mode: %s\n" % mode)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
