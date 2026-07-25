"""Regression tests: the bar must not invent STALLED for a live agent.

``StatusBarModel.last_activity_monotonic`` is a *push-time snapshot*. The
runner re-pushes a model only when the (phase, cycle, alert, label) signature
changes, which during a long development phase is many minutes apart, while
``StatusBar._renderable`` re-derives ``stalled`` against a ``now_monotonic``
that advances on every Live tick. The gap being measured was therefore "time
since the last model push", not "time since the last activity" — so a run whose
agent was demonstrably alive (the idle watchdog holding fresh waiting-status and
subagent-progress evidence, all of which the host records) still flipped to
STALLED 30 s in.

The host's ``last_activity_monotonic`` is the live anchor and is what the
watchdog's evidence ultimately refreshes; the bar reads it at render time so
the two liveness views cannot drift apart.
"""

from __future__ import annotations

from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBar, StatusBarModel

_STALL_SECONDS = 30.0


def _ctx(width: int = 160) -> object:
    return make_display_context(force_width=width, force_glyphs=True)


class _HostWithAnchor:
    """Stand-in for ParallelDisplay: keeps a live activity anchor."""

    def __init__(self, anchor: float | None) -> None:
        self._ctx = _ctx()
        self._is_quiet = False
        self._anchor = anchor

    @property
    def last_activity_monotonic(self) -> float | None:
        return self._anchor


class _LegacyHost:
    """Host predating the live anchor; the bar must still render."""

    def __init__(self) -> None:
        self._ctx = _ctx()
        self._is_quiet = False


def _model(*, started: float, pushed_anchor: float | None) -> StatusBarModel:
    return StatusBarModel(
        workspace_root="/tmp/ws",
        phase_label="Development",
        phase_style="bold",
        run_started_monotonic=started,
        last_activity_monotonic=pushed_anchor,
    )


def test_live_anchor_prevents_stalled_for_an_active_agent() -> None:
    """A stale push + fresh host activity must NOT read as stalled."""
    now = 100.0 + 10 * _STALL_SECONDS
    # Model was pushed at the start of the phase; the agent has been emitting
    # activity ever since, the most recent one moments ago.
    host = _HostWithAnchor(now - 1.0)
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, pushed_anchor=100.0)

    assert "STALLED" not in bar._renderable().plain


def test_genuinely_idle_run_still_reports_stalled() -> None:
    """The guard must not disable the signal it is protecting."""
    now = 100.0 + 10 * _STALL_SECONDS
    host = _HostWithAnchor(now - (_STALL_SECONDS + 1.0))
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, pushed_anchor=100.0)

    assert "STALLED" in bar._renderable().plain


def test_a_newer_pushed_snapshot_is_not_discarded() -> None:
    """The later of the two anchors wins, whichever side it came from."""
    now = 100.0 + 10 * _STALL_SECONDS
    host = _HostWithAnchor(100.0)
    bar = StatusBar(host, clock=lambda: now)
    bar._model = _model(started=100.0, pushed_anchor=now - 1.0)

    assert "STALLED" not in bar._renderable().plain


def test_host_without_a_live_anchor_falls_back_to_the_snapshot() -> None:
    """A legacy host must degrade, never raise inside the render callback."""
    now = 100.0 + 10 * _STALL_SECONDS
    bar = StatusBar(_LegacyHost(), clock=lambda: now)
    bar._model = _model(started=100.0, pushed_anchor=now - 1.0)

    assert "STALLED" not in bar._renderable().plain
