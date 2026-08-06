"""Tests for DisplayContext and make_display_context factory.

After the wt-028-display consolidation, DisplayContext exposes a single
``default`` mode with one fixed set of adaptive limits. The
``compact`` / ``medium`` / ``wide`` tier is gone, and
``RALPH_FORCE_NARROW`` is silently ignored. The historical per-mode
constants (``COMPACT_HEADLINE_MAX_CHARS`` etc.) are removed.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from ralph.display import DisplayContext as DisplayContextExport
from ralph.display import make_display_context as make_display_context_export
from ralph.display import theme as _theme
from ralph.display._mode_adaptive_limits import (
    CONDENSER_HARD_LIMIT,
    CONDENSER_SOFT_LIMIT,
    HEADLINE_MAX_CHARS,
    STREAMING_CHECKPOINT_CHARS,
    THINKING_PREVIEW_MIN_CHARS,
    TOOL_RESULT_HEADLINE_MIN_CHARS,
)
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.scene_catalog import CONTRAST_FLOOR
from ralph.display.theme import pick_status_styles, theme_for_background

_NARROW_TEST_WIDTH = 40
_WIDE_TEST_WIDTH = 200


@pytest.mark.parametrize("width", [40, 60, 80, 100, 120, 200])
def test_width_is_preserved_for_any_input_width(width: int) -> None:
    """Single default-mode invariant: input width is preserved on the context."""
    console = Console(width=width, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == width


def test_force_width_overrides_console_width() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={}, force_width=_NARROW_TEST_WIDTH)
    assert ctx.width == _NARROW_TEST_WIDTH


def test_columns_env_overrides_console_width() -> None:
    """Env ``COLUMNS`` overrides the default console width when Ralph built the console.

    When the caller does NOT pass an explicit ``console=`` argument,
    ``injected_console`` is False and ``COLUMNS`` wins over the
    console's default width. When the caller DOES pass an explicit
    ``console=`` argument, the console's own width is AUTHORITATIVE
    (see ``_compute_width_uncached`` -- ``test_force_width_overrides_console_width``
    pins the explicit-console case); mixing the two would silently
    widen a 40-column test fixture under a host ``COLUMNS=200``.
    """
    ctx = make_display_context(env={"COLUMNS": str(_NARROW_TEST_WIDTH)})
    assert ctx.width == _NARROW_TEST_WIDTH


def test_force_width_takes_precedence_over_columns_env() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(
        console=console, env={"COLUMNS": str(_NARROW_TEST_WIDTH)}, force_width=_WIDE_TEST_WIDTH
    )
    assert ctx.width == _WIDE_TEST_WIDTH


def test_no_color_env_disables_color() -> None:
    console = Console(width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.color_enabled is False


def test_color_enabled_by_default() -> None:
    console = Console(width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.color_enabled is True


def test_display_context_regression_default_console_emits_color_off_tty() -> None:
    """S-2: the default color-enabled context emits ANSI to a non-TTY stream."""
    ctx = make_display_context(env={"RALPH_TERMINAL_BG": "dark"})
    with ctx.console.capture() as output:
        ctx.console.print("[theme.phase.development]development[/]")

    assert "\x1b[38;2;" in output.get()


def test_default_mode_uses_single_fixed_limits() -> None:
    """Single default-mode uses one fixed set of adaptive limits."""
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.headline_max_chars == HEADLINE_MAX_CHARS
    assert ctx.condenser_soft_limit == CONDENSER_SOFT_LIMIT
    assert ctx.condenser_hard_limit == CONDENSER_HARD_LIMIT
    assert ctx.streaming_checkpoint_chars == STREAMING_CHECKPOINT_CHARS
    assert ctx.thinking_preview_min_chars == THINKING_PREVIEW_MIN_CHARS
    assert ctx.tool_result_headline_min_chars == TOOL_RESULT_HEADLINE_MIN_CHARS


def test_default_mode_limits_constant_for_any_width() -> None:
    """Single default-mode uses the same limits regardless of width."""
    narrow = make_display_context(console=Console(width=40, force_terminal=True), env={})
    wide = make_display_context(console=Console(width=200, force_terminal=True), env={})
    assert narrow.headline_max_chars == wide.headline_max_chars
    assert narrow.condenser_soft_limit == wide.condenser_soft_limit
    assert narrow.condenser_hard_limit == wide.condenser_hard_limit
    assert narrow.streaming_checkpoint_chars == wide.streaming_checkpoint_chars


def test_display_context_is_frozen() -> None:
    params = getattr(DisplayContext, "__dataclass_params__", None)
    assert params is not None and getattr(params, "frozen", False) is True


def test_display_context_has_theme_for_resolved_background() -> None:
    ctx = make_display_context(env={})
    expected = theme_for_background(ctx.terminal_background_is_light)
    assert set(ctx.theme.styles) == set(expected.styles)
    assert {
        name: str(style) for name, style in ctx.theme.styles.items()
    } == {
        name: str(style) for name, style in expected.styles.items()
    }


def test_make_display_context_creates_console_when_none() -> None:
    ctx = make_display_context(env={})
    assert ctx.console is not None
    assert isinstance(ctx.width, int)
    assert ctx.width > 0


def test_console_passed_in_is_used() -> None:
    console = Console(width=80)
    ctx = make_display_context(console=console, env={})
    assert ctx.console is console


def test_display_context_exported_from_display_package() -> None:
    assert DisplayContextExport is DisplayContext
    assert make_display_context_export is make_display_context


# --- wt-028-display S-5 / AC-04: wide-terminal measure cap. ----------------
# The measure cap keeps prose and log body text at a comfortable
# column count on very wide terminals (e.g. 250 cols) so the
# operator's eye does not have to track a 250-character line. Rules,
# tables, and aligned columns continue to use the full ``width``;
# the cap only applies to prose-shaped content.


def test_body_measure_caps_at_measure_cap_on_wide_terminal() -> None:
    """S-5: 250-column console → body measure is the 100-column cap, not 250."""
    from ralph.display._mode_adaptive_limits import BODY_MEASURE_CAP

    ctx = make_display_context(console=Console(width=250, force_terminal=True), env={})
    assert ctx.width == 250
    assert ctx.body_measure_cap == BODY_MEASURE_CAP
    assert ctx.body_measure() == BODY_MEASURE_CAP, (
        f"body_measure() on 250-col console must cap at {BODY_MEASURE_CAP}, "
        f"got {ctx.body_measure()}"
    )


def test_body_measure_returns_full_width_when_narrower_than_cap() -> None:
    """S-5: 80-column console → body measure is the full 80 (cap is a no-op)."""
    ctx = make_display_context(console=Console(width=80, force_terminal=True), env={})
    assert ctx.width == 80
    assert ctx.body_measure() == 80


def test_body_measure_handles_very_wide_terminal() -> None:
    """S-5: 500-column console → body measure still capped (does not grow)."""
    from ralph.display._mode_adaptive_limits import BODY_MEASURE_CAP

    ctx = make_display_context(console=Console(width=500, force_terminal=True), env={})
    assert ctx.body_measure() == BODY_MEASURE_CAP


def test_body_measure_floor_at_40_columns() -> None:
    """S-5: 30-column console → body measure floors at 40 (token wrap contract)."""
    ctx = make_display_context(console=Console(width=30, force_terminal=True), env={})
    assert ctx.body_measure() == 40, (
        f"body_measure() must floor at 40 columns, got {ctx.body_measure()}"
    )


def test_body_measure_survives_refreshed() -> None:
    """S-5: ``refreshed()`` preserves the cap on the new context."""
    from ralph.display._mode_adaptive_limits import BODY_MEASURE_CAP

    ctx = make_display_context(console=Console(width=250, force_terminal=True), env={})
    refreshed = ctx.refreshed()
    assert refreshed.body_measure_cap == ctx.body_measure_cap == BODY_MEASURE_CAP
    assert refreshed.body_measure() == BODY_MEASURE_CAP


# --- DA-003 / DA-004: measured-surface hex validation and threading. -------


@pytest.mark.parametrize("malformed", ["#zzzzzz", "#12345"])
def test_display_context_rejects_malformed_terminal_background_hex(malformed: str) -> None:
    """DA-003: a malformed RALPH_TERMINAL_BG hex must not crash display
    construction and must not be threaded, unvalidated, into the palette
    solver."""
    ctx = make_display_context(
        env={"RALPH_TERMINAL_BG": malformed}, console=Console(file=io.StringIO())
    )
    assert ctx.terminal_background_hex is None


def test_display_context_threads_measured_surface_hex_through_resolved_theme() -> None:
    """DA-004: a valid RALPH_TERMINAL_BG hex is carried onto the context, and
    the resolved theme resolves the same pigment as the surface-aware status
    resolver for the same surface."""
    ctx = make_display_context(
        env={"RALPH_TERMINAL_BG": "#2D2A2E"}, console=Console(file=io.StringIO())
    )
    assert ctx.terminal_background_hex == "#2D2A2E"

    expected_style = pick_status_styles(False, surface_hex="#2D2A2E")["warning"][0]
    expected_hex = _theme._extract_hex(expected_style)
    actual_hex = _theme._extract_hex(str(ctx.theme.styles["theme.status.warning"]))
    assert expected_hex and actual_hex.lower() == expected_hex.lower()


def test_malformed_terminal_bg_hex_falls_through_to_the_osc11_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-1: a malformed ``RALPH_TERMINAL_BG`` hex (wrong length, non-hex
    digits) must fall through to the next precedence tier -- the OSC 11
    probe -- rather than being threaded, unvalidated, into the palette
    solver or raising. ``relative_luminance`` already raises ``ValueError``
    for a malformed body; ``detect_terminal_background_hex`` must catch that
    and continue past the ``RALPH_TERMINAL_BG`` branch instead of
    propagating it or returning the malformed string.
    """
    monkeypatch.setattr(
        "ralph.display._terminal_bg_query.query_terminal_background_hex",
        lambda *, timeout: "#123456",
    )
    for malformed in ("#notahex", "#12", "#GGGGGG", "#1234567"):
        env = {"RALPH_TERMINAL_BG": malformed}
        resolved_hex = _theme.detect_terminal_background_hex(env)
        assert resolved_hex == "#123456", malformed

        # The boolean detector must not raise either -- it degrades to the
        # probe/COLORFGBG tier the same way.
        is_light = _theme.detect_terminal_background_is_light(env)
        assert is_light is not None


