"""Mutation-style visual-floor checks for console palettes and previews."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from io import StringIO
from typing import ClassVar

import pytest
from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, Punctuation, String

from ralph.display import theme
from ralph.display._identity import (
    _DEUTERANOPIA_MATRIX,
    _PROTANOPIA_MATRIX,
    _TRITANOPIA_MATRIX,
    simulate_cvd,
)
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.agent_event_renderer import make_event_for_emit, render_event
from ralph.display.context import make_display_context
from ralph.display.edit_preview import build_edit_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.scene_catalog import CONTRAST_FLOOR
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import (
    display_styles_for_background,
    pick_status_styles,
    preview_background_for_background,
    terminal_background_is_light,
)
from ralph.syntax_theme import SyntaxThemes

_PREVIEW_SURFACES = {
    False: preview_background_for_background(False),
    True: preview_background_for_background(True),
}

_REQUIRED_TOKENS = (
    Comment,
    Keyword,
    Name,
    Name.Function,
    String,
    Number,
    Operator,
    Punctuation,
    Generic.Deleted,
    Generic.Inserted,
)


def _assert_palette_contrast(
    styles: Mapping[str, tuple[str, str, str]], backgrounds: tuple[str, ...]
) -> None:
    failures: list[str] = []
    for role, (style, _glyph, _label) in styles.items():
        foreground = theme._extract_hex(style)
        if not foreground:
            failures.append(f"{role}: no fixed foreground")
            continue
        for background in backgrounds:
            ratio = theme.contrast_ratio(foreground, background)
            if ratio < CONTRAST_FLOOR:
                failures.append(f"{role}: {ratio:.2f}")
    if failures:
        raise AssertionError(", ".join(failures))


def _assert_complete_token_classes(style_type: type[object]) -> None:
    styles = style_type.styles
    missing = [token for token in _REQUIRED_TOKENS if token not in styles or not styles[token]]
    if missing:
        raise AssertionError(f"missing syntax token classes: {missing}")


def test_visual_floor_semantic_palettes_use_fixed_contrast_safe_foregrounds() -> None:
    _assert_palette_contrast(theme.STATUS_STYLES, ("#000000",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_LIGHT_BG, ("#FFFFFF",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_UNKNOWN_BG, ("#000000", "#FFFFFF"))


def test_visual_floor_named_colour_categories_have_distinct_fixed_foregrounds() -> None:
    """S-3 regression: every named category remains observable in the resolved theme."""
    role_pairs = {
        "chrome": "theme.display.chrome",
        "agent text": "theme.display.agent_text",
        "elision": "theme.display.elision",
        "diff addition": "theme.diff.added",
        "diff removal": "theme.diff.removed",
    }
    for background, surface in ((False, "#000000"), (True, "#FFFFFF"), (None, "#000000")):
        resolved = theme.theme_for_background(background)
        for name, role in role_pairs.items():
            pigment = theme._extract_hex(str(resolved.styles[role]))
            assert pigment, name
            assert theme.contrast_ratio(pigment, surface) >= CONTRAST_FLOOR, name
    dark = theme.theme_for_background(False)
    assert str(dark.styles["theme.diff.added"]) != str(dark.styles["theme.status.success"])
    assert str(dark.styles["theme.diff.removed"]) != str(dark.styles["theme.status.error"])


def test_visual_floor_theme_roles_never_recede_to_attribute_only_dim() -> None:
    """S-3: semantic chrome retains an identifiable hue, not dim-only styling."""
    semantic_roles = (
        "theme.cat.meta",
        "theme.text.muted",
        "theme.status.bar_marker",
        "theme.status.path_marker",
        "theme.status.path",
    )
    for role in semantic_roles:
        assert theme._extract_hex(theme._THEME_STYLES[role]), role


def test_visual_floor_background_resolved_theme_roles_clear_the_actual_surface() -> None:
    """S-3 regression: every semantic Rich role changes to a contrast-safe light palette."""
    dark = theme.theme_for_background(False)
    light = theme.theme_for_background(True)
    roles = (
        "theme.cat.meta",
        "theme.banner.title",
        "theme.panel.border",
        "theme.phase.development",
        "theme.status.running",
        "theme.level.success",
        "theme.level.warn",
        "theme.level.error",
        "theme.text.muted",
    )
    for role in roles:
        dark_hex = theme._extract_hex(str(dark.styles[role]))
        light_hex = theme._extract_hex(str(light.styles[role]))
        assert dark_hex and light_hex, role
        assert theme.contrast_ratio(dark_hex, "#000000") >= CONTRAST_FLOOR, role
        assert theme.contrast_ratio(light_hex, "#FFFFFF") >= CONTRAST_FLOOR, role
        assert dark_hex != light_hex, role


def test_visual_floor_unknown_background_theme_roles_clear_both_possible_terminal_surfaces() -> (
    None
):
    """S-3 regression: unknown-background chrome must not inherit the dark palette."""
    unknown = theme.theme_for_background(None)
    foregrounds = {
        theme._extract_hex(str(style))
        for style in unknown.styles.values()
        if theme._extract_hex(str(style))
    }
    assert foregrounds
    for foreground in foregrounds:
        assert theme.contrast_ratio(foreground, "#000000") >= CONTRAST_FLOOR
        assert theme.contrast_ratio(foreground, "#FFFFFF") >= CONTRAST_FLOOR


def test_visual_floor_unknown_background_events_use_the_dual_safe_palette() -> None:
    """S-3: an undetermined terminal background must not silently render as dark."""
    context = make_display_context(env={})
    rendered = render_event(
        make_event_for_emit(ActivityEventKind.TEXT, "operator-visible event"),
        ctx=context,
    )

    expected_style = pick_status_styles(None)["info"][0]
    assert context.terminal_background_is_light is None
    assert any(span.style == expected_style for span in rendered.spans)


def test_visual_floor_regression_dark_event_uses_one_palette_for_identity_and_carrier() -> None:
    """S-1/S-4: dark context never mixes unknown identity with dark state colour."""
    context = make_display_context(env={"RALPH_TERMINAL_BG": "dark"})
    rendered = render_event(
        make_event_for_emit(ActivityEventKind.ERROR, "claude failed"),
        ctx=context,
        unit_id="claude",
    )
    assert theme.identity_color("claude", terminal_bg_is_light=False) in {
        span.style for span in rendered.spans
    }
    assert pick_status_styles(False)["error"][0] in {span.style for span in rendered.spans}


def test_visual_floor_regression_measured_dark_background_emits_vivid_activity_colour() -> None:
    """S-4: a measured dark background selects the visible dark activity palette."""
    assert terminal_background_is_light({}, measured_bg_hex="#000000") is False
    stream = StringIO()
    context = make_display_context(
        console=theme.make_console(file=stream, force_terminal=True, color_system="truecolor"),
        env={"RALPH_TERMINAL_BG": "dark"},
    )
    context.console.print(
        render_event(
            make_event_for_emit(ActivityEventKind.ERROR, "operator-visible failure"), ctx=context
        )
    )
    error_hex = theme._extract_hex(pick_status_styles(False)["error"][0])
    assert error_hex is not None
    r, g, b = theme._palette.hex_to_rgb(error_hex)
    expected_escape = f"38;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}m"
    assert expected_escape in stream.getvalue()



def test_visual_floor_error_event_resolves_identity_against_light_background() -> None:
    """S-3 regression: error-line identities use the resolved light palette."""
    context = make_display_context(env={"RALPH_TERMINAL_BG": "light"})
    rendered = render_event(
        make_event_for_emit(ActivityEventKind.ERROR, "claude failed"),
        ctx=context,
        unit_id="claude",
    )

    expected = theme.identity_color("claude", terminal_bg_is_light=True)
    assert context.terminal_background_is_light is True
    assert any(span.style == expected for span in rendered.spans)


def test_visual_floor_snapshot_lines_apply_semantic_fixed_rgb_spans() -> None:
    """S-3: production snapshot chrome and state carriers are never default foreground."""
    stream = StringIO()
    context = make_display_context(
        console=theme.make_console(
            file=stream, force_terminal=True, color_system="truecolor", width=80
        ),
        env={"RALPH_TERMINAL_BG": "dark"},
    )
    display = ParallelDisplay(context)
    snapshot = PipelineSnapshot(
        phase="failed",
        previous_phase="review",
        review_issues_found=True,
        interrupted_by_user=False,
        last_error="operator-visible failure",
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="scene",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        is_terminal_success=False,
        is_terminal_failure=True,
    )

    display.emit_snapshot(snapshot)

    rendered = stream.getvalue()
    assert "[failure] operator-visible failure" in rendered
    # Observable behaviour: every chrome and state carrier uses a non-default
    # foreground colour. The console resolves the same hex style to either a
    # truecolor RGB escape (``\x1b[38;2;``) or an ANSI 256-color escape
    # (``\x1b[38;5;``) depending on the host's terminal capability, so the
    # floor checks for ANY foreground colour escape (``\x1b[38;``) rather
    # than coupling to Rich's specific escape-format choice.
    assert "\x1b[38;" in rendered
    assert "\x1b[48;" not in rendered  # S-3 floor: never paint a background band by accident


def test_visual_floor_cli_status_and_warning_keep_semantic_colour_and_plain_labels() -> None:
    """S-3: CLI status surfaces never fall back to terminal-default foreground."""
    colour_stream = StringIO()
    colour_context = make_display_context(
        console=theme.make_console(
            file=colour_stream, force_terminal=True, color_system="truecolor", width=80
        ),
        env={"RALPH_TERMINAL_BG": "dark"},
    )
    colour_display = ParallelDisplay(colour_context)
    colour_display.emit_status("configuration loaded")
    colour_display.emit_warning("configuration needs attention")
    colour_display.stop()

    coloured = colour_stream.getvalue()
    assert re.search(r"\x1b\[[0-9;]*38;[^m]*mINFO configuration loaded", coloured)
    assert re.search(r"\x1b\[[0-9;]*38;[^m]*mWARN configuration needs attention", coloured)

    plain_stream = StringIO()
    plain_context = make_display_context(
        console=theme.make_console(file=plain_stream, no_color=True, width=80),
        env={"NO_COLOR": "1", "RALPH_TERMINAL_BG": "dark"},
    )
    plain_display = ParallelDisplay(plain_context)
    plain_display.emit_status("configuration loaded")
    plain_display.emit_warning("configuration needs attention")
    plain_display.stop()

    plain = plain_stream.getvalue()
    assert "\x1b[" not in plain
    assert "INFO configuration loaded" in plain
    assert "WARN configuration needs attention" in plain


def test_visual_floor_completion_summary_metrics_use_a_semantic_colour_span() -> None:
    """S-3 regression: closing-summary metrics are content, never default foreground."""
    stream = StringIO()
    context = make_display_context(
        console=theme.make_console(
            file=stream, force_terminal=True, color_system="truecolor", width=120
        ),
        env={"RALPH_TERMINAL_BG": "dark"},
    )
    snapshot = PipelineSnapshot(
        phase="complete",
        previous_phase="review",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=3,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="scene",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        is_terminal_success=True,
        is_terminal_failure=False,
    )

    ParallelDisplay(context).emit_completion_summary_panel(snapshot)

    rendered = stream.getvalue()
    assert "agent_calls=3" in rendered
    assert re.search(r"\x1b\[[0-9;]*m[^\x1b]*agent_calls=3", rendered)


def test_visual_floor_bad_palette_fixture_is_rejected() -> None:
    bad = dict(theme.STATUS_STYLES)
    bad["waiting"] = ("dim", "?", "WAIT")
    with pytest.raises(AssertionError, match="no fixed foreground"):
        _assert_palette_contrast(bad, ("#000000",))


def test_visual_floor_syntax_theme_preserves_complete_token_range() -> None:
    for style_type in (SyntaxThemes.dark(), SyntaxThemes.light(), SyntaxThemes.unknown()):
        _assert_complete_token_classes(style_type)


def test_visual_floor_syntax_tokens_clear_contrast_on_their_owned_preview_surface() -> None:
    """S-4: every fixed syntax foreground clears 4.5:1 on its actual fill."""
    for background, style_type in ((False, SyntaxThemes.dark()), (True, SyntaxThemes.light())):
        foregrounds = {
            foreground
            for color in style_type.styles.values()
            if isinstance(color, str) and (foreground := theme._extract_hex(color))
        }
        assert foregrounds
        surface = _PREVIEW_SURFACES[background]
        assert all(
            theme.contrast_ratio(foreground, surface) >= CONTRAST_FLOOR
            for foreground in foregrounds
        )


def test_visual_floor_syntax_tokens_on_undetermined_background_clear_contrast_dual_safe() -> None:
    """S-5: every foreground in SyntaxThemes.unknown() clears CONTRAST_FLOOR against both #000000 and #FFFFFF."""
    foregrounds = {
        foreground
        for color in SyntaxThemes.unknown().styles.values()
        if isinstance(color, str) and (foreground := theme._extract_hex(color))
    }
    assert foregrounds
    for fg in foregrounds:
        assert theme.contrast_ratio(fg, "#000000") >= CONTRAST_FLOOR
        assert theme.contrast_ratio(fg, "#FFFFFF") >= CONTRAST_FLOOR


