"""Gate-protocol tests (F9): notification, gate card content, poll-gate wiring.

When a worker reaches a task-brief gate the dispatcher parks the task, fires a macOS
notification, and keeps the worker window (the session waits live -- the slot is held, the
user's accepted trade-off). The newspaper gate card must carry scope / bar / authority /
path to the brief / cost of error, and mark irreversible decisions as never-batch-approved.
Notification delivery is exercised through the ORC_NOTIFY_DRYRUN seam (no real popup).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from orc import notify, report, dispatcher, shift as shiftmod  # noqa: E402
from orc import strings as S  # noqa: E402


# --------------------------------------------------------------------------- #
# notification composition + delivery seam
# --------------------------------------------------------------------------- #
def test_notify_macos_composes_and_records(tmp_path, monkeypatch):
    log = str(tmp_path / "notif.log")
    monkeypatch.setenv("ORC_NOTIFY_DRYRUN", "1")
    monkeypatch.setenv("ORC_NOTIFY_LOG", log)
    ok = notify.notify_macos("orc", 'a "quoted" task', subtitle="gate", sound="Glass")
    assert ok is True
    body = open(log, encoding="utf-8").read()
    assert "display notification" in body
    assert 'with title "orc"' in body
    assert '\\"quoted\\"' in body                 # embedded quotes escaped
    assert "gate" in body


def test_notify_gate_uses_ru_strings(tmp_path, monkeypatch):
    log = str(tmp_path / "n.log")
    monkeypatch.setenv("ORC_NOTIFY_DRYRUN", "1")
    monkeypatch.setenv("ORC_NOTIFY_LOG", log)
    ok = notify.notify_gate({"notify": "macos"}, "t-7", "ship the release", "publish")
    assert ok is True
    body = open(log, encoding="utf-8").read()
    assert "t-7" in body
    assert "ship the release" in body


def test_notify_unknown_channel_returns_false():
    assert notify.notify_gate({"notify": "carrier-pigeon"}, "t", "x", "y") is False


# --------------------------------------------------------------------------- #
# gate card content: scope / bar / authority / brief path / cost / irreversible
# --------------------------------------------------------------------------- #
def _gate_task(**card):
    meta = {"project": "/proj", "slug": "ship", "gate": True, "gate_card": card}
    return {"id": "g1", "title": "approve release brief", "metadata": meta}


def test_gate_card_has_scope_bar_authority_brief_cost(monkeypatch):
    task = _gate_task(scope="publish v2", bar="tests green + changelog",
                      authority="may tag, may NOT push", cost="a bad release ships to users")
    monkeypatch.setattr(report.beads, "show", lambda hub, tid: task)
    card = report._gate_card("hub", {"task": "g1", "reason": "gate"})
    assert "publish v2" in card                              # scope
    assert "tests green + changelog" in card                 # bar
    assert "may tag, may NOT push" in card                   # authority
    assert "a bad release ships to users" in card            # cost of error
    assert "/proj/docs/tasks/ship/brief.md" in card          # path to the brief (ТЗ)


def test_gate_card_marks_irreversible(monkeypatch):
    task = _gate_task(scope="delete prod bucket", irreversible=True, cost="data loss")
    monkeypatch.setattr(report.beads, "show", lambda hub, tid: task)
    card = report._gate_card("hub", {"task": "g1", "reason": "gate"})
    assert "необратимое" in card                             # irreversible marker present


def test_gate_card_no_irreversible_marker_when_reversible(monkeypatch):
    task = _gate_task(scope="rename a variable", cost="tiny")
    monkeypatch.setattr(report.beads, "show", lambda hub, tid: task)
    card = report._gate_card("hub", {"task": "g1", "reason": "gate"})
    assert "необратимое" not in card


# --------------------------------------------------------------------------- #
# poll_completions gate branch: park + notify + keep window
# --------------------------------------------------------------------------- #
GATE_STATE = "- Phase: 2\n- Status: parked-on-gate\n"


def test_poll_gate_parks_notifies_and_keeps_window(tmp_path, monkeypatch):
    proj = str(tmp_path)
    os.makedirs(os.path.join(proj, "docs", "tasks", "slug1"))
    with open(os.path.join(proj, "docs", "tasks", "slug1", "STATE.md"), "w") as f:
        f.write(GATE_STATE)
    monkeypatch.setattr(dispatcher, "_worker_slug", lambda hub, tid, p: "slug1")
    monkeypatch.setattr(dispatcher.beads, "set_status", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher.beads, "show",
                        lambda hub, tid: {"id": tid, "title": "t", "metadata": {}})
    notified = []
    monkeypatch.setattr(dispatcher, "_notify_gate",
                        lambda cfg, hub, tid: notified.append(tid) or True)
    closed = []
    monkeypatch.setattr(dispatcher.spawn, "close_worker",
                        lambda cfg, tab, session=None: closed.append(tab))

    st = shiftmod._empty()
    shiftmod.add_worker(st, pid=1, session="g1", project=proj, task="g1", tab_id="w1")
    st, tr = dispatcher.poll_completions(st, "hub", cfg={"notify": "macos"})

    assert tr == [("g1", "gate")]
    assert notified == ["g1"]                     # operator notified
    assert closed == []                            # window KEPT (session waits live)
    assert any(p["task"] == "g1" for p in st["parked"])
    assert st["workers"] == []                     # parked out of the active set


def test_notify_gate_pulls_title_and_scope_from_metadata(monkeypatch):
    task = {"id": "g1", "title": "approve", "metadata":
            {"gate_card": {"scope": "publish"}}}
    monkeypatch.setattr(dispatcher.beads, "show", lambda hub, tid: task)
    seen = {}
    # _notify_gate imports orc.notify internally; patch that module's notify_gate
    import orc.notify as nmod

    def _capture(cfg, tid, title, scope):
        seen.update(tid=tid, title=title, scope=scope)
        return True
    monkeypatch.setattr(nmod, "notify_gate", _capture)
    ok = dispatcher._notify_gate({"notify": "macos"}, "hub", "g1")
    assert ok is True
    assert seen["scope"] == "publish"
    assert seen["title"] == "approve"
    assert seen["tid"] == "g1"
