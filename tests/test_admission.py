"""Admission + back-pressure tests (F5), driven by tests/fixtures/limit-*.txt.

Every fixture holds the LITERAL limit-string wording the Claude Code CLI prints
(code.claude.com/docs/en/errors). The tests assert the classifier reacts correctly for
100% of the fixtures and that the admission gate refuses/admits on the RAM and usage
window signals. No real worker is spawned -- the whole surface is pure/deterministic.
"""
import os
import sys
import time
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import admission  # noqa: E402


FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


# A fixed "now" so reset-time parsing is deterministic (a Wednesday afternoon).
NOW = datetime.datetime(2026, 7, 15, 10, 0, 0)  # Wed 10:00
CFG = {"min_free_ram_mb": 400, "min_window_minutes": 5}
GOOD_WINDOW = {"active": True, "remaining_minutes": 120, "total_tokens": 1000}


# --------------------------------------------------------------------------- #
# classification: every fixture -> correct kind + reaction  (100% of fixtures)
# --------------------------------------------------------------------------- #
def test_session_limit_parks_with_reset():
    c = admission.classify_limit(_fixture("limit-session.txt"), now=NOW)
    assert c["kind"] == admission.KIND_SESSION
    assert c["reaction"] == admission.REACT_PARK
    assert c["reset_raw"] == "3:45pm"
    # 3:45pm today (Wed) is in the future relative to 10:00 -> same day 15:45
    reset = datetime.datetime.fromtimestamp(c["reset_epoch"])
    assert (reset.hour, reset.minute) == (15, 45)
    assert reset.date() == NOW.date()


def test_weekly_limit_parks_deep_with_weekday_reset():
    c = admission.classify_limit(_fixture("limit-weekly.txt"), now=NOW)
    assert c["kind"] == admission.KIND_WEEKLY
    assert c["reaction"] == admission.REACT_PARK
    assert c["reset_raw"] == "Mon 12:00am"
    reset = datetime.datetime.fromtimestamp(c["reset_epoch"])
    # next Monday 00:00 from a Wednesday
    assert reset.weekday() == 0
    assert (reset.hour, reset.minute) == (0, 0)
    assert reset > NOW


def test_opus_limit_is_degradation_not_hard_stop():
    c = admission.classify_limit(_fixture("limit-opus.txt"), now=NOW)
    assert c["kind"] == admission.KIND_OPUS
    assert c["reaction"] == admission.REACT_DEGRADE
    assert c["reset_raw"] == "3:45pm"
    assert c["reset_epoch"] is not None


def test_429_is_retry_no_park_no_reset():
    c = admission.classify_limit(_fixture("limit-429.txt"), now=NOW)
    assert c["kind"] == admission.KIND_429
    assert c["reaction"] == admission.REACT_RETRY
    assert c["reset_epoch"] is None


def test_529_is_retry_no_park():
    c = admission.classify_limit(_fixture("limit-529.txt"), now=NOW)
    assert c["kind"] == admission.KIND_529
    assert c["reaction"] == admission.REACT_RETRY


def test_clean_transcript_has_no_limit():
    assert admission.classify_limit(_fixture("limit-none.txt"), now=NOW) is None
    assert admission.classify_limit("", now=NOW) is None


def test_usage_limit_outranks_transient():
    # If both a session cap and a 429 appear, the safe (park) reaction wins.
    text = "API Error: Request rejected (429)\nYou've hit your session limit · resets 3:45pm"
    c = admission.classify_limit(text, now=NOW)
    assert c["kind"] == admission.KIND_SESSION
    assert c["reaction"] == admission.REACT_PARK


# --------------------------------------------------------------------------- #
# reset-time parsing edge cases
# --------------------------------------------------------------------------- #
def test_reset_time_rolls_to_tomorrow_when_past():
    # 9:00am has already passed at 10:00 -> next occurrence is tomorrow 09:00
    epoch = admission.parse_reset_time("9:00am", now=NOW)
    reset = datetime.datetime.fromtimestamp(epoch)
    assert (reset.hour, reset.minute) == (9, 0)
    assert reset.date() == (NOW + datetime.timedelta(days=1)).date()


