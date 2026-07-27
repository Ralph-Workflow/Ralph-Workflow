"""Regression tests: the bar must mirror the watchdog's STALLED assessment.

wt-047-stall-label: the watchdog is the sole owner of the STALLED
label. The bar reads the watchdog-sourced attention via the host's
``watchdog_attention`` property and substitutes it into the model
ONLY when the pushed ``attention`` is None (a pushed operator state
such as ``waiting`` / ``retrying`` / ``terminated`` always wins).

These tests pin the contract:

- a watchdog-sourced ``stalled`` renders ``STALLED``;
- a pushed ``waiting`` / ``retrying`` / ``terminated`` always wins
  over a watchdog-sourced ``stalled``;
- a host without ``watchdog_attention`` degrades to the pushed model
  without raising inside the render callback;
- a watchdog-sourced ``None`` (no stall) renders blank (no STALLED).

The previous file tested the deleted ``_model_with_live_activity_anchor``
behavior (30 s gap derivation); the new file tests the
``_model_with_live_attention`` behavior in its place. The renamings
are intentional: the live-activity anchor was the OBSERVED symptom of
the drift, the watchdog-attention slot is the FIX (single source of
truth).
"""

from __future__ import annotations

import re

from ralph.display.context import make_display_context
from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBar, StatusBarModel, render_status_bar


def _ctx(width: int = 160) -> object:
    return make_display_context(force_width=width, force_glyphs=True)


class _HostWithWatchdogAttention:
    """Stand-in for ParallelDisplay: keeps a watchdog-sourced attention state."""

    def __init__(self, attention: str | None) -> None:
        self._ctx = _ctx()
        self._is_quiet = False
        self._attention = attention

    @property
    def watchdog_attention(self) -> str | None:
        return self._attention


class _LegacyHost:
    """Host predating the watchdog-attention slot; the bar must still render."""

    def __init__(self) -> None:
        self._ctx = _ctx()
        self._is_quiet = False


def _model(*, started: float, attention: str | None = None) -> StatusBarModel:
    return StatusBarModel(
        workspace_root="/tmp/ws",
        phase_label="Development",
        phase_style="bold",
        run_started_monotonic=started,
        attention=attention,
    )


def _model_no_anchor(*, attention: str | None = None) -> StatusBarModel:
    """Return a ``StatusBarModel`` with NO ``run_started_monotonic`` anchor.

    The anchor-less branch in :meth:`StatusBar._renderable` (the
    ``model.run_started_monotonic is None`` path) was the regression
    site for DA-001: the previous code only ran
    ``_model_with_live_attention`` on the anchored branch, so a
    watchdog-sourced ``stalled`` was invisible when the runner had
    not yet pushed an elapsed-time anchor. These tests pin the
    corrected behavior in BOTH branches.
    """
    return StatusBarModel(
        workspace_root="/tmp/ws",
        phase_label="Development",
        phase_style="bold",
        run_started_monotonic=None,
        attention=attention,
    )


def test_watchdog_stalled_renders_stalled() -> None:
    """A watchdog-sourced ``stalled`` (no pushed attention) renders ``STALLED``."""
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, attention=None)

    assert "STALLED" in bar._renderable().plain


def test_pushed_waiting_wins_over_watchdog_stalled() -> None:
    """A pushed ``waiting`` always wins over a watchdog-sourced ``stalled``."""
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, attention="waiting")

    rendered = bar._renderable().plain
    assert "WAITING" in rendered
    assert "STALLED" not in rendered


def test_pushed_retrying_wins_over_watchdog_stalled() -> None:
    """A pushed ``retrying`` always wins over a watchdog-sourced ``stalled``."""
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, attention="retrying")

    rendered = bar._renderable().plain
    assert "RETRYING" in rendered
    assert "STALLED" not in rendered


def test_pushed_terminated_wins_over_watchdog_stalled() -> None:
    """A pushed ``terminated`` always wins over a watchdog-sourced ``stalled``."""
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, attention="terminated")

    rendered = bar._renderable().plain
    assert "DONE" in rendered
    assert "STALLED" not in rendered


def test_host_without_watchdog_attention_falls_back_to_pushed_model() -> None:
    """A legacy host (no watchdog_attention slot) degrades to the pushed model."""
    now = 100.0
    bar = StatusBar(_LegacyHost(), clock=lambda: now)
    bar._model = _model(started=100.0, attention=None)

    # The render must not raise and must not invent a STALLED label
    # out of thin air (the watchdog never reported one).
    rendered = bar._renderable().plain
    assert "STALLED" not in rendered


def test_watchdog_attention_none_renders_blank() -> None:
    """A watchdog-sourced ``None`` (no stall) renders blank (no STALLED)."""
    now = 100.0
    host = _HostWithWatchdogAttention(None)
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, attention=None)

    rendered = bar._renderable().plain
    assert "STALLED" not in rendered


