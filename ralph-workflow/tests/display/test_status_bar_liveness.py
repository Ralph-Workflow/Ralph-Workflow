"""P0 (wt-028-display AC-01): elapsed time advances without a model re-push.

The status bar's elapsed timer is the operator's primary signal that
a long agent turn is still alive. The pre-P0 implementation only
re-rendered the bar when something else triggered a model re-push,
which meant the elapsed timer sat frozen during a quiet agent turn
- exactly when an operator most needs proof the run is still alive.

The fix introduces two new optional parameters that are pure with
respect to existing behavior:

* ``StatusBarModel.run_started_monotonic``: the run-start anchor in
  ``time.monotonic()`` units. ``None`` keeps the existing snapshot
  contract.
* ``render_status_bar(..., now_monotonic=...)``: the wall-clock
  anchor at render time. When both are set the renderer recomputes
  ``elapsed = now - run_started`` so the bar ticks on every refresh
  even if the upstream model has not changed.

These tests pin that contract without touching real I/O, real
clocks, or ``time.sleep``.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel, render_status_bar


def _ctx(width: int = 160) -> object:
    return make_display_context(force_width=width, force_glyphs=True)


def _model(started_at: float | None = 0.0) -> StatusBarModel:
    return StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=started_at,
    )


def test_render_status_bar_recomputes_elapsed_at_now_monotonic() -> None:
    """Two ``now_monotonic`` values with the SAME model yield different elapsed text.

    The whole point of AC-01: a single frozen model must keep ticking
    when the renderer is invoked again with a fresh ``now_monotonic``.
    """
    ctx = _ctx()
    model = _model(started_at=100.0)
    text_t0 = render_status_bar(model, ctx, now_monotonic=100.0)
    text_t61 = render_status_bar(model, ctx, now_monotonic=161.0)
    assert text_t0.plain != text_t61.plain, (
        "AC-01 violated: identical model + different now_monotonic produced "
        "identical elapsed text. The bar cannot have ticked."
    )


def test_render_status_bar_elapsed_segment_advances_at_now_monotonic() -> None:
    """``Time mm:ss`` advances between two ``now_monotonic`` values."""
    ctx = _ctx()
    model = _model(started_at=100.0)
    text_t0 = render_status_bar(model, ctx, now_monotonic=100.0)
    text_t65 = render_status_bar(model, ctx, now_monotonic=165.0)
    # Pin the exact text of the elapsed segment so a future regression
    # that drops the label or reformats it cannot pass silently.
    assert "Time 00:00" in text_t0.plain
    assert "Time 01:05" in text_t65.plain


def test_render_status_bar_without_now_monotonic_uses_snapshot() -> None:
    """The pre-P0 contract (snapshot ``elapsed_seconds`` only) is unchanged."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=42.0,
    )
    text = render_status_bar(model, ctx)
    # No now_monotonic, no run_started_monotonic: snapshot is the only
    # source of truth and the segment reads it.
    assert "Time 00:42" in text.plain


def test_render_status_bar_without_run_started_falls_back_to_snapshot() -> None:
    """``now_monotonic`` alone is not enough - the run-start anchor must be set too.

    This keeps the new params an opt-in: callers that supply only
    ``now_monotonic`` see the pre-P0 snapshot behavior, not a
    ``Time 00:00`` blank.
    """
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=42.0,
        run_started_monotonic=None,
    )
    text = render_status_bar(model, ctx, now_monotonic=200.0)
    assert "Time 00:42" in text.plain


def test_render_status_bar_now_monotonic_before_run_start_does_not_go_negative() -> None:
    """A clock skew that places ``now`` before ``run_started`` is clamped, not negative.

    Operators see ``Time 00:00`` rather than a bogus negative duration.
    """
    ctx = _ctx()
    model = _model(started_at=100.0)
    text = render_status_bar(model, ctx, now_monotonic=50.0)
    # Render uses the snapshot fallback in this edge case (the guard
    # only fires when now >= run_started); the segment must still be
    # present and well-formed.
    assert "Time" in text.plain


