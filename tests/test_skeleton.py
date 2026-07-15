"""Unit tests for the F2 skeleton modules: config, shift state, ordering, report, canary.

These are the fast (no-spawn) checks; the real interactive-spawn proof lives in
.verify/e2e-skeleton.sh with its evidence log.
"""
import os
import sys
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import config, shift as shiftmod, dispatcher, report, canary, beads  # noqa: E402
from orc import strings as S  # noqa: E402


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = str(tmp_path / "orc-home")
    monkeypatch.setenv("ORC_HOME", home)
    monkeypatch.setenv("ORC_HUB", home)
    os.makedirs(home)
    return home


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_config_defaults_and_override(isolated_home):
    cfg = config.load()
    assert cfg["max_workers"] == 1  # 8GB machine
    assert cfg["min_free_ram_mb"] > 0
    assert cfg["orc_src"].endswith("src")
    # override via config.json
    with open(config.config_path(), "w") as f:
        json.dump({"min_free_ram_mb": 999}, f)
    cfg2 = config.load()
    assert cfg2["min_free_ram_mb"] == 999
    assert cfg2["max_workers"] == 1  # default preserved


def test_config_malformed_falls_back(isolated_home):
    with open(config.config_path(), "w") as f:
        f.write("{ not json")
    cfg = config.load()  # must not crash
    assert cfg["max_workers"] == 1


# --------------------------------------------------------------------------- #
# shift state: atomic round-trip + transitions
# --------------------------------------------------------------------------- #
def test_shift_roundtrip_and_transitions(isolated_home):
    st = shiftmod.load()
    assert st["workers"] == [] and st["done"] == []
    shiftmod.start_shift(st, window_pct=30)
    shiftmod.add_worker(st, pid=123, session="s", project="/p", task="t1")
    shiftmod.save(st)

    st2 = shiftmod.load()
    assert st2["started"] is not None
    assert st2["window_pct_at_start"] == 30
    assert len(st2["workers"]) == 1 and st2["workers"][0]["task"] == "t1"

    shiftmod.mark_done(st2, "t1")
    assert st2["workers"] == [] and st2["done"][0]["task"] == "t1"
    shiftmod.add_worker(st2, pid=1, session="s", project="/p", task="t2")
    shiftmod.mark_parked(st2, "t2", "waiting on gate")
    assert st2["parked"][0]["reason"] == "waiting on gate"
    shiftmod.add_worker(st2, pid=1, session="s", project="/p", task="t3")
    shiftmod.mark_failed(st2, "t3", "boom")
    assert st2["failed"][0]["reason"] == "boom"


def test_add_worker_dedupes_task(isolated_home):
    st = shiftmod.load()
    shiftmod.add_worker(st, pid=1, session="s", project="/p", task="t1")
    shiftmod.add_worker(st, pid=2, session="s", project="/p", task="t1")
    assert len([w for w in st["workers"] if w["task"] == "t1"]) == 1


# --------------------------------------------------------------------------- #
# dispatcher: ordering (gate tasks to the end) + project-mutex
# --------------------------------------------------------------------------- #
def test_order_ready_gate_last_then_priority():
    tasks = [
        {"id": "a", "priority": 2, "labels": []},
        {"id": "g", "priority": 0, "labels": ["gate"]},        # gate -> last despite p0
        {"id": "b", "priority": 0, "labels": []},
        {"id": "gm", "priority": 1, "metadata": {"gate": True}},  # gate via metadata
    ]
    ordered = [t["id"] for t in dispatcher.order_ready(tasks)]
    assert ordered[:2] == ["b", "a"]           # autonomous by priority
    assert set(ordered[2:]) == {"g", "gm"}      # gates at the end


def test_project_mutex():
    st = {"workers": [{"project": "/proj/x", "task": "t1"}]}
    assert dispatcher.project_busy(st, "/proj/x") is True
    assert dispatcher.project_busy(st, "/proj/y") is False


def test_start_prompt_raw_vs_pipeline(monkeypatch):
    monkeypatch.delenv("ORC_RAW_PROMPT", raising=False)
    p = dispatcher.start_prompt("/proj", "slug", "do the thing")
    assert "pipeline" in p and "docs/tasks/slug/" in p
    monkeypatch.setenv("ORC_RAW_PROMPT", "1")
    assert dispatcher.start_prompt("/proj", "slug", "do the thing") == "do the thing"


def test_start_prompt_per_slug_dir(monkeypatch, tmp_path):
    # F12 seam: ORC_PROMPT_DIR selects a per-slug prompt file for the worker.
    monkeypatch.setenv("ORC_RAW_PROMPT", "1")
    monkeypatch.setenv("ORC_PROMPT_DIR", str(tmp_path))
    (tmp_path / "make-widget").write_text("build the widget and commit")
    assert dispatcher.start_prompt("/p", "make-widget", "x") == "build the widget and commit"
    # a slug with no file falls back to the task text
    assert dispatcher.start_prompt("/p", "no-file", "fallback text") == "fallback text"


# --------------------------------------------------------------------------- #
# beads metadata parsing
# --------------------------------------------------------------------------- #
def test_task_meta_parses_dict_and_json_string():
    assert beads.task_meta({"metadata": {"project": "/p"}}) == {"project": "/p"}
    assert beads.task_meta({"metadata": '{"project":"/p"}'}) == {"project": "/p"}
    assert beads.task_meta({"metadata": None}) == {}
    assert beads.task_meta({}) == {}


