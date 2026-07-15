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


def commits_since(repo, since_epoch):
    """Commit hashes (newest first) with a committer date STRICTLY after since_epoch.

    Used by the reward-hacking external-fact gate (B1): a DONE claim needs a REAL commit
    made after the worker started -- but "a commit exists" is not enough, the commit must
    also carry a non-empty diff (see commit_touches_real_files). Returns [] on error.

    We filter by committer timestamp (%ct) ourselves rather than trusting `git log --since`,
    which is date-fuzzy and INCLUSIVE (it would return the baseline commit created in the
    same second as since_epoch, whose real file would falsely pass the gate)."""
    if since_epoch is None:
        return []
    rc, out, _ = _git(repo, "log", "--format=%H %ct")
    if rc != 0:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        h, ct = parts[0], parts[1]
        try:
            if int(ct) > int(since_epoch):
                result.append(h)
        except ValueError:
            continue
    return result


def commit_touches_real_files(repo, rev, exclude_prefixes=()):
    """True if a commit changed at least one NON-empty, non-excluded file (B1).

    Rejects `git commit --allow-empty` (0 files changed) and commits that only touch
    orc-managed scaffolding (.orc/ / .claude/ / docs/tasks/). "Real" means: the commit's
    name-status lists an added/modified/... file outside the excluded prefixes whose blob
    at that commit is non-empty. A worker cannot pass the DONE gate with an empty commit
    or a commit that only rewrites its own STATE.md."""
    # name-status: lines like "A\tpath", "M\tpath", "R100\told\tnew"
    rc, out, _ = _git(repo, "show", "--no-renames", "--name-status",
                      "--format=", rev)
    if rc != 0:
        return False
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        path = parts[-1].strip()          # for renames the new name is last
        if status.startswith("D"):        # a deletion is not a produced deliverable
            continue
        if not path or path.startswith(tuple(exclude_prefixes)):
            continue
        # the file's content at this commit must be non-empty (reject an empty added file)
        rc2, blob, _ = _git(repo, "show", "%s:%s" % (rev, path))
        if rc2 == 0 and blob.strip():
            return True
    return False


def dirty_has_nonempty_file(repo, exclude_prefixes=()):
    """True if the working tree has a NON-empty, non-excluded dirty/untracked file (B1).

    Rejects an empty `touch out.txt` (size 0) and files only under orc-managed prefixes.
    This is the uncommitted-work analogue of commit_touches_real_files: a worker producing
    real output leaves a non-empty file; a reward-hacker's empty touch does not pass."""
    for p in dirty_paths(repo):
        if p.startswith(tuple(exclude_prefixes)):
            continue
        full = os.path.join(repo, p)
        try:
            if os.path.isfile(full) and os.path.getsize(full) > 0:
                return True
        except OSError:
            continue
    return False
