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
    """Percent of the 5h block ELAPSED, best-effort. window from probes.ccusage_window.

    DEPRECATED for the newspaper: this is the block-reset TIMER (time elapsed in the
    5-hour block), NOT quota/token spend. It used to be shown as an "N% of window
    consumed" line, which misled the operator into reading a schedule timer as resource
    consumption (same time-vs-limits confusion removed from admission). The newspaper now
    reports the REAL shift spend (token/cost delta) instead. Kept only for legacy capture.
    """
    if not window or not window.get("active"):
        return None
    rem = window.get("remaining_minutes")
    if rem is None:
        return None
    total = 300.0  # 5h ccusage block
    used = max(0.0, total - float(rem))
    return int(round(used / total * 100))


def _fmt_tokens(n):
    """Format a token count compactly: 1234 -> '1.2k', 3260000 -> '3.3M', 900 -> '900'."""
    n = int(n)
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000.0)
    if n >= 1_000:
        return "%.0fk" % round(n / 1_000.0)
    return str(n)


def shift_spend_text(state, window=None):
    """The newspaper's HONEST shift-spend phrase (RU), or None if no figure is available.

    We CANNOT compute a percent of the Max x20 subscription cap (ccusage does not know the
    plan's quota ceiling), so we show the ABSOLUTE spend this shift -- a real number the
    operator can watch -- never an invented percent.

    Preference order:
      1. per-task token delta summed over the shift (dispatcher.shift_spend) -- the most
         precise attribution (each worker's own ccusage baseline);
      2. whole-window token delta vs tokens_at_start captured at `orc start`;
      3. USD delta vs cost_at_start (fallback when token deltas are unknown).
    Returns the rendered RU_SPEND_SHIFT_* phrase (e.g. a "~326k tokens" / "~$0.3" spend
    line), or None when no figure is available.
    """
    from . import dispatcher  # lazy: avoid any import-order coupling
    spent = dispatcher.shift_spend(state)
    if spent is None:
        # fall back to the whole-window token delta vs the start-of-shift baseline
        base = state.get("tokens_at_start")
        now = probes.total_tokens_now()
        if base is not None and now is not None:
            spent = max(0, int(now) - int(base))
    if spent is not None:
        return S.RU_SPEND_SHIFT_TOKENS.format(tokens=_fmt_tokens(spent))
    # last resort: USD delta vs the start-of-shift cost baseline
    cbase = state.get("cost_at_start")
    cnow = probes.total_cost_now()
    if cbase is not None and cnow is not None:
        cost = max(0.0, float(cnow) - float(cbase))
        return S.RU_SPEND_SHIFT_COST.format(cost=("%.1f" % cost))
    return None


def _no_sandbox_active():
    """True if the config disables the OS-sandbox wall (allow_no_sandbox opt-out). B2."""
    try:
        from . import config
        return bool(config.load().get("allow_no_sandbox"))
    except Exception:
        return False


def summary_line(state):
    """First line of the newspaper: the whole shift in one grep-able sentence.

    Reports the REAL shift spend (token/cost delta) -- NOT the block-reset timer. When no
    spend figure is available (ccusage down) the spend clause is omitted rather than
    printing a misleading placeholder.
    """
    done = len(state.get("done", []))
    waiting = len(state.get("parked", []))
    failed = len(state.get("failed", []))
    spend = shift_spend_text(state)
    spend_clause = ("; " + spend) if spend else ""
    return S.RU_REPORT_SUMMARY.format(
        done=done, waiting=waiting, failed=failed, spend=spend_clause)


def queued_lines(ready_tasks):
    """Render the ready-but-not-started queue (consumer M1: a task added before `start`
    was invisible in status). One row per queued task with its project."""
    lines = [S.RU_SECTION_QUEUED]
    for t in ready_tasks:
        meta = beads.task_meta(t)
        lines.append(S.RU_ROW_QUEUED.format(
            id=t.get("id"), project=meta.get("project", "—")))
    return lines


def live_status(state, hub, window=None, ready_tasks=None):
    """Live status text: gate/waiting rows first, running rows, pool footer.

    When no shift is running but the queue is non-empty, show the queued tasks instead of
    the bare "shift not started" line (consumer M1 finding #2): the operator must see that
    the task they just added is really there.
    """
    if window is None:
        window = probes.ccusage_window()
    lines = []

    parked = state.get("parked", [])
    workers = state.get("workers", [])

    if not state.get("started") and not workers and not parked:
        if ready_tasks:
            body = queued_lines(ready_tasks)
            body.append(S.RU_REPORT_NO_SHIFT)
            return "\n".join(body)
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

    # Pool footer. HONEST labels: shift spend (real tokens/USD) + minutes until the LIMIT
    # WINDOW RESETS (a schedule timer, labelled as such -- never as "spent") + free RAM.
    mins_left = window.get("remaining_minutes") if window else None
    ram = probes.free_ram_mb()
    ram_s = "ok" if (ram is not None) else "?"
    spend = shift_spend_text(state) or S.RU_SPEND_UNKNOWN
    lines.append(S.RU_POOL_LINE.format(
        spend=spend,
        mins_left=("?" if mins_left is None else mins_left),
        ram=ram_s))
    return "\n".join(lines)


