"""Git helpers for preflight (clean tree) and re-validate (product-layer change).

Thin wrappers over `git -C <repo>`; return simple values and never raise for an
expected "not a repo" / "dirty" state — the dispatcher decides what to do.
"""
import os
import subprocess


def _git(repo, *args):
    try:
        p = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return 1, "", "git not found"
    return p.returncode, p.stdout, p.stderr


def is_repo(path):
    rc, out, _ = _git(path, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def dirty_paths(repo):
    """Return the list of dirty/untracked paths (porcelain), or [] if clean.

    Uses -uall so untracked directories are expanded to individual files; this lets the
    dispatcher distinguish our own generated .claude/settings.json from a human's file
    inside an otherwise-new directory.
    """
    rc, out, _ = _git(repo, "status", "--porcelain", "-uall")
    if rc != 0:
        return []
    paths = []
    for line in out.splitlines():
        # porcelain: XY <path>  (or "XY orig -> new" for renames)
        if len(line) < 4:
            continue
        p = line[3:]
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        paths.append(p.strip())
    return paths


def is_clean(repo):
    return len(dirty_paths(repo)) == 0


def head_rev(repo):
    rc, out, _ = _git(repo, "rev-parse", "HEAD")
    return out.strip() if rc == 0 else None


def head_commit_epoch(repo):
    """Unix epoch (int) of the HEAD commit's committer date, or None.

    Used by the watchdog external post-condition check (F7): a commit newer than the
    worker's start time is real, on-disk progress -- not a self-reported claim."""
    rc, out, _ = _git(repo, "log", "-1", "--format=%ct")
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def product_layer_rev(repo, product_dir="docs"):
    """A cheap fingerprint of the product layer (docs/) so we can detect changes
    between task-brief approval and claim (re-validate, risk R5). Returns the last
    commit hash that touched product_dir, or None."""
    rc, out, _ = _git(repo, "log", "-1", "--format=%H", "--", product_dir)
    return out.strip() if rc == 0 else None