def test_explicit_dark_override_is_not_contradicted_by_a_light_probe_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an explicit ``RALPH_TERMINAL_BG=dark`` must resolve the same
    canonical surface for both the boolean and the hex detector, even when the
    OSC 11 probe (stubbed here) would answer with a contradicting light colour.

    Before the fix, ``detect_terminal_background_hex`` ignored the explicit
    non-hex declaration and threaded the probed ``#FFFFFF`` into the solver
    while ``detect_terminal_background_is_light`` stayed ``False`` -- a
    dark/light contradiction that resolved a *light-surface-solved* palette
    (e.g. ``success`` at ``#568316``) onto what the operator declared a dark
    terminal. DA-001 also moved the canonical dark/light tables off the pure
    ``#000000``/``#FFFFFF`` endpoints onto the representative
    ``#2D2A2E``/``#FAF8F5`` surfaces (a pigment solved to exactly the floor
    on ``#000000`` measures only ~3.05:1 on a realistic ``#2D2A2E``
    surface), so this declared-but-unmeasured path must resolve to that same
    representative surface too, or it would regress below the floor exactly
    where DA-001 restored headroom. This test pins both properties: the hex
    and boolean resolvers agree, and the surface-aware resolution collapses
    onto the same dark canonical table -- now solved with real headroom on
    ``#2D2A2E`` -- used everywhere else, not a contradicting light-solved one.
    """
    monkeypatch.setattr(
        "ralph.display._terminal_bg_query.query_terminal_background_hex",
        lambda *, timeout: "#FFFFFF",
    )
    env = {"RALPH_TERMINAL_BG": "dark"}

    resolved_hex = _theme.detect_terminal_background_hex(env)
    assert resolved_hex == "#2D2A2E"
    assert _theme.background_hex_is_light(resolved_hex) is False

    is_light = _theme.detect_terminal_background_is_light(env)
    assert is_light is False

    surface_resolved = pick_status_styles(is_light, surface_hex=resolved_hex)
    canonical = pick_status_styles(is_light)
    assert surface_resolved == canonical

    real_dark_surface = "#2D2A2E"
    broken_light_solved_success = "#568316"
    for style, _glyph, _label in surface_resolved.values():
        pigment = _theme._extract_hex(style)
        assert pigment.upper() != broken_light_solved_success
        # DA-002: an absolute floor assertion, not a self-comparison against
        # `canonical` -- `surface_resolved == canonical` above already pins
        # the two byte-for-byte, so comparing derived values against each
        # other proved nothing. This is a true witness once DA-001 restores
        # canonical headroom, and it fails if the `elif explicit:` branch in
        # `detect_terminal_background_hex` regresses to threading a pure
        # `#000000`/`#FFFFFF` endpoint (zero headroom) instead of the
        # representative surface.
        assert _theme.contrast_ratio(pigment, real_dark_surface) >= CONTRAST_FLOOR
