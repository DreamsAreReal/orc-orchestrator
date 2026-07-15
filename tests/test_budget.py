"""Budget-cap + per-task spend attribution tests (F6).

Spend attribution is the delta of ccusage total tokens between claim and close; on this
1-worker machine that delta is exactly this task's spend (design.md). Budget caps (task /
shift) come from config and, when exceeded, park the offending work. The digest also
differentiates DONE / DONE-WAVE-N / BETA and shows per-task spend. Pure/deterministic:
token totals are injected, no real worker is spawned.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import dispatcher, shift as shiftmod, report  # noqa: E402
from orc import strings as S  # noqa: E402


def _worker(task, before, tab=None):
    return {"task": task, "tokens_before": before, "tab_id": tab, "project": "/p"}


# --------------------------------------------------------------------------- #
# per-task spend attribution
# --------------------------------------------------------------------------- #
def test_task_spend_is_delta_of_ccusage_total():
    w = _worker("t1", before=1000)
    assert dispatcher.task_spend(w, tokens_now=1500) == 500


def test_task_spend_never_negative():
    # ccusage total can only grow; guard against a transient dip reading
    w = _worker("t1", before=2000)
    assert dispatcher.task_spend(w, tokens_now=1900) == 0


def test_task_spend_unknown_when_no_baseline():
    assert dispatcher.task_spend({"tokens_before": None}, tokens_now=5000) is None


def test_shift_spend_sums_done_and_live():
    state = shiftmod._empty()
    state["done"] = [{"task": "d1", "spent": 300}, {"task": "d2", "spent": 200}]
    state["workers"] = [_worker("w1", before=1000)]
    # live worker delta = 1400-1000 = 400; done = 500; total 900
    assert dispatcher.shift_spend(state, tokens_now=1400) == 900


def test_shift_spend_unknown_when_nothing_known():
    state = shiftmod._empty()
    state["workers"] = [{"task": "w", "tokens_before": None}]
    assert dispatcher.shift_spend(state, tokens_now=5000) is None


# --------------------------------------------------------------------------- #
# task cap
# --------------------------------------------------------------------------- #
def test_over_task_cap_true_when_exceeded():
    cfg = {"task_token_cap": 1000}
    assert dispatcher.over_task_cap(cfg, _worker("t", before=0), tokens_now=1500) is True


def test_over_task_cap_false_within_cap():
    cfg = {"task_token_cap": 1000}
    assert dispatcher.over_task_cap(cfg, _worker("t", before=0), tokens_now=800) is False


def test_over_task_cap_zero_is_unlimited():
    cfg = {"task_token_cap": 0}
    assert dispatcher.over_task_cap(cfg, _worker("t", before=0), tokens_now=10 ** 9) is False


def test_enforce_budget_parks_over_cap_worker(monkeypatch):
    cfg = {"task_token_cap": 1000}
    state = shiftmod._empty()
    state["workers"] = [_worker("t-big", before=0, tab="9")]
    blocked = []
    closed = []
    monkeypatch.setattr(dispatcher, "_safe_block",
                        lambda hub, tid: blocked.append(tid))
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, tab, session=None: closed.append(tab))
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 5000)
    parked = dispatcher.enforce_budget(cfg, "hub", state)
    assert parked == [("t-big", 5000)]
    assert blocked == ["t-big"]           # bd blocked so it is not re-served
    assert closed == ["9"]                # worker stopped (RAM freed)
    assert state["workers"] == []
    assert any(p["task"] == "t-big" and "budget" in p["reason"]
               for p in state["parked"])


def test_enforce_budget_leaves_within_cap_worker(monkeypatch):
    cfg = {"task_token_cap": 100000}
    state = shiftmod._empty()
    state["workers"] = [_worker("t-ok", before=0, tab="9")]
    monkeypatch.setattr(dispatcher.probes, "total_tokens_now", lambda: 5000)
    parked = dispatcher.enforce_budget(cfg, "hub", state)
    assert parked == [] and len(state["workers"]) == 1


# --------------------------------------------------------------------------- #
# shift cap: no new tasks once the shift total is over the cap
# --------------------------------------------------------------------------- #
def test_over_shift_cap_true_when_done_exceeds():
    cfg = {"shift_token_cap": 1000}
    state = shiftmod._empty()
    state["done"] = [{"task": "d", "spent": 1200}]
    assert dispatcher.over_shift_cap(cfg, state) is True


def test_spawn_one_refuses_when_shift_cap_reached(tmp_path, monkeypatch):
    import subprocess
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    with open(os.path.join(repo, "r.md"), "w") as f:
        f.write("x")
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "i"], check=True)
    cfg = {"claude_bin": "/bin/true", "mcp_allowlist": [], "shift_token_cap": 1000,
           "min_free_ram_mb": 400, "min_window_minutes": 5, "allow_no_sandbox": True}
    state = shiftmod._empty()
    state["done"] = [{"task": "prev", "spent": 5000}]   # already over the shift cap
    claimed = []
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher.beads, "claim", lambda hub, tid: claimed.append(tid))
    task = {"id": "new", "metadata": {"project": repo, "slug": "new", "text": "N"}}
    ok, detail, state = dispatcher.spawn_one(cfg, "hub", state, task)
    assert ok is False and "shift-budget-cap" in detail
    assert claimed == []                                 # never claimed
    assert any(p["task"] == "new" for p in state["parked"])


# --------------------------------------------------------------------------- #
# newspaper: DONE / DONE-WAVE-N / BETA differentiation + spend suffix
# --------------------------------------------------------------------------- #
def test_done_kind_distinguishes_wave_beta_done():
    assert dispatcher.done_kind("- Status: DONE") == "done"
    assert dispatcher.done_kind("- Status: DONE-WAVE-3") == "wave"
    assert dispatcher.done_kind("- Status: BETA") == "beta"
    assert dispatcher.done_kind("phase 5 -> DONE-WAVE-12 (next wave queued)") == "wave"


def test_newspaper_differentiates_done_wave_beta(isolated_home=None, monkeypatch=None):
    # build state directly (no fixtures needed)
    state = shiftmod._empty()
    state["done"] = [
        {"task": "a", "kind": "done", "spent": 1200},
        {"task": "b", "kind": "wave", "spent": 3400},
        {"task": "c", "kind": "beta", "spent": None},
    ]
    news = report.newspaper(state, "hub", window={"active": True, "remaining_minutes": 150})
    # plain done shows spend; wave labelled; beta labelled as awaiting decision
    assert "~1200" in news
    assert "волна" in news          # DONE-WAVE-N is not a flat "готово"
    assert "бета" in news           # BETA differentiated
    assert "~3400" in news


def test_newspaper_summary_is_first_line():
    state = shiftmod._empty()
    state["done"] = [{"task": "a", "kind": "done", "spent": 100}]
    news = report.newspaper(state, "hub", window={"active": True, "remaining_minutes": 150})
    first = news.splitlines()[0]
    assert "смена:" in first and "готово" in first   # summary, not the title
    assert first != S.RU_REPORT_TITLE