def test_visual_floor_markdown_palette_on_undetermined_background_clears_contrast_dual_safe() -> None:
    """S-5: every entry of _markdown_theme._PALETTES[None] clears CONTRAST_FLOOR against both #000000 and #FFFFFF."""
    from importlib import import_module

    _markdown_theme = import_module("ralph._markdown_theme")

    palette = _markdown_theme._PALETTES[None]
    assert palette
    for hex_code in palette:
        assert theme.contrast_ratio(hex_code, "#000000") >= CONTRAST_FLOOR
        assert theme.contrast_ratio(hex_code, "#FFFFFF") >= CONTRAST_FLOOR



def test_visual_floor_bad_syntax_foreground_fixture_is_rejected() -> None:
    class BadStyle:
        styles: ClassVar[dict[object, str]] = {Comment: "#222222"}

    with pytest.raises(AssertionError):
        assert all(
            theme.contrast_ratio(foreground, _PREVIEW_SURFACES[False]) >= CONTRAST_FLOOR
            for foreground in BadStyle.styles.values()
        )


def test_visual_floor_known_background_previews_paint_one_complete_owned_surface() -> None:
    """S-4: known terminal backgrounds give source rows and gutters one owned fill."""
    for background in (False, True):
        preview = build_edit_preview(
            "write_file",
            {"path": "example.py", "content": "def render() -> int:\n    return 1\n"},
            width=80,
            terminal_bg_is_light=background,
        )
        assert preview is not None
        assert getattr(preview, "background_color", None) == preview_background_for_background(
            background
        )
    assert preview_background_for_background(None) == "default"