# --------------------------------------------------------------------------- #
# report: newspaper first line + <=150 words + running-worker acknowledgement
# --------------------------------------------------------------------------- #
def test_newspaper_summary_first_and_word_cap(isolated_home, monkeypatch):
    # Deterministic window. total_tokens is the CURRENT cumulative window total; the shift
    # baseline (tokens_at_start) makes the honest shift-spend delta computable.
    monkeypatch.setattr(report.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 150,
                                 "total_tokens": 500000, "cost_usd": 5.0})
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: 500000)
    monkeypatch.setattr(report.probes, "free_ram_mb", lambda: 1000)
    st = shiftmod.load()
    st["tokens_at_start"] = 174000    # spend delta = 500000-174000 = 326000 -> "~326k"
    shiftmod.mark_done(st, "t1")
    shiftmod.mark_parked(st, "t2", "gate")
    news = report.newspaper(st, isolated_home)
    lines = news.splitlines()
    # F6 backlog fix (taste passport): the one-sentence SUMMARY is the very first line,
    # the decorative title follows it (previously the title sat on line 1, summary on 2).
    assert "смена:" in lines[0]   # summary is now THE first line
    # The summary reports REAL shift spend (token delta), NOT the block-reset timer. The old
    # misleading "50% окна" (elapsed block time shown as consumption) must be gone.
    assert "326k" in lines[0] and "токенов" in lines[0]
    assert "% окна" not in news and "съедено" not in news
    assert lines[1] == S.RU_REPORT_TITLE
    assert len(news.split()) <= 150


def test_newspaper_acknowledges_running_worker(isolated_home, monkeypatch):
    monkeypatch.setattr(report.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 150, "total_tokens": 1})
    st = shiftmod.load()
    shiftmod.add_worker(st, pid=1, session="s", project="/p", task="t9")
    news = report.newspaper(st, isolated_home)
    assert "t9" in news
    assert "пуста" not in news  # must NOT claim empty when a worker runs


def test_live_status_no_shift(isolated_home):
    st = shiftmod.load()
    assert report.live_status(st, isolated_home) == S.RU_REPORT_NO_SHIFT


# --------------------------------------------------------------------------- #
# canary: forced-fail refuses; report format
# --------------------------------------------------------------------------- #
def test_canary_forced_fail(isolated_home, monkeypatch):
    monkeypatch.setenv("ORC_CANARY_FAIL", "auth")
    # stub probes so only the forced fail is decisive
    monkeypatch.setattr(canary.probes, "claude_auth_ok", lambda b: True)
    monkeypatch.setattr(canary.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 100})
    monkeypatch.setattr(canary.probes, "notifier_available", lambda: True)
    monkeypatch.setattr(canary.probes, "free_ram_mb", lambda: 1000)
    monkeypatch.setattr(canary.beads, "bd_available", lambda: True)
    monkeypatch.setattr(canary.beads, "ready", lambda hub: [])
    cfg = config.load()
    checks, ok = canary.run(cfg, isolated_home, spawn_probe=False)
    assert ok is False
    auth = [c for c in checks if c[0] == "auth"][0]
    assert auth[1] is False


def test_canary_all_ok(isolated_home, monkeypatch):
    monkeypatch.delenv("ORC_CANARY_FAIL", raising=False)
    monkeypatch.setattr(canary.probes, "claude_auth_ok", lambda b: True)
    monkeypatch.setattr(canary.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 100})
    monkeypatch.setattr(canary.probes, "notifier_available", lambda: True)
    monkeypatch.setattr(canary.probes, "free_ram_mb", lambda: 1000)
    monkeypatch.setattr(canary.beads, "bd_available", lambda: True)
    monkeypatch.setattr(canary.beads, "ready", lambda hub: [])
    cfg = config.load()
    checks, ok = canary.run(cfg, isolated_home, spawn_probe=False)
    assert ok is True
    assert all(c[1] for c in checks)


def test_canary_warns_loud_when_sandbox_disabled(isolated_home, monkeypatch):
    """B2 loud opt-out: allow_no_sandbox=true appends a [WARN] to the canary (does NOT fail
    the shift, but the operator is always told the exfiltration wall is off)."""
    monkeypatch.delenv("ORC_CANARY_FAIL", raising=False)
    monkeypatch.setattr(canary.probes, "claude_auth_ok", lambda b: True)
    monkeypatch.setattr(canary.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 100})
    monkeypatch.setattr(canary.probes, "notifier_available", lambda: True)
    monkeypatch.setattr(canary.probes, "free_ram_mb", lambda: 1000)
    monkeypatch.setattr(canary.beads, "bd_available", lambda: True)
    monkeypatch.setattr(canary.beads, "ready", lambda hub: [])
    cfg = config.load()
    cfg["allow_no_sandbox"] = True
    checks, ok = canary.run(cfg, isolated_home, spawn_probe=False)
    assert ok is True                                   # opt-out does not fail the shift
    warn = [c for c in checks if len(c) > 3 and c[3] == "warn"]
    assert len(warn) == 1 and warn[0][0] == "sandbox"
    report = canary.format_report(checks)
    assert "[WARN]" in report
    assert "~/.ssh" in report and "NOT blocked" in report
