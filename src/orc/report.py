"""Shift report (the "newspaper") + live status — the signature experience (F2/F3).

Two modes:
  - LIVE: one row per active task (phase/status/minutes/spend); gate tasks first (glyph
    for waiting); pool footer (window %, minutes left, RAM).
  - NEWSPAPER: on shift completion. First line is the one-sentence summary; first screen
    <=150 words; gate cards below; paths at the bottom.

Output discipline (taste passport): plain text, important first, <=80 cols, no emoji
beyond status glyphs. User-facing text is Russian and lives in the RU_* strings; this
module only assembles those strings.
"""
import time

from . import strings as S
from . import beads
from . import probes


def _mins_since(epoch):
    if not epoch:
        return 0
    return int((time.time() - float(epoch)) / 60)


def _window_pct(window):
    """Percent of the 5h window consumed, best-effort. window from probes.ccusage_window."""
    if not window or not window.get("active"):
        return None
    rem = window.get("remaining_minutes")
    if rem is None:
        return None
    total = 300.0  # 5h ccusage block
    used = max(0.0, total - float(rem))
    return int(round(used / total * 100))


def summary_line(state):
    """First line of the newspaper: the whole shift in one grep-able sentence."""
    done = len(state.get("done", []))
    waiting = len(state.get("parked", []))
    failed = len(state.get("failed", []))
    window = probes.ccusage_window()
    pct = _window_pct(window)
    pct_s = "?" if pct is None else str(pct)
    return S.RU_REPORT_SUMMARY.format(done=done, waiting=waiting, failed=failed, pct=pct_s)


def live_status(state, hub, window=None):
    """Live status text: gate/waiting rows first, running rows, pool footer."""
    if window is None:
        window = probes.ccusage_window()
    lines = []

    parked = state.get("parked", [])
    workers = state.get("workers", [])

    if not state.get("started") and not workers and not parked:
        return S.RU_REPORT_NO_SHIFT

    # Waiting-on-you (gates / parked) first.
    if parked:
        lines.append(S.RU_SECTION_GATES)
        for p in parked:
            lines.append(S.RU_ROW_WAITING.format(id=p.get("task"), reason=p.get("reason", "")))

    # Running.
    if workers:
        lines.append(S.RU_SECTION_RUNNING)
        for w in workers:
            mins = _mins_since(w.get("started_epoch"))
            tokens = w.get("tokens_before")
            tok_s = "—" if tokens is None else str(tokens)
            lines.append(S.RU_ROW_RUNNING.format(
                id=w.get("task"), phase=w.get("phase", "build"),
                status="active", mins=mins, tokens=tok_s))

    # Pool footer.
    pct = _window_pct(window)
    mins_left = window.get("remaining_minutes") if window else None
    ram = probes.free_ram_mb()
    ram_s = "ok" if (ram is not None) else "?"
    lines.append(S.RU_POOL_LINE.format(
        pct=("?" if pct is None else pct),
        mins_left=("?" if mins_left is None else mins_left),
        ram=ram_s))
    return "\n".join(lines)


def newspaper(state, hub, window=None):
    """The completion newspaper. First line summary, then sections, then gate cards."""
    if window is None:
        window = probes.ccusage_window()
    lines = [S.RU_REPORT_TITLE, summary_line(state), ""]

    parked = state.get("parked", [])
    done = state.get("done", [])
    failed = state.get("failed", [])
    workers = state.get("workers", [])

    if not (parked or done or failed or workers):
        lines.append(S.RU_REPORT_EMPTY)
        return "\n".join(lines)

    # Still-running workers (a newspaper viewed mid-shift).
    if workers:
        lines.append(S.RU_SECTION_RUNNING)
        for w in workers:
            mins = _mins_since(w.get("started_epoch"))
            lines.append(S.RU_ROW_RUNNING.format(
                id=w.get("task"), phase=w.get("phase", "build"),
                status="active", mins=mins, tokens="—"))
        lines.append("")

    # Gates / waiting-on-you — with decision cards.
    if parked:
        lines.append(S.RU_SECTION_GATES)
        for p in parked:
            card = _gate_card(hub, p)
            lines.append(card)
        lines.append("")

    if done:
        lines.append(S.RU_SECTION_DONE)
        for d in done:
            lines.append(S.RU_ROW_DONE.format(id=d.get("task")))
        lines.append("")

    if failed:
        for fl in failed:
            lines.append(S.RU_ROW_FAILED.format(id=fl.get("task"), reason=fl.get("reason", "")))

    return "\n".join(lines).rstrip()


def _gate_card(hub, parked_entry):
    """Render a gate decision card. Pulls task detail from bd metadata when present."""
    task_id = parked_entry.get("task")
    reason = parked_entry.get("reason", "")
    title = task_id
    scope = reason or "—"
    bar = "—"
    authority = "—"
    cost = "—"
    brief_path = "—"
    task = beads.show(hub, task_id) if task_id else None
    if task:
        title = task.get("title", task_id)
        meta = beads.task_meta(task)
        gate = meta.get("gate_card") or {}
        scope = gate.get("scope", scope)
        bar = gate.get("bar", bar)
        authority = gate.get("authority", authority)
        cost = gate.get("cost", cost)
        slug = meta.get("slug")
        proj = meta.get("project")
        if proj and slug:
            brief_path = "%s/docs/tasks/%s/brief.md" % (proj, slug)
    return S.RU_GATE_CARD.format(
        id=task_id, title=title, scope=scope, bar=bar,
        authority=authority, cost=cost, brief_path=brief_path)