def newspaper(state, hub, window=None):
    """The completion newspaper. First line summary, then sections, then gate cards.

    Taste passport / design.md signature: the ONE-SENTENCE SUMMARY is the very first line
    (grep-able "N done" up top), the decorative title follows. This is the backlog fix
    from F14 (the summary used to sit on line 2 behind the title).
    """
    if window is None:
        window = probes.ccusage_window()
    lines = [summary_line(state), S.RU_REPORT_TITLE, ""]

    # B2 loud opt-out: if the OS-sandbox is disabled (allow_no_sandbox), the worker runs
    # without its exfiltration wall. Surface a bold warning in the morning digest so the
    # operator sees the missing wall, not just in the start-time canary.
    if _no_sandbox_active():
        lines.append(S.RU_NO_SANDBOX_WARN)
        lines.append("")

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
        # window total lets _done_row suppress an implausible per-task figure that is really
        # the global ccusage counter (consumer-1 #4). None when ccusage is unavailable.
        window_total = window.get("total_tokens") if window else None
        lines.append(S.RU_SECTION_DONE)
        for d in done:
            lines.append(_done_row(d, window_total=window_total))
        lines.append("")

    # Failed tasks get their OWN section header (consumer-1): a fallen task must not read
    # as "done". Rendered after the done section, clearly separated.
    if failed:
        lines.append(S.RU_SECTION_FAILED)
        for fl in failed:
            lines.append(S.RU_ROW_FAILED.format(id=fl.get("task"), reason=fl.get("reason", "")))

    return "\n".join(lines).rstrip()


def _spend_suffix(entry, window_total=None):
    """Render the PER-TASK spend suffix (F6), empty when spend is unknown or implausible.

    consumer-1 #4: the newspaper once showed a per-task figure of ~81M tokens — that is the
    GLOBAL ccusage window counter, not one task's spend (it leaked in via a recovered worker
    with no captured baseline). A real per-task delta can never exceed the whole active-window
    total, so if the recorded `spent` is >= the window total we treat it as a mis-recorded
    global and SUPPRESS it rather than print an alarming number. The reliable per-task delta
    (tokens_now - tokens_before) still renders normally.
    """
    spent = entry.get("spent")
    if spent is None:
        return ""
    if window_total is not None:
        try:
            if int(spent) >= int(window_total):
                return ""
        except (TypeError, ValueError):
            return ""
    return S.RU_SPEND_SUFFIX.format(spent=spent)


def _done_row(entry, window_total=None):
    """Render a completed-task row differentiating DONE / DONE-WAVE-N / BETA (F6)."""
    task_id = entry.get("task")
    spend = _spend_suffix(entry, window_total=window_total)
    kind = entry.get("kind", "done")
    if kind == "beta":
        return S.RU_ROW_BETA.format(id=task_id, spend=spend)
    if kind == "wave":
        return S.RU_ROW_DONE_WAVE.format(id=task_id, spend=spend)
    return S.RU_ROW_DONE.format(id=task_id, spend=spend)


def _truncate_path(path, max_len):
    """Shorten a path to <= max_len chars by eliding the MIDDLE with an ellipsis (E2).

    The gate card's brief-path line must respect the <=80-col taste-passport budget. A long
    project path (e.g. /Users/x/projects/<proj>/docs/tasks/<slug>/brief.md) easily exceeds it.
    Middle-elision keeps the informative head (which project) and tail (which file) visible,
    which matters more than the middle dirs. Measured in characters (not bytes).
    """
    if path is None:
        return "—"
    if len(path) <= max_len:
        return path
    if max_len <= 1:
        return "…"
    keep = max_len - 1  # room for the single ellipsis char
    head = (keep + 1) // 2
    tail = keep - head
    if tail <= 0:
        return path[:head] + "…"
    return path[:head] + "…" + path[-tail:]


# The gate card's brief-path line is rendered as "     <label>: <path>" — 5 leading spaces
# plus a 2-char label plus ": " (4). Budget the path so the whole line stays within the
# 80-column taste-passport width. Prefix width = 5 + 2 + 2 = 9 (measured in characters).
_GATE_TZ_PREFIX = 9
_GATE_LINE_MAX = 80


def _gate_card(hub, parked_entry):
    """Render a gate decision card. Pulls task detail from bd metadata when present.

    P8: if bd is transiently unavailable (BeadsError), the card DEGRADES to a minimal form
    (id + park reason + a note) instead of crashing the whole newspaper. The morning digest
    is the signature artifact -- it must print what it can, never blow up on a flaky bd.
    """
    task_id = parked_entry.get("task")
    reason = parked_entry.get("reason", "")
    title = task_id
    scope = reason or "—"
    bar = "—"
    authority = "—"
    cost = "—"
    brief_path = "—"
    irreversible = ""
    try:
        task = beads.show(hub, task_id) if task_id else None
    except beads.BeadsError:
        # bd down at render time -> degrade this one card, keep the rest of the newspaper.
        return S.RU_GATE_CARD_DEGRADED.format(id=task_id, reason=reason or "—")
    if task:
        title = task.get("title", task_id)
        meta = beads.task_meta(task)
        gate = meta.get("gate_card") or {}
        scope = gate.get("scope", scope)
        bar = gate.get("bar", bar)
        authority = gate.get("authority", authority)
        cost = gate.get("cost", cost)
        if gate.get("irreversible"):
            irreversible = S.RU_GATE_IRREVERSIBLE
        slug = meta.get("slug")
        proj = meta.get("project")
        if proj and slug:
            brief_path = "%s/docs/tasks/%s/brief.md" % (proj, slug)
    # Keep the brief-path line within the 80-col budget (E2): elide the path middle if needed.
    brief_path = _truncate_path(brief_path, _GATE_LINE_MAX - _GATE_TZ_PREFIX)
    return S.RU_GATE_CARD.format(
        id=task_id, title=title, scope=scope, bar=bar,
        authority=authority, cost=cost, brief_path=brief_path,
        irreversible=irreversible)
