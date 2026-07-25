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

from ralph.display.context import make_display_context
from ralph.display.status_bar import StatusBar, StatusBarModel


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
