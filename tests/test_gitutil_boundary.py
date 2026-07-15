"""Boundary correctness for the B1 external-fact gate (found by the P2/3 scale run).

git's committer timestamp %ct has 1-second resolution, so a FAST worker whose commit lands
in the SAME wall-clock second as its start second was missed by a strict `%ct > since_epoch`
filter -- the task then wedged (a real, committed deliverable never recognized). The fix
(gitutil.commits_since + external_progress `baseline_rev`) recognizes a same-second worker
commit while still excluding the pre-existing baseline HEAD, so the anti-reward-hacking wall
stays intact. These tests use a REAL git repo.
"""
import os
import sys
import subprocess
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import gitutil, watchdog  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init_repo(path):
    _git(path, "init")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "commit", "--allow-empty", "-m", "init")


def test_same_second_worker_commit_is_recognized_with_baseline(tmp_path):
    """A worker commit made in the SAME second as `since_epoch` counts when baseline_rev is
    supplied -- the exact case the strict `>` filter missed and that wedged the scale run."""
    repo = str(tmp_path)
    _init_repo(repo)
    baseline = gitutil.head_rev(repo)
    since = time.time()                       # worker start
    # a fast worker: create a real file and commit it within the same wall-clock second.
    with open(os.path.join(repo, "OUT.txt"), "w") as f:
        f.write("real")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "orc: add OUT.txt")
    # the commit's %ct equals int(since) (same second) -> strict `>` would MISS it.
    revs_strict = gitutil.commits_since(repo, since)                    # no baseline
    revs_baseline = gitutil.commits_since(repo, since, baseline_rev=baseline)
    assert revs_strict == []                  # demonstrates the off-by-one the fix repairs
    assert len(revs_baseline) == 1            # baseline-aware: recognizes the new commit
    # end-to-end: external_progress now sees the fact with baseline_rev.
    assert watchdog.external_progress(repo, since_epoch=since) is False
    assert watchdog.external_progress(repo, since_epoch=since,
                                      baseline_rev=baseline) is True


def test_baseline_head_itself_never_counts(tmp_path):
    """B1 wall intact: the pre-existing HEAD (baseline) is excluded even at the same second,
    so a worker that committed NOTHING new cannot pass by pointing at the old HEAD."""
    repo = str(tmp_path)
    _init_repo(repo)
    # a real baseline commit (non-empty) that predates the worker.
    with open(os.path.join(repo, "PRE.txt"), "w") as f:
        f.write("pre-existing")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pre")
    baseline = gitutil.head_rev(repo)
    since = time.time()
    # no new commit by the worker.
    revs = gitutil.commits_since(repo, since, baseline_rev=baseline)
    assert revs == []                          # baseline + ancestors excluded
    assert watchdog.external_progress(repo, since_epoch=since,
                                      baseline_rev=baseline) is False


def test_empty_commit_still_rejected_with_baseline(tmp_path):
    """A `git commit --allow-empty` (0 diff) after start must still NOT pass the fact gate,
    even though it is a new commit at/after the start second (reward-hack bypass)."""
    repo = str(tmp_path)
    _init_repo(repo)
    baseline = gitutil.head_rev(repo)
    since = time.time()
    _git(repo, "commit", "--allow-empty", "-m", "empty after start")
    # commits_since returns it (it is new), but external_progress rejects it (no real file).
    assert len(gitutil.commits_since(repo, since, baseline_rev=baseline)) == 1
    assert watchdog.external_progress(repo, since_epoch=since,
                                      baseline_rev=baseline) is False
