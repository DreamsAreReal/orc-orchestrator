"""CLI-level tests for orc add / status (F3): batch add, --json validity, live ordering.

These drive the real `orc` CLI against a real beads queue in an isolated ORC_HOME with
throwaway git projects, so they exercise the same code path an operator uses. Marked to
skip cleanly if `bd` is not installed.
"""
import os
import sys
import json
import subprocess

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import beads  # noqa: E402

ORC = os.path.join(os.path.dirname(__file__), "..", "bin", "orc")

pytestmark = pytest.mark.skipif(not beads.bd_available(), reason="bd not installed")


def _mkproj(tmp_path, name):
    p = tmp_path / name
    p.mkdir()
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    (p / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(p), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], check=True)
    return str(p)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = str(tmp_path / "home")
    os.makedirs(home)
    e = dict(os.environ)
    e["ORC_HOME"] = home
    e["ORC_HUB"] = home
    subprocess.run([ORC, "init"], env=e, capture_output=True, text=True, check=True)
    return e, home


def _run(env, *args, stdin=None):
    return subprocess.run([ORC, *args], env=env, input=stdin,
                          capture_output=True, text=True)


def test_add_single_json(env, tmp_path):
    e, home = env
    proj = _mkproj(tmp_path, "p1")
    r = _run(e, "add", proj, "do the thing", "-p", "1", "--json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["project"] == proj and d["id"]


def test_add_offline_sets_meta(env, tmp_path):
    # P2: `orc add --offline` records offline=True in the task meta so the dispatcher can
    # cut this worker's network (deny) at spawn time.
    e, home = env
    proj = _mkproj(tmp_path, "p1")
    r = _run(e, "add", proj, "local-only refactor", "--offline", "--json")
    assert r.returncode == 0
    tid = json.loads(r.stdout)["id"]
    task = beads.show(home, tid)
    assert beads.task_meta(task).get("offline") is True


def test_add_without_offline_has_no_offline_meta(env, tmp_path):
    e, home = env
    proj = _mkproj(tmp_path, "p1")
    r = _run(e, "add", proj, "normal networked task", "--json")
    tid = json.loads(r.stdout)["id"]
    task = beads.show(home, tid)
    assert beads.task_meta(task).get("offline") is None


def test_add_batch_creates_all(env, tmp_path):
    e, home = env
    p1 = _mkproj(tmp_path, "p1")
    p2 = _mkproj(tmp_path, "p2")
    lines = "".join("%s: build feature %d\n" % (p1 if i < 5 else p2, i) for i in range(10))
    r = _run(e, "add", "--batch", "--json", stdin=lines)
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert len(d["created"]) == 10
    ready = beads.ready(home)
    assert len(ready) == 10


def test_add_batch_skips_missing_project(env, tmp_path):
    e, home = env
    p1 = _mkproj(tmp_path, "p1")
    lines = "%s: good task\n/nonexistent/proj: bad task\n" % p1
    r = _run(e, "add", "--batch", "--json", stdin=lines)
    d = json.loads(r.stdout)
    assert len(d["created"]) == 1  # bad project skipped
    assert "does not exist" in r.stderr


def test_add_missing_project_errors(env, tmp_path):
    e, home = env
    r = _run(e, "add", "/definitely/not/here", "task")
    assert r.returncode == 1
    assert "does not exist" in r.stderr


def test_status_live_json_valid(env, tmp_path):
    e, home = env
    proj = _mkproj(tmp_path, "p1")
    _run(e, "add", proj, "task one")
    r = _run(e, "status", "--json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    for key in ("started", "workers", "parked", "done", "failed", "summary"):
        assert key in d


def test_status_live_no_shift_message(env, tmp_path):
    e, home = env
    r = _run(e, "status")
    assert r.returncode == 0
    # no shift started yet -> the "no shift" guidance line
    assert "orc add" in r.stdout


def test_start_json_is_pure_json_on_canary_fail(env, tmp_path):
    """P7 (paspport): `orc start --json` stdout must be a SINGLE valid JSON object even
    when the canary fails -- the human-readable canary report goes to stderr, so the
    output pipes cleanly into jq. Before the fix the report preamble was printed to stdout
    ahead of the JSON and jq choked."""
    e, home = env
    e = dict(e)
    e["ORC_CANARY_FAIL"] = "auth"            # deterministic canary failure
    r = _run(e, "start", "--once", "--no-spawn-probe", "--json")
    assert r.returncode == 2                 # shift did not start
    # stdout is ONE clean JSON object (no canary preamble leaked onto stdout)
    d = json.loads(r.stdout)                  # would raise if preamble leaked
    assert d["canary_ok"] is False
    assert any(c["name"] == "auth" and c["ok"] is False for c in d["checks"])
    # the human-readable report lives on stderr, not stdout
    assert "canary preflight" in r.stderr
    assert "canary preflight" not in r.stdout


def test_start_notifies_on_canary_fail(env, tmp_path):
    """P6 / G7: on a canary failure a macOS notification is PUSHED (the operator learns the
    shift did not start even unattended). ORC_NOTIFY_DRYRUN records the composed script."""
    e, home = env
    e = dict(e)
    e["ORC_CANARY_FAIL"] = "auth"
    e["ORC_NOTIFY_DRYRUN"] = "1"
    log = str(tmp_path / "notify.log")
    e["ORC_NOTIFY_LOG"] = log
    r = _run(e, "start", "--once", "--no-spawn-probe")
    assert r.returncode == 2
    assert os.path.exists(log), "no notification was fired on canary failure"
    body = open(log).read()
    # the notification names the failed shift and the failing check
    assert "auth" in body
    # and it is the canary-fail notification (product title), not a gate one
    assert "orc" in body.lower()


def test_gate_task_ordered_last(env, tmp_path):
    e, home = env
    proj = _mkproj(tmp_path, "p1")
    _run(e, "add", proj, "autonomous A", "-p", "2")
    _run(e, "add", proj, "autonomous B", "-p", "1")
    _run(e, "add", proj, "approve me", "--gate", "-p", "0")
    from orc import dispatcher
    ordered = dispatcher.order_ready(beads.ready(home))
    labels_last = ordered[-1].get("labels") or []
    assert "gate" in labels_last  # gate is last despite p0
    # the two autonomous tasks come first, priority-sorted
    assert "gate" not in (ordered[0].get("labels") or [])
