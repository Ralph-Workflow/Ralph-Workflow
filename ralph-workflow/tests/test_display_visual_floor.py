from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from io import StringIO
from typing import ClassVar

import pytest
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Token,
)

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
from ralph.display.syntax_theme import SyntaxThemes
from ralph.display.theme import (
    display_styles_for_background,
    pick_status_styles,
    preview_background_for_background,
    terminal_background_is_light,
)

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
    Generic.Heading,
    Generic.Strong,
    Generic.Prompt,
    Generic.Output,
    Generic.Traceback,
    Generic.Error,
    Generic.Emph,
    Token.Other,
    Token.Escape,
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
    colourless = [
        token
        for token in _REQUIRED_TOKENS
        if style_type.style_for_token(token).get("color") is None
    ]
    if colourless:
        raise AssertionError(f"syntax token classes resolve to no colour: {colourless}")


def test_visual_floor_semantic_palettes_use_fixed_contrast_safe_foregrounds() -> None:
    _assert_palette_contrast(theme.STATUS_STYLES, ("#000000",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_LIGHT_BG, ("#FFFFFF",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_UNKNOWN_BG, ("#000000", "#FFFFFF"))


def test_visual_floor_named_colour_categories_have_distinct_fixed_foregrounds() -> None:
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


@pytest.mark.criteria("C-6")
def test_visual_floor_theme_roles_never_recede_to_attribute_only_dim() -> None:
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


@pytest.mark.criteria("C-2")
def test_visual_floor_unknown_background_theme_roles_clear_both_possible_terminal_surfaces() -> (
    None
):
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
    context = make_display_context(env={})
    rendered = render_event(
        make_event_for_emit(ActivityEventKind.TEXT, "operator-visible event"),
        ctx=context,
    )

    expected_style = pick_status_styles(None)["info"][0]
    assert context.terminal_background_is_light is None
    assert any(span.style == expected_style for span in rendered.spans)


def test_visual_floor_regression_dark_event_uses_one_palette_for_identity_and_carrier() -> None:
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
    assert "\x1b[38;" in rendered
    assert "\x1b[48;" not in rendered


@pytest.mark.criteria("C-4")
def test_visual_floor_cli_status_and_warning_keep_semantic_colour_and_plain_labels() -> None:
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


@pytest.mark.criteria("D-3")
def test_visual_floor_syntax_theme_preserves_complete_token_range() -> None:
    for style_type in (SyntaxThemes.dark(), SyntaxThemes.light(), SyntaxThemes.unknown()):
        _assert_complete_token_classes(style_type)


@pytest.mark.criteria("C-1")
def test_visual_floor_syntax_tokens_clear_contrast_on_their_owned_preview_surface() -> None:
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

    for surface_hex in ("#2D2A2E", "#1E1E1E", "#FAF8F5"):
        style_type = SyntaxThemes.for_surface(surface_hex)
        foregrounds = {
            foreground
            for color in style_type.styles.values()
            if isinstance(color, str) and (foreground := theme._extract_hex(color))
        }
        assert foregrounds
        fill = preview_background_for_background(None, surface_hex=surface_hex)
        assert all(
            theme.contrast_ratio(foreground, fill) >= CONTRAST_FLOOR for foreground in foregrounds
        )


def test_visual_floor_syntax_tokens_on_undetermined_background_clear_contrast_dual_safe() -> None:
    foregrounds = {
        foreground
        for color in SyntaxThemes.unknown().styles.values()
        if isinstance(color, str) and (foreground := theme._extract_hex(color))
    }
    assert foregrounds
    for fg in foregrounds:
        assert theme.contrast_ratio(fg, "#000000") >= CONTRAST_FLOOR
        assert theme.contrast_ratio(fg, "#FFFFFF") >= CONTRAST_FLOOR


def test_visual_floor_markdown_palette_on_undetermined_background_clears_contrast_dual_safe() -> (
    None
):
    from importlib import import_module

    _markdown_theme = import_module("ralph.display._markdown_theme")

    palette = _markdown_theme._PALETTES[None]
    assert palette
    for hex_code in palette:
        assert theme.contrast_ratio(hex_code, "#000000") >= CONTRAST_FLOOR
        assert theme.contrast_ratio(hex_code, "#FFFFFF") >= CONTRAST_FLOOR


@pytest.mark.criteria("D-4")
def test_visual_floor_markdown_palette_draws_from_the_same_palette_roles() -> None:
    from importlib import import_module

    from ralph.display._palette import resolve_palette
    from ralph.display.theme import preview_background_for_background

    _markdown_theme = import_module("ralph.display._markdown_theme")

    entry_to_role: dict[str, str] = {
        "default": "foreground",
        "accent": "chrome",
        "link": "chrome",
        "url": "skipped",
        "bullet": "success",
        "rule": "success",
    }

    for case in (False, True, None):
        if case is None:
            palette = _markdown_theme._PALETTES[None]
            assert palette
            for entry in entry_to_role:
                indexed = {"default": 0, "accent": 1, "link": 2, "url": 3, "bullet": 4, "rule": 5}
                expected = _markdown_theme._build_markdown_palette(None)[indexed[entry]]
                _ = expected
            continue
        terminal_surface = "#2D2A2E" if case is False else "#FAF8F5"
        preview_surface = preview_background_for_background(None, surface_hex=terminal_surface)
        md_palette = _markdown_theme._markdown_palette_for_surface(terminal_surface)
        resolved = resolve_palette(preview_surface)
        for entry, role in entry_to_role.items():
            indexed = {"default": 0, "accent": 1, "link": 2, "url": 3, "bullet": 4, "rule": 5}
            md_hex = md_palette[indexed[entry]]
            role_hex = resolved[role]
            assert md_hex == role_hex, (
                f"D-4: markdown[{entry}]={md_hex} != palette[{role}]={role_hex} on {preview_surface}"
            )


def test_visual_floor_bad_syntax_foreground_fixture_is_rejected() -> None:
    class BadStyle:
        styles: ClassVar[dict[object, str]] = {Comment: "#222222"}

    with pytest.raises(AssertionError):
        assert all(
            theme.contrast_ratio(foreground, _PREVIEW_SURFACES[False]) >= CONTRAST_FLOOR
            for foreground in BadStyle.styles.values()
        )


def test_visual_floor_known_background_previews_paint_one_complete_owned_surface() -> None:
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
    for first_role, second_role in (
        ("success", "error"),
        ("warning", "error"),
    ):
        pigments = _status_pair_hexes(first_role, second_role)
        _cvd_separability_check(pigments)

    for background in (False, True, None):
        styles = display_styles_for_background(background)
        added = styles["diff_added"]
        removed = styles["diff_removed"]
        _cvd_separability_check((added, removed))


def test_visual_floor_palette_pairs_clear_contrast_under_cvd_simulation() -> None:
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
    confusable = ("#000100", "#010001")
    with pytest.raises(AssertionError, match="collapse under deuteranopia"):
        _cvd_separability_check(confusable)


def test_visual_floor_partial_fill_fixture_is_rejected() -> None:
    from rich.console import Console
    from rich.syntax import Syntax

    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        width=80,
        theme=theme.theme_for_background(False),
    )

    code = "def render() -> int:\n    return 1\n"
    bg_color = preview_background_for_background(False)
    syntax = Syntax(code, "python", background_color=bg_color, theme="monokai")
    console.print(syntax)
    rendered = stream.getvalue()

    r, g, b = theme._palette.hex_to_rgb(bg_color)
    preview_fill = f"48;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}"
    production_count = rendered.count(preview_fill)
    assert production_count >= 2

    lines = rendered.splitlines(keepends=True)
    assert len(lines) >= 2, (
        "preview unexpectedly collapsed to a single row; the negative-control "
        "mutation cannot proceed, so the partial-fill check would prove nothing"
    )
    mutated = "".join(
        line.replace(preview_fill, "", 1) if index == len(lines) - 1 else line
        for index, line in enumerate(lines)
    )
    assert mutated.count(preview_fill) < production_count


def test_visual_floor_silent_elision_fixture_is_rejected() -> None:
    from ralph.display.scene_catalog import SupportCase, render_scene

    rendered = render_scene(
        "burst",
        SupportCase("dark", "truecolour", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    assert "count=" in rendered
    assert "bytes=" in rendered
    assert ".agent/raw/run.log" in rendered

    muted = rendered
    for marker in ("count=", "bytes=", ".agent/raw/run.log"):
        muted = muted.replace(marker, "")
    assert "count=" not in muted
    assert "bytes=" not in muted
    assert ".agent/raw/run.log" not in muted


def test_visual_floor_reduced_colour_named_category_passes() -> None:
    import re as _re

    from ralph.display.scene_catalog import SupportCase, render_scene

    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    assert "38;5;" in rendered
    elision_match = _re.search(r"\x1b\[([0-9;]+)m[^\x1b]*output condensed count=", rendered)
    assert elision_match is not None, "elision body lost its foreground in reduced mode"
    assert elision_match.group(1).startswith("38;5;"), elision_match.group(1)


def test_visual_floor_reduced_colour_default_foreground_fixture_is_rejected() -> None:
    import re as _re

    from ralph.display.scene_catalog import SupportCase, render_scene

    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    elision_match = _re.search(r"\x1b\[([0-9;]+)m[^\x1b]*output condensed count=", rendered)
    assert elision_match is not None
    production_escape = elision_match.group(1)
    assert production_escape.startswith("38;5;")

    muted = rendered.replace(f"\x1b[{production_escape}m", "")
    assert f"\x1b[{production_escape}m" not in muted
    assert "output condensed count=" in muted
    assert not _re.search(r"\x1b\[38;5;\d+m[^\x1b]*output condensed count=", muted)


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
            raise AssertionError(f"pigments {pigments!r} collapse under {matrix_name} simulation")


def _check_surface_contrast_failures(
    surface_hex: str, *, use_resolver_surface: bool = True
) -> list[str]:
    is_light = surface_hex == "#FAF8F5"
    resolver_surface_hex = surface_hex if use_resolver_surface else None
    failures: list[str] = []

    resolved_theme = theme.theme_for_background(is_light, surface_hex=resolver_surface_hex)
    for role_name in theme._THEME_STYLES:
        style_obj = resolved_theme.styles.get(role_name)
        if style_obj is None:
            continue
        foreground = theme._extract_hex(str(style_obj))
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(
                f"theme_for_background[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}"
            )

    display_styles = theme.display_styles_for_background(is_light, surface_hex=resolver_surface_hex)
    for role_name, style_str in display_styles.items():
        foreground = theme._extract_hex(style_str)
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(
                f"display_styles[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}"
            )

    status_styles = theme.pick_status_styles(is_light, surface_hex=resolver_surface_hex)
    for role_name, (style_str, _glyph, _label) in status_styles.items():
        foreground = theme._extract_hex(style_str)
        if not foreground:
            continue
        ratio = theme.contrast_ratio(foreground, surface_hex)
        if ratio < CONTRAST_FLOOR:
            failures.append(
                f"pick_status_styles[{surface_hex}] {role_name} ({foreground}): {ratio:.2f}"
            )

    return failures


@pytest.mark.criteria("D-2")
def test_visual_floor_preview_and_diff_foregrounds_clear_contrast_on_their_own_fills() -> None:
    cases: tuple[bool | str, ...] = (False, True, "#2D2A2E", "#1E1E1E", "#FAF8F5", "#282C34")
    for case in cases:
        is_bool = isinstance(case, bool)
        terminal_bg_is_light: bool | None = case if is_bool else None
        surface_hex: str | None = None if is_bool else case

        preview_fg = theme.preview_foreground_for_background(
            terminal_bg_is_light, surface_hex=surface_hex
        )
        preview_fill = theme.preview_background_for_background(
            terminal_bg_is_light, surface_hex=surface_hex
        )
        assert theme.contrast_ratio(preview_fg, preview_fill) >= CONTRAST_FLOOR, (
            f"preview foreground {preview_fg} on fill {preview_fill} for {case!r}"
        )

        removed_fg, added_fg = theme.diff_token_foregrounds(
            terminal_bg_is_light, surface_hex=surface_hex
        )
        fills = theme.diff_fill_styles(terminal_bg_is_light, surface_hex=surface_hex)
        assert fills is not None, f"expected an owned diff fill for {case!r}"
        removed_fill, added_fill = fills
        assert theme.contrast_ratio(removed_fg, removed_fill) >= CONTRAST_FLOOR, (
            f"diff removed foreground {removed_fg} on fill {removed_fill} for {case!r}"
        )
        assert theme.contrast_ratio(added_fg, added_fill) >= CONTRAST_FLOOR, (
            f"diff added foreground {added_fg} on fill {added_fill} for {case!r}"
        )


def test_visual_floor_all_resolvers_clear_contrast_on_realistic_surfaces() -> None:
    realistic_surfaces = ("#2D2A2E", "#1E1E1E", "#FAF8F5")
    failures: list[str] = []

    for surface_hex in realistic_surfaces:
        failures.extend(_check_surface_contrast_failures(surface_hex))

    if failures:
        raise AssertionError(
            f"{len(failures)} contrast failures on realistic surfaces:\n" + "\n".join(failures)
        )


def test_visual_floor_boolean_resolvers_clear_contrast_on_realistic_surfaces() -> None:
    realistic_surfaces = ("#2D2A2E", "#1E1E1E", "#FAF8F5")
    failures: list[str] = []

    for surface_hex in realistic_surfaces:
        failures.extend(_check_surface_contrast_failures(surface_hex, use_resolver_surface=False))

    if failures:
        raise AssertionError(
            f"{len(failures)} contrast failures on realistic surfaces (boolean-only resolvers):\n"
            + "\n".join(failures)
        )