def test_reset_time_pm_conversion():
    epoch = admission.parse_reset_time("11:30pm", now=NOW)
    reset = datetime.datetime.fromtimestamp(epoch)
    assert (reset.hour, reset.minute) == (23, 30)


def test_reset_time_12am_is_midnight():
    epoch = admission.parse_reset_time("12:00am", now=NOW)
    reset = datetime.datetime.fromtimestamp(epoch)
    assert reset.hour == 0


def test_reset_time_unparseable_is_none():
    assert admission.parse_reset_time("whenever", now=NOW) is None
    assert admission.parse_reset_time("", now=NOW) is None
    assert admission.parse_reset_time("99:99", now=NOW) is None


# --------------------------------------------------------------------------- #
# admission gate: RAM / window / ready / limit signals
# --------------------------------------------------------------------------- #
def test_admit_ok_when_all_signals_green():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3)
    assert ok is True and reason is None


def test_refuse_when_no_ready():
    ok, reason, _ = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=0)
    assert ok is False and reason == "no-ready"


def test_refuse_when_low_ram():
    ok, reason, _ = admission.admission_check(
        CFG, free_ram_mb=200, window=GOOD_WINDOW, ready_count=3)
    assert ok is False and reason == "low-ram"


def test_refuse_when_ram_unknown():
    ok, reason, _ = admission.admission_check(
        CFG, free_ram_mb=None, window=GOOD_WINDOW, ready_count=3)
    assert ok is False and reason == "low-ram"


def test_refuse_when_window_nearly_closed():
    win = {"active": True, "remaining_minutes": 2, "total_tokens": 1}
    ok, reason, _ = admission.admission_check(
        CFG, free_ram_mb=2000, window=win, ready_count=3)
    assert ok is False and reason == "window-low"


def test_refuse_when_window_inactive():
    win = {"active": False, "remaining_minutes": None, "total_tokens": None}
    ok, reason, _ = admission.admission_check(
        CFG, free_ram_mb=2000, window=win, ready_count=3)
    assert ok is False and reason == "window-inactive"


def test_session_limit_refuses_admission_with_reset_meta():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3,
        limit_text=_fixture("limit-session.txt"), now=NOW)
    assert ok is False and reason == "limit-session"
    assert meta["reaction"] == admission.REACT_PARK
    assert meta["reset_epoch"] is not None


def test_weekly_limit_refuses_admission():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3,
        limit_text=_fixture("limit-weekly.txt"), now=NOW)
    assert ok is False and reason == "limit-weekly"


def test_opus_limit_admits_but_flags_degradation():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3,
        limit_text=_fixture("limit-opus.txt"), now=NOW)
    assert ok is True and reason is None
    assert meta["reaction"] == admission.REACT_DEGRADE
    assert meta["limit_kind"] == admission.KIND_OPUS


def test_429_admits_transient_retry():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3,
        limit_text=_fixture("limit-429.txt"), now=NOW)
    assert ok is True and reason is None
    assert meta["reaction"] == admission.REACT_RETRY


def test_529_admits_transient_retry():
    ok, reason, meta = admission.admission_check(
        CFG, free_ram_mb=2000, window=GOOD_WINDOW, ready_count=3,
        limit_text=_fixture("limit-529.txt"), now=NOW)
    assert ok is True and meta["reaction"] == admission.REACT_RETRY


def test_reset_wait_seconds_is_positive_for_future():
    future = time.time() + 600
    assert 590 <= admission.reset_wait_seconds(future) <= 600
    assert admission.reset_wait_seconds(None) is None
    past = time.time() - 100
    assert admission.reset_wait_seconds(past) == 0.0
