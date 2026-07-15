"""Newspaper spend-reporting tests (2026-07-15 fix).

The shift summary/pool footer must report the REAL shift spend (token/cost delta), NOT the
5-hour block-reset timer. The old "N% of window" line showed elapsed block TIME as if it
were quota consumption -- misleading (same time-vs-limits confusion removed from admission).
These tests pin the corrected behavior. Pure/deterministic: ccusage totals are injected.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import report, shift as shiftmod  # noqa: E402


# --------------------------------------------------------------------------- #
# compact token formatting
# --------------------------------------------------------------------------- #
def test_fmt_tokens_scales_k_and_m():
    assert report._fmt_tokens(900) == "900"
    assert report._fmt_tokens(1000) == "1k"
    assert report._fmt_tokens(326000) == "326k"
    assert report._fmt_tokens(3260000) == "3.3M"


# --------------------------------------------------------------------------- #
# shift spend text: real tokens, never a time-percent
# --------------------------------------------------------------------------- #
def test_spend_text_prefers_per_task_delta():
    # per-task delta (dispatcher.shift_spend) wins: one live worker with a baseline.
    st = shiftmod._empty()
    st["workers"] = [{"task": "w", "tokens_before": 100000, "project": "/p"}]
    from orc import probes
    orig = probes.total_tokens_now
    probes.total_tokens_now = lambda: 426000
    try:
        txt = report.shift_spend_text(st)
    finally:
        probes.total_tokens_now = orig
    assert "326k" in txt and "токенов" in txt
    assert "%" not in txt          # never a percent of an unknown cap


def test_spend_text_falls_back_to_window_delta(monkeypatch):
    # No per-task baseline -> use the whole-window delta vs tokens_at_start.
    st = shiftmod._empty()
    st["tokens_at_start"] = 174000
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: 500000)
    txt = report.shift_spend_text(st)
    assert "326k" in txt


def test_spend_text_cost_fallback_when_no_token_delta(monkeypatch):
    # No token figures at all -> honest USD delta vs cost_at_start.
    st = shiftmod._empty()
    st["cost_at_start"] = 22.0
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: None)
    monkeypatch.setattr(report.probes, "total_cost_now", lambda: 22.3)
    txt = report.shift_spend_text(st)
    assert "$0.3" in txt


def test_spend_text_none_when_nothing_available(monkeypatch):
    st = shiftmod._empty()
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: None)
    monkeypatch.setattr(report.probes, "total_cost_now", lambda: None)
    assert report.shift_spend_text(st) is None


# --------------------------------------------------------------------------- #
# newspaper summary / pool footer show spend, never the misleading time-percent
# --------------------------------------------------------------------------- #
def test_summary_reports_tokens_not_window_percent(monkeypatch):
    monkeypatch.setattr(report.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 3,
                                 "total_tokens": 500000, "cost_usd": 5.0})
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: 500000)
    st = shiftmod._empty()
    st["tokens_at_start"] = 174000
    shiftmod.mark_done(st, "t1")
    line = report.summary_line(st)
    assert "потрачено" in line and "326k" in line
    # A near-reset block (3 min) must NOT read as "94% window consumed" or "съедено".
    assert "% окна" not in line and "съедено" not in line


def test_pool_footer_labels_reset_timer_honestly(monkeypatch):
    monkeypatch.setattr(report.probes, "ccusage_window",
                        lambda: {"active": True, "remaining_minutes": 150,
                                 "total_tokens": 500000, "cost_usd": 5.0})
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: 500000)
    monkeypatch.setattr(report.probes, "free_ram_mb", lambda: 1000)
    st = shiftmod._empty()
    st["started"] = "x"
    st["tokens_at_start"] = 174000
    shiftmod.add_worker(st, pid=1, session="s", project="/p", task="t9")
    footer = report.live_status(st, "hub").splitlines()[-1]
    # spend shown; reset time labelled as a reset timer, NOT as consumption.
    assert "потрачено" in footer
    assert "до сброса окна лимитов" in footer
    assert "% окна" not in footer


def test_summary_omits_spend_clause_when_ccusage_down(monkeypatch):
    monkeypatch.setattr(report.probes, "ccusage_window", lambda: None)
    monkeypatch.setattr(report.probes, "total_tokens_now", lambda: None)
    monkeypatch.setattr(report.probes, "total_cost_now", lambda: None)
    st = shiftmod._empty()
    shiftmod.mark_done(st, "t1")
    line = report.summary_line(st)
    assert "готово" in line
    # no misleading placeholder: the spend clause is simply omitted.
    assert "потрачено" not in line and "%" not in line