def test_unknown_watchdog_value_is_ignored() -> None:
    """An unknown watchdog-sourced value (defensive) is ignored -- no STALLED."""

    class _HostWithUnknown:
        def __init__(self) -> None:
            self._ctx = _ctx()
            self._is_quiet = False

        @property
        def watchdog_attention(self) -> str | None:
            return "in_progress"  # not a known attention key

    now = 100.0
    bar = StatusBar(_HostWithUnknown(), clock=lambda: now)
    bar._model = _model(started=100.0, attention=None)

    rendered = bar._renderable().plain
    assert "STALLED" not in rendered


# ---------------------------------------------------------------------------
# DA-001: anchor-less host cases.
#
# These pin the DA-001 fix: ``_renderable`` must run
# ``_model_with_live_attention`` BEFORE branching on
# ``run_started_monotonic`` so the watchdog-sourced STALLED slot is
# honored even when the runner has not yet pushed an elapsed-time
# anchor. The original code only substituted the watchdog value on
# the anchored branch, so a watchdog-sourced ``stalled`` was
# invisible on the anchor-less branch.
# ---------------------------------------------------------------------------


def test_watchdog_stalled_renders_stalled_anchor_less_host() -> None:
    """Watchdog ``stalled`` renders ``STALLED`` even with no elapsed-time anchor.

    DA-001 regression: the bar must mirror the watchdog's STALLED
    assessment on the ``run_started_monotonic is None`` branch
    (the previous code only substituted on the anchored branch).
    """
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model_no_anchor(attention=None)

    assert "STALLED" in bar._renderable().plain


def test_watchdog_attention_none_renders_blank_anchor_less_host() -> None:
    """Watchdog cleared + anchor-less host renders blank (no STALLED)."""
    now = 100.0
    host = _HostWithWatchdogAttention(None)
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model_no_anchor(attention=None)

    rendered = bar._renderable().plain
    assert "STALLED" not in rendered


def test_pushed_waiting_wins_over_watchdog_stalled_anchor_less_host() -> None:
    """Pushed ``waiting`` wins over watchdog ``stalled`` on the anchor-less branch too."""
    now = 100.0
    host = _HostWithWatchdogAttention("stalled")
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model_no_anchor(attention="waiting")

    rendered = bar._renderable().plain
    assert "WAITING" in rendered
    assert "STALLED" not in rendered


def test_legacy_host_anchor_less_does_not_invent_stalled() -> None:
    """Legacy host + anchor-less model renders blank (no STALLED)."""
    now = 100.0
    bar = StatusBar(_LegacyHost(), clock=lambda: now)
    bar._model = _model_no_anchor(attention=None)

    rendered = bar._renderable().plain
    assert "STALLED" not in rendered


# ---------------------------------------------------------------------------
# wt-028-display S-1 / AC-01: the live P0 defect — elapsed frozen
# during quiet agent turns at width >= 60, AND the dual rendering
# of the elapsed segment (fixed-width column + optional-label trail).
#
# These are TDD-RED regression tests: the plan pins the spec ("the
# elapsed timer advances visibly, about once a second") and these
# tests prove the contract is in force from now on. They MUST fail
# against the pre-fix implementation and pass against the fix.
# ---------------------------------------------------------------------------


def _elapsed_model(*, started_at: float = 0.0) -> StatusBarModel:
    return StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        inner_analysis=1,
        inner_analysis_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=started_at,
        agent_name="claude",
    )


def test_elapsed_fixed_column_uses_recomputed_value_at_width_120() -> None:
    """S-1: at width 120, the visible elapsed text advances across two clock values.

    Pre-fix the fixed-width column at status_bar.py:_format_elapsed_fixed
    was driven by ``model.elapsed_seconds`` (the stale push snapshot).
    A re-render at a later ``now_monotonic`` left the visible text
    frozen on the snapshot, then the optional trailing label was the
    only thing that changed -- which is why AC-01 explicitly requires
    the visible text to advance (not just one of two copies).
    """
    ctx = _ctx(width=120)
    model = _elapsed_model(started_at=0.0)
    text_t0 = render_status_bar(model, ctx, now_monotonic=0.0)
    text_t61 = render_status_bar(model, ctx, now_monotonic=61.0)
    # Pin the exact transition: t=0 -> Time 00:00, t=61 -> Time 01:01.
    assert "Time 00:00" in text_t0.plain, (
        f"baseline render must include Time 00:00, got {text_t0.plain!r}"
    )
    assert "Time 01:01" in text_t61.plain, (
        f"fixed column at width 120 must have advanced to Time 01:01; "
        f"got {text_t61.plain!r}"
    )