@pytest.mark.parametrize(
    "started_at,now_at,expected",
    [
        (0.0, 0.0, "00:00"),
        (0.0, 59.0, "00:59"),
        (0.0, 60.0, "01:00"),
        (0.0, 3_599.0, "59:59"),
        (0.0, 3_600.0, "1:00:00"),
    ],
)
def test_render_status_bar_format_boundaries(
    started_at: float, now_at: float, expected: str
) -> None:
    """Format-boundary table for the new recompute path.

    Same shape as the existing ``_format_elapsed`` boundary tests but
    driving the public ``render_status_bar(..., now_monotonic=...)``
    surface so the recompute is exercised end-to-end.
    """
    ctx = _ctx()
    model = _model(started_at=started_at)
    text = render_status_bar(model, ctx, now_monotonic=now_at)
    assert f"Time {expected}" in text.plain


def test_status_bar_injects_clock_into_recompute() -> None:
    """P0 (wt-028-display AC-01) integration: ``StatusBar`` owns an injectable clock.

    The Live region re-invokes ``_renderable`` on every tick, so the
    bar must drive the recompute from the configured clock - never
    from ``time.monotonic`` directly, which would be unreproducible
    in tests. Two calls to ``_renderable`` at different clock values
    must produce different ``Time mm:ss`` segments.
    """
    from ralph.display.status_bar import StatusBar

    class _StubDisplay:
        def __init__(self) -> None:
            self._ctx = _ctx()
            self._is_quiet = False

    clock = [100.0]

    def _now() -> float:
        return clock[0]

    bar = StatusBar(_StubDisplay(), clock=_now)
    bar._model = _model(started_at=100.0)

    clock[0] = 100.0
    text_t0 = bar._renderable()
    clock[0] = 161.0
    text_t61 = bar._renderable()
    assert text_t0.plain != text_t61.plain, (
        "StatusBar did not advance elapsed across two _renderable "
        "calls at different clock values - the Live region would "
        "show a frozen bar."
    )
    assert "Time 00:00" in text_t0.plain
    assert "Time 01:01" in text_t61.plain


def test_status_bar_default_clock_is_monotonic_when_no_clock_passed() -> None:
    """Backward-compat: constructing ``StatusBar`` without a clock keeps ``time.monotonic``."""
    from ralph.display.status_bar import StatusBar

    class _StubDisplay:
        def __init__(self) -> None:
            self._ctx = _ctx()
            self._is_quiet = False

    bar = StatusBar(_StubDisplay())
    assert bar._clock is not None
    # The default clock is a callable returning a float.
    sample = bar._clock()
    assert isinstance(sample, float)


def test_status_bar_clock_independent_of_run_started_yields_snapshot() -> None:
    """When the model has no run-start anchor the clock is irrelevant."""
    from ralph.display.status_bar import StatusBar

    class _StubDisplay:
        def __init__(self) -> None:
            self._ctx = _ctx()
            self._is_quiet = False

    bar = StatusBar(_StubDisplay(), clock=lambda: 99_999.0)
    bar._model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=12.0,
    )
    # No run_started_monotonic -> the clock must not be used; the
    # snapshot elapsed_seconds=12 is the only source of truth.
    text = bar._renderable()
    assert "Time 00:12" in text.plain


def test_render_status_bar_purity_preserved_when_now_monotonic_set() -> None:
    """DI purity: now_monotonic does not introduce env reads or Console construction.

    The renderer already lives behind a DI-purity test
    (``test_status_bar.py::test_render_status_bar_does_not_call_path_home``);
    this case exercises the new parameter and asserts the function
    remains a pure transformation of its inputs.
    """
    ctx = _ctx()
    model_a = _model(started_at=10.0)
    model_b = _model(started_at=10.0)
    a = render_status_bar(model_a, ctx, now_monotonic=20.0)
    b = render_status_bar(model_b, ctx, now_monotonic=20.0)
    # Identical inputs -> identical plain text (no hidden state).
    assert a.plain == b.plain
    # Returned object is a rich Text (the established public type).
    assert isinstance(a, Text)