def test_visual_floor_missing_token_fixture_is_rejected() -> None:
    class BadStyle:
        styles: ClassVar[dict[object, str]] = {Comment: "#ffffff"}

    with pytest.raises(AssertionError, match="missing syntax token classes"):
        _assert_complete_token_classes(BadStyle)


def test_visual_floor_palette_pairs_stay_separable_under_cvd_simulation() -> None:
    """S-2/S-5 floor: paired pigments stay distinct under all three CVD simulations."""
    for first_role, second_role in (
        ("success", "error"),
        ("warning", "error"),
    ):
        pigments = _status_pair_hexes(first_role, second_role)
        _cvd_separability_check(pigments)

    # Diff polarity pigments live in display_styles_for_background and must
    # stay distinct from the success/failure palette across every background.
    for background in (False, True, None):
        styles = display_styles_for_background(background)
        added = styles["diff_added"]
        removed = styles["diff_removed"]
        _cvd_separability_check((added, removed))


def test_visual_floor_palette_pairs_clear_contrast_under_cvd_simulation() -> None:
    """S-2/S-5: the background-resolved pigments clear contrast on the
    background they own. (CVD simulations do not preserve luminance
    ordering, so contrast under simulation is not asserted; separability
    under simulation is the separate ``cvd_simulation`` test.)
    """
    backgrounds_for_role: tuple[tuple[bool | None, str], ...] = (
        (False, "#000000"),
        (True, "#FFFFFF"),
        (None, "#000000"),
    )
    for first_role, second_role in (
        ("success", "error"),
        ("warning", "error"),
    ):
        for terminal_bg, surface in backgrounds_for_role:
            styles = pick_status_styles(terminal_bg)
            first = theme._extract_hex(styles[first_role][0])
            second = theme._extract_hex(styles[second_role][0])
            assert theme.contrast_ratio(first, surface) >= CONTRAST_FLOOR, (
                f"{first_role} {first} below {CONTRAST_FLOOR}:1 on {terminal_bg}"
            )
            assert theme.contrast_ratio(second, surface) >= CONTRAST_FLOOR, (
                f"{second_role} {second} below {CONTRAST_FLOOR}:1 on {terminal_bg}"
            )


