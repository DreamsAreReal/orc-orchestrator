"""F13: OS-sandbox (macOS seatbelt) as the PRIMARY wall over the F1 pattern-hook.

The F1 deny-wall matches the Bash command string; it is bypassable by obfuscation
(base64|bash rm, python shutil.rmtree, find -delete, xargs rm) because the dangerous
action never appears literally. A seatbelt profile enforced by `sandbox-exec` denies
`file-write*` outside the task workspace at the SYSCALL level, so it does not matter how
the write was reached -- every obfuscated escape is blocked, and child processes inherit
the profile. This is the boundary that survives obfuscation; the F1 hook stays as the
secondary layer (defense in depth). Spike + live proof: .spikes/probe/sandbox.md,
.verify/sandbox-walls.sh.

Profile-design invariant (a real trap, see the spike): the workspace allow-rule must be
the NARROW subpath of the task workspace only -- never a broad parent like /private/tmp,
or the sandbox will (correctly) allow deletes under that parent.
"""
import os
import shlex


def sandbox_available():
    """True if macOS seatbelt (`sandbox-exec`) is present."""
    return os.path.exists("/usr/bin/sandbox-exec")


def _sb_quote(path):
    """Quote a path for a seatbelt profile string literal (double-quote context)."""
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_profile(workspace, extra_write_subpaths=None, deny_network=False):
    """Build a seatbelt profile: deny file-write everywhere, allow ONLY the workspace.

    `workspace` is made writable via a narrow (subpath ...) rule -- NOT a broad parent.
    `extra_write_subpaths` may add tightly-scoped writable dirs the worker legitimately
    needs (e.g. a per-worker temp dir); each must be a specific path, never a broad parent.
    `deny_network` fully blocks outbound network (per-host allowlisting is unreliable in
    user seatbelt -- see the spike; the default keeps network on because workers need the
    claude API / git fetch / brew, and git push stays blocked by the F1 hook).
    """
    ws = os.path.realpath(workspace)
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        '(allow file-write*',
        '  (subpath "%s"))' % _sb_quote(ws),
    ]
    for p in (extra_write_subpaths or []):
        rp = os.path.realpath(p)
        lines.append('(allow file-write* (subpath "%s"))' % _sb_quote(rp))
    # the shell/claude need these device sinks even under a write-deny profile
    lines.append(
        '(allow file-write-data '
        '(literal "/dev/null") (literal "/dev/stdout") '
        '(literal "/dev/stderr") (literal "/dev/tty"))')
    if deny_network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def write_profile(workspace, profile_dir=None, extra_write_subpaths=None,
                  deny_network=False):
    """Write a per-workspace seatbelt profile to disk. Returns the profile path.

    Stored under the workspace's .orc dir by default (inside the only writable subpath, so
    the sandboxed worker can be launched with a profile that lives where it may write).
    """
    if profile_dir is None:
        profile_dir = os.path.join(os.path.realpath(workspace), ".orc")
    os.makedirs(profile_dir, exist_ok=True)
    path = os.path.join(profile_dir, "sandbox.sb")
    content = build_profile(workspace, extra_write_subpaths=extra_write_subpaths,
                            deny_network=deny_network)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)
    return path


def wrap_command(profile_path, inner_command):
    """Wrap a shell command so it runs under the seatbelt profile (F13).

    Returns a single shell string: `sandbox-exec -f <profile> bash -lc '<inner>'`. The
    worker (and every child it spawns) is confined by the profile; the inner command is the
    normal `cd <project> && claude ...` line. Kept as one string so it drops into the same
    osascript `do script` / Ghostty `-e` spawn path unchanged.
    """
    return "/usr/bin/sandbox-exec -f %s bash -lc %s" % (
        shlex.quote(profile_path), shlex.quote(inner_command))