def test_elapsed_renders_exactly_once_at_width_120() -> None:
    """S-1: the elapsed label appears exactly once at width 120 (no dual rendering).

    Pre-fix the optional trailing label could also carry an
    ``elapsed_label`` (recomputed at render time), so the bar showed
    the elapsed twice -- once in the fixed-width column (frozen) and
    once in the optional trail. The spec requires ONE elapsed
    rendering per entry.
    """
    ctx = _ctx(width=120)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=65.0).plain
    matches = re.findall(r"(?:Time \d{1,2}:\d{2}(?::\d{2})?|\d+m\d{2}s)", plain)
    assert len(matches) == 1, (
        f"S-1: elapsed must render exactly once at width 120; got "
        f"{len(matches)} matches: {matches!r} in plain={plain!r}"
    )


def test_elapsed_renders_exactly_once_at_width_60() -> None:
    """S-1: the elapsed label appears exactly once at width 60 (no dual rendering)."""
    ctx = _ctx(width=60)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=0.0).plain
    matches = re.findall(r"(?:Time \d{1,2}:\d{2}(?::\d{2})?|\d+m\d{2}s)", plain)
    assert len(matches) == 1, (
        f"S-1: elapsed must render exactly once at width 60; got "
        f"{len(matches)} matches: {matches!r} in plain={plain!r}"
    )


def test_elapsed_renders_exactly_once_at_width_80() -> None:
    """S-1: the elapsed label appears exactly once at width 80 (no dual rendering)."""
    ctx = _ctx(width=80)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=0.0).plain
    matches = re.findall(r"(?:Time \d{1,2}:\d{2}(?::\d{2})?|\d+m\d{2}s)", plain)
    assert len(matches) == 1, (
        f"S-1: elapsed must render exactly once at width 80; got "
        f"{len(matches)} matches: {matches!r} in plain={plain!r}"
    )


def test_elapsed_first_occurrence_is_recomputed_at_width_120() -> None:
    """S-1: at width 120, the FIRST 'Time' label is the recomputed one.

    The pre-fix code rendered the stale snapshot in the fixed-width
    column FIRST, then appended the recomputed value as the optional
    trailing label. The operator's eye lands on the leading bar
    segment first, so the FIRST occurrence is the one that has to
    tick. This test pins the spec.
    """
    ctx = _ctx(width=120)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=61.0).plain
    # The first 'Time' label must be the recomputed value (1:01),
    # not the stale snapshot (0:00).
    first_time_idx = plain.find("Time ")
    assert first_time_idx >= 0, f"no 'Time' label found in {plain!r}"
    first_time_value = plain[first_time_idx:first_time_idx + 12]
    assert "01:01" in first_time_value, (
        f"S-1: first 'Time' label at width 120 must be the recomputed "
        f"value (Time 01:01); got first_time_value={first_time_value!r} "
        f"in plain={plain!r}"
    )
    # And there must be exactly one Time label.
    matches = re.findall(r"Time \d{1,2}:\d{2}(?::\d{2})?", plain)
    assert len(matches) == 1, (
        f"S-1: elapsed must render exactly once at width 120; got "
        f"{len(matches)} matches: {matches!r} in plain={plain!r}"
    )


def test_elapsed_survives_at_width_40_floor() -> None:
    """S-1 / S-3: the 40-col floor MUST keep the elapsed segment."""
    import re as _re
    ctx = _ctx(width=40)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=761.0).plain
    short_forms = (
        _re.search(r"Time \d{1,2}:\d{2}", plain),
        _re.search(r"\d+m\d{2}s", plain),
        _re.search(r"\d+:\d{2}:\d{2}", plain),
    )
    assert any(short_forms), (
        f"S-1/S-3: 40-col floor must keep an elapsed short form "
        f"(mm:ss / XmXXs / H:MM:SS); got plain={plain!r}"
    )


def test_liveness_glyph_between_phase_and_elapsed_at_width_120() -> None:
    """S-3: a distinct liveness glyph segment sits between phase and elapsed."""
    ctx = _ctx(width=120)
    model = _elapsed_model(started_at=0.0)
    plain = render_status_bar(model, ctx, now_monotonic=0.0).plain
    phase_idx = plain.find("Development")
    time_idx = plain.find("Time ")
    assert phase_idx >= 0
    assert time_idx >= 0
    between = plain[phase_idx + len("Development"):time_idx]
    non_space = [c for c in between if not c.isspace() and c not in chr(9670) + chr(9632) + chr(9646)]
    assert len(non_space) >= 1, (
        f"S-3: a liveness glyph must sit between phase and elapsed; "
        f"between={between!r} plain={plain!r}"
    )