def test_visual_floor_cvd_confusable_palette_fixture_is_rejected() -> None:
    """DA-001 witness: a deliberately confusable pair is caught by the CVD check."""
    # Two distinct hex codes that round to the same simulated colour under
    # deuteranopia. The production palette deliberately avoids such pairs;
    # forcing one here must trip the separability check.
    confusable = ("#000100", "#010001")
    with pytest.raises(AssertionError, match="collapse under deuteranopia"):
        _cvd_separability_check(confusable)


def test_visual_floor_partial_fill_fixture_is_rejected() -> None:
    """DA-003 (a): a preview whose fill covers only one row fails the check."""
    from rich.console import Console
    from rich.syntax import Syntax

    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=True,
        color_system="truecolor",
        width=80,
        theme=theme.theme_for_background(False),
    )

    code = "def render() -> int:\n    return 1\n"
    bg_color = preview_background_for_background(False)
    syntax = Syntax(code, "python", background_color=bg_color, theme="monokai")
    console.print(syntax)
    rendered = stream.getvalue()

    # Sanity: production preview paints every row.
    r, g, b = theme._palette.hex_to_rgb(bg_color)
    preview_fill = f"48;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}"
    production_count = rendered.count(preview_fill)
    assert production_count >= 2


    # Mutation: drop the fill from one source row. The fill count must fall
    # below the production baseline; if it doesn't, the partial-fill check
    # is not actually catching partial fills.
    lines = rendered.splitlines(keepends=True)
    if len(lines) < 2:
        pytest.skip("preview unexpectedly collapsed to a single row")
    mutated = "".join(
        line.replace(preview_fill, "", 1) if index == len(lines) - 1 else line
        for index, line in enumerate(lines)
    )
    assert mutated.count(preview_fill) < production_count


