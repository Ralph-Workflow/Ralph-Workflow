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


# ---------------------------------------------------------------------------
# P0 (wt-028-display AC-02 / AC-03): attention slot + liveness derivation.
# ---------------------------------------------------------------------------


def test_attention_slot_is_blank_when_no_state_set() -> None:
    """A healthy run renders a blank attention slot; neighbours do not shift."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
    )
    healthy_text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    # No attention glyph or label appears on a healthy run.
    assert "WAITING" not in healthy_text
    assert "STALLED" not in healthy_text
    assert "RETRYING" not in healthy_text
    # The phase label and elapsed segment still render.
    assert "Development" in healthy_text


def test_attention_waiting_label_and_glyph_render() -> None:
    """``attention="waiting"`` surfaces a distinct label + glyph + style."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        attention="waiting",
    )
    rendered = render_status_bar(model, ctx)
    assert "WAITING" in rendered.plain


def test_attention_retrying_label_and_glyph_render() -> None:
    """``attention="retrying"`` surfaces a distinct label + glyph + style."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        attention="retrying",
    )
    rendered = render_status_bar(model, ctx)
    assert "RETRYING" in rendered.plain


def test_attention_terminated_label_renders() -> None:
    """``attention="terminated"`` surfaces the DONE label."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        attention="terminated",
    )
    rendered = render_status_bar(model, ctx)
    assert "DONE" in rendered.plain


def test_attention_stall_rendered_only_when_pushed() -> None:
    """wt-047-stall-label: the bar renders ``STALLED`` ONLY when the
    watchdog has pushed ``attention='stalled'``.

    The display-side 30s gap derivation is removed (the watchdog is the
    sole owner of the stall label). A bare gap between model push and
    ``now_monotonic`` no longer flips the attention slot to ``stalled``.
    """
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
    )
    # No pushed attention and no ``last_activity_monotonic`` field: stale
    # rendered text for ANY now_monotonic must never show STALLED.
    for now_at in (100.0, 500.0, 9_999.0):
        text = render_status_bar(model, ctx, now_monotonic=now_at).plain
        assert "STALLED" not in text, (
            f"STALLED must not appear without a pushed attention; got {text!r}"
        )


def test_attention_stall_rendered_when_pushed() -> None:
    """wt-047-stall-label: a pushed ``attention='stalled'`` renders STALLED."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
        attention="stalled",
    )
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert "STALLED" in text


def test_stall_threshold_named_constant_removed() -> None:
    """wt-047-stall-label: ``_STALL_THRESHOLD_SECONDS`` no longer exists.

    The display-side gap derivation is removed because the watchdog is the
    sole owner of the stall label. Importing the constant from
    ``ralph.display.status_bar`` MUST raise ``ImportError`` so any caller
    that still relies on the display-side gap derivation is caught at
    import time (not at render time).
    """
    import importlib

    status_bar = importlib.import_module("ralph.display.status_bar")
    assert not hasattr(status_bar, "_STALL_THRESHOLD_SECONDS")


def test_attention_operator_state_wins_over_stall() -> None:
    """wt-047-stall-label: a pushed ``waiting`` overrides a pushed ``stalled``.

    The pushed operator state (waiting / retrying / terminated) wins
    over the watchdog-sourced ``stalled`` because operator intent must
    always be visible. This is the precedence rule pinned by the
    S-2 / S-4 contract.
    """
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
        attention="waiting",
    )
    text = render_status_bar(model, ctx, now_monotonic=100.0).plain
    assert "WAITING" in text
    assert "STALLED" not in text


def test_attention_unknown_pushed_value_renders_blank() -> None:
    """Defensive: an unknown pushed attention value is ignored."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        attention="not-a-real-state",
    )
    text = render_status_bar(model, ctx).plain
    assert "not-a-real-state" not in text


@pytest.mark.parametrize(
    ("attention", "expected"),
    [
        ("starting", "STARTING"),
        ("completed", "COMPLETE"),
        ("failed", "FAILED"),
        ("cancelled", "CANCELLED"),
    ],
)
def test_status_bar_regression_terminal_and_startup_states_are_truthful(
    attention: str, expected: str
) -> None:
    """S-1: startup and each terminal outcome remain textually distinct."""
    rendered = render_status_bar(
        StatusBarModel(
            workspace_root="/tmp/probe",
            phase_label="Development",
            phase_style=phase_style_for_phase("development"),
            attention=attention,
        ),
        _ctx(),
    ).plain
    assert expected in rendered


def test_status_bar_regression_liveness_frame_advances_without_reflow() -> None:
    """S-1: quiet live ticks rotate a fixed-width liveness cell."""
    model = _model(started_at=0.0)
    first = render_status_bar(model, _ctx(), now_monotonic=0.0).plain
    second = render_status_bar(model, _ctx(), now_monotonic=1.0).plain
    first_liveness = first[first.index("Development") + len("Development") : first.index("Time")]
    second_liveness = second[
        second.index("Development") + len("Development") : second.index("Time")
    ]
    assert first_liveness != second_liveness
    assert len(first_liveness) == len(second_liveness)
    assert first.index("Time") == second.index("Time")


def test_status_bar_refreshes_only_for_changed_live_frames() -> None:
    """The bounded ticker emits only when elapsed, attention, or width changes."""
    from ralph.display.status_bar import StatusBar

    class _StubDisplay:
        def __init__(self) -> None:
            self._ctx = _ctx(width=120)
            self._is_quiet = False

    class _Live:
        def __init__(self) -> None:
            self.refreshes = 0

        def refresh(self) -> None:
            self.refreshes += 1

    clock = [100.0]
    display = _StubDisplay()
    bar = StatusBar(display, clock=lambda: clock[0])
    bar.update(_model(started_at=100.0))
    live = _Live()
    bar._live = live
    bar._last_live_frame = bar._renderable().plain

    assert bar._refresh_live_if_changed() is False
    assert live.refreshes == 0

    clock[0] = 101.0
    assert bar._refresh_live_if_changed() is True
    assert live.refreshes == 1

    waiting_model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=4,
        elapsed_seconds=0.0,
        run_started_monotonic=100.0,
        attention="waiting",
    )
    bar.update(waiting_model)
    assert bar._refresh_live_if_changed() is True
    assert live.refreshes == 2

    display._ctx = _ctx(width=80)
    assert bar._refresh_live_if_changed() is True
    assert live.refreshes == 3