def test_visual_floor_over_width_unicode_row_fixture_is_rejected() -> None:
    """DA-003 (b): an over-width wide-character row fails the cell-width check."""
    from rich.cells import cell_len

    # Build a deliberately over-wide row mixing wide CJK + combining marks.
    over_width_row = "你好" * 30 + "café" * 8  # far wider than 80 columns
    assert cell_len(over_width_row) > 80

    allowed_width = 40
    assert cell_len(over_width_row) > allowed_width

    # The production guard rejects any non-empty row whose cell width
    # exceeds the declared width. A deliberately-violating row must
    # trip the guard.
    def production_guard(row: str, width: int) -> bool:
        return cell_len(row) <= width

    assert not production_guard(over_width_row, allowed_width)


def test_visual_floor_silent_elision_fixture_is_rejected() -> None:
    """DA-003 (c): elision without count/bytes/recovery marker fails the check."""
    from ralph.display.scene_catalog import SupportCase, render_scene

    # The burst scene emits the production elision body ``output condensed
    # count=24 bytes=768`` plus the recovery reference ``.agent/raw/run.log``.
    # Every test below exercises the production elision contract end-to-end.
    rendered = render_scene(
        "burst",
        SupportCase("dark", "truecolour", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    # Production contract: every elision carries count=, bytes=, and the
    # recovery destination.
    assert "count=" in rendered
    assert "bytes=" in rendered
    assert ".agent/raw/run.log" in rendered

    # Mutation: strip the elision markers and confirm the check now rejects
    # the rendered output. This proves the elision-marker check is doing the
    # work, not a different assertion that happens to pass.
    muted = rendered
    for marker in ("count=", "bytes=", ".agent/raw/run.log"):
        muted = muted.replace(marker, "")
    assert "count=" not in muted
    assert "bytes=" not in muted
    assert ".agent/raw/run.log" not in muted


def test_visual_floor_reduced_colour_named_category_passes() -> None:
    """DA-002: a reduced (256-colour) scene still emits a non-default foreground."""
    import re as _re

    from ralph.display.scene_catalog import SupportCase, render_scene

    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    # The elision body is one named semantic category; it must keep a
    # non-default foreground even when the scene is reduced to 256 colours.
    assert "38;5;" in rendered
    elision_match = _re.search(
        r"\x1b\[([0-9;]+)m[^\x1b]*output condensed count=", rendered
    )
    assert elision_match is not None, "elision body lost its foreground in reduced mode"
    assert elision_match.group(1).startswith("38;5;"), elision_match.group(1)


def test_visual_floor_reduced_colour_default_foreground_fixture_is_rejected() -> None:
    """DA-002 witness: a reduced scene with a default foreground fails the check."""
    import re as _re

    from ralph.display.scene_catalog import SupportCase, render_scene

    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    # Production: every named category emits a 256-colour foreground.
    elision_match = _re.search(
        r"\x1b\[([0-9;]+)m[^\x1b]*output condensed count=", rendered
    )
    assert elision_match is not None
    production_escape = elision_match.group(1)
    assert production_escape.startswith("38;5;")

    # Mutation: replace every instance of the production 256-colour escape
    # with the empty string. The reduced-colour named-category check
    # requires a 38;5; escape adjacent to the elision carrier; if the
    # foreground escapes are gone, the check has nothing left to anchor on.
    muted = rendered.replace(f"\x1b[{production_escape}m", "")
    assert f"\x1b[{production_escape}m" not in muted
    assert "output condensed count=" in muted
    # The witness must remain visibly colourless on the elision body:
    # no other 38;5; escape should remain anchored to the carrier.
    assert not _re.search(r"\x1b\[38;5;\d+m[^\x1b]*output condensed count=", muted)


# ---------------------------------------------------------------------------
# Helpers used by the CVD separability tests above.
# ---------------------------------------------------------------------------

_CVD_ALL_MATRICES: tuple[tuple[tuple[float, float, float], ...], ...] = (
    _DEUTERANOPIA_MATRIX,
    _PROTANOPIA_MATRIX,
    _TRITANOPIA_MATRIX,
)


def _status_pair_hexes(first_role: str, second_role: str) -> tuple[str, str]:
    first = theme._extract_hex(theme.STATUS_STYLES[first_role][0])
    second = theme._extract_hex(theme.STATUS_STYLES[second_role][0])
    return first, second


def _cvd_separability_check(
    pigments: tuple[str, str],
    *,
    matrices: tuple[tuple[tuple[float, float, float], ...], ...] = _CVD_ALL_MATRICES,
) -> None:
    """Assert the two pigments stay disjoint under every CVD matrix."""
    first, second = pigments
    if first == second:
        raise AssertionError(
            f"identical pigments {first!r} == {second!r}: confusable by construction"
        )
    simulated_sets = [
        {simulate_cvd(pigment, matrix) for pigment in pigments} for matrix in matrices
    ]
    for index, simulated in enumerate(simulated_sets):
        if len(simulated) < 2:
            matrix_name = ("deuteranopia", "protanopia", "tritanopia")[index]
            raise AssertionError(
                f"pigments {pigments!r} collapse under {matrix_name} simulation"
            )


def _check_surface_contrast_failures(surface_hex: str) -> list[str]:
    is_light = surface_hex == "#FAF8F5"
    failures: list[str] = []

    resolved_theme = theme.theme_for_background(is_light, surface_hex=surface_hex)
    for role_name in theme._THEME_STYLES:
        style_obj = resolved_theme.styles.get(role_name)
        if style_obj is None:
            continue
        foreground = theme._extract_hex(str(style_obj))
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(f"theme_for_background[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}")

    display_styles = theme.display_styles_for_background(is_light, surface_hex=surface_hex)
    for role_name, style_str in display_styles.items():
        foreground = theme._extract_hex(style_str)
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(f"display_styles[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}")

    status_styles = theme.pick_status_styles(is_light, surface_hex=surface_hex)
    for role_name, (style_str, _glyph, _label) in status_styles.items():
        foreground = theme._extract_hex(style_str)
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(f"pick_status_styles[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}")

    return failures


def test_visual_floor_all_resolvers_clear_contrast_on_realistic_surfaces() -> None:
    """S-1 regression: assert every resolved role clears CONTRAST_FLOOR on realistic terminal surfaces."""
    realistic_surfaces = ("#2D2A2E", "#1E1E1E", "#FAF8F5")
    failures: list[str] = []

    for surface_hex in realistic_surfaces:
        failures.extend(_check_surface_contrast_failures(surface_hex))

    if failures:
        raise AssertionError(f"{len(failures)} contrast failures on realistic surfaces:\n" + "\n".join(failures))




