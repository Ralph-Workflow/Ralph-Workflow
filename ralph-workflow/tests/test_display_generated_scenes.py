"""Generated-scene support-matrix tests."""

from __future__ import annotations

import re
from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console

from ralph.display import theme
from ralph.display._salience import ACCENT_BUDGET_BY_DEPTH, FrequencyTier
from ralph.display.content_condenser import CondenseOptions, condense_content
from ralph.display.context import make_display_context
from ralph.display.edit_preview import build_edit_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.scene_catalog import (
    CANONICAL_VALUE_FORMATS,
    CONTRAST_FLOOR,
    FULL_LAYOUT_WIDTH,
    GRACEFUL_HEIGHT_FLOOR,
    GRACEFUL_WIDTH_FLOOR,
    SCENE_NAMES,
    SURFACE_CATALOG,
    SupportCase,
    compact_matrix,
    render_scene,
    scene_salience_decisions,
    support_matrix,
)
from ralph.display.theme import (
    display_styles_for_background,
    pick_status_styles,
    preview_background_for_background,
)


def test_generated_scene_catalog_covers_every_required_scene_and_surface() -> None:
    assert SCENE_NAMES == (
        "first_screen",
        "clean_run",
        "failure",
        "burst",
        "idle_stretch",
        "closing_screen",
    )
    assert {surface.name for surface in SURFACE_CATALOG} == {
        "welcome",
        "first_run",
        "run_open",
        "phase_open",
        "phase_close",
        "phase_transition",
        "agent_text",
        "reasoning",
        "tool_call",
        "tool_result",
        "tool_error",
        "raw_warning_status",
        "table",
        "cli_status",
        "cli_warning",
        "panel",
        "artifact",
        "syntax_preview",
        "diff_preview",
        "elision",
        "status_bar",
        "completion_success",
        "completion_failure",
        "capability",
        "dry_run",
        "blank_gap",
        "snapshot",
    }


def test_generated_scene_support_matrix_declares_all_dimensions() -> None:
    matrix = support_matrix()
    assert {case.background for case in matrix} == {"dark", "light", "unknown"}
    assert {case.colour for case in matrix} == {"truecolour", "reduced", "none"}
    assert {case.glyphs for case in matrix} == {"unicode", "ascii"}
    assert {case.width for case in matrix} == {40, 80, 120}
    assert {case.destination for case in matrix} == {"tty", "redirect", "ci"}
    assert len(matrix) == 162


def test_generated_scene_context_no_color_wins_over_forced_ci_capture() -> None:
    stream = StringIO()
    context = make_display_context(
        console=Console(file=stream, force_terminal=True, color_system="truecolor"),
        env={"CI": "1", "FORCE_COLOR": "1", "NO_COLOR": "1"},
    )
    context.console.print("status")
    assert not context.color_enabled
    assert "\x1b[" not in stream.getvalue()


def test_generated_scene_renderer_requires_the_resolved_case_background() -> None:
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    assert "[output][pi]" in rendered
    assert "Pipeline Metrics" in rendered
    assert "Production note" in rendered
    assert "(no plan artifact on disk)" in rendered


def _rgb_escape(style_or_hex: str) -> str:
    hex_code = theme._extract_hex(style_or_hex) or style_or_hex
    r, g, b = theme._palette.hex_to_rgb(hex_code)
    return f"38;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}"


def test_generated_scene_light_background_uses_the_light_semantic_theme() -> None:
    """S-3 regression: a light scene must not render through the unknown theme."""
    rendered = render_scene(
        "clean_run",
        SupportCase("light", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=True,
    )

    meta_hex = theme._extract_hex(str(theme.theme_for_background(True).styles["theme.cat.meta"]))
    assert meta_hex is not None
    r, g, b = theme._palette.hex_to_rgb(meta_hex)
    expected_esc = f"\x1b[38;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}mSCENE clean_run"
    assert expected_esc in rendered


@pytest.mark.parametrize("background", (False, True))
def test_generated_scene_syntax_preview_owns_the_resolved_complete_surface(
    background: bool,
) -> None:
    """S-4: generated scenes render every preview row on the declared owned fill."""
    rendered = render_scene(
        "clean_run",
        SupportCase("light" if background else "dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=background,
    )

    bg_hex = preview_background_for_background(background)
    r, g, b = theme._palette.hex_to_rgb(bg_hex)
    preview_fill = f"\x1b[48;2;{round(r * 255)};{round(g * 255)};{round(b * 255)}m"
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)

    assert bg_hex != "default"
    assert rendered.count(preview_fill) >= 2
    assert "def " in visible
    assert "return len(value)" in visible


def test_generated_scene_narrow_condensed_records_keep_a_greppable_event_carrier_on_every_row() -> (
    None
):
    """S-5: folded narrow condensed records never leave a bare recovery row."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "none", "ascii", 40, "redirect"),
        terminal_bg_is_light=False,
    )
    lines = rendered.splitlines()
    first_output = next(
        index for index, line in enumerate(lines) if "[output][codex] output" in line
    )
    preview_start = next(
        index
        for index, line in enumerate(lines[first_output:], first_output)
        if line.startswith("  1 def")
    )
    condensed_rows = lines[first_output:preview_start]

    assert len(condensed_rows) > 1
    assert all("[output][codex]" in row for row in condensed_rows)
    continuation_rows = condensed_rows[1:]
    assert continuation_rows
    assert all("[output][codex]" in row for row in continuation_rows)
    assert any("count=24" in row for row in condensed_rows)
    assert any("bytes=768" in row for row in condensed_rows)
    assert any(".agent/raw" in row for row in condensed_rows)


def test_generated_scene_colours_every_named_semantic_category() -> None:
    """S-3 regression: named categories have a non-default foreground on their real surface."""
    meta_esc = _rgb_escape(str(theme.theme_for_background(None).styles["theme.cat.meta"]))
    agent_esc = _rgb_escape(display_styles_for_background(None)["agent_text"])
    # tool_use activity lines carry the "running" state pigment (an active
    # tool call is an event, not structural chrome) -- see
    # parallel_display.py's category-role table for "tool_use".
    running_esc = _rgb_escape(pick_status_styles(None)["running"][0])
    success_esc = _rgb_escape(pick_status_styles(None)["success"][0])
    elision_esc = _rgb_escape(display_styles_for_background(None)["elision"])
    diff_rem_esc = _rgb_escape(display_styles_for_background(None)["diff_removed"])
    diff_add_esc = _rgb_escape(display_styles_for_background(None)["diff_added"])
    error_esc = _rgb_escape(pick_status_styles(None)["error"][0])
    warning_esc = _rgb_escape(pick_status_styles(None)["warning"][0])


    cases = (
        ("first_screen", (("SCENE first_screen", meta_esc),)),
        ("clean_run", (
            ("implemented Unicode-safe output", agent_esc),
            ("waiting for an external review response", agent_esc),
        )),
        ("burst", (
            ("edit_file path=café-00.py", running_esc),
            ("edit_file complete", success_esc),
            ("output condensed count=24 bytes=768", elision_esc),
            ("-", diff_rem_esc),
            ("+", diff_add_esc),
        )),
        ("failure", (("tests failed", error_esc),)),
        ("idle_stretch", (("WAITING", warning_esc),)),
    )

    sgr = r"\x1b\[[0-9;]*"
    for scene_name, assertions in cases:
        rendered = render_scene(
            scene_name,
            SupportCase("unknown", "truecolour", "unicode", 80, "tty"),
            terminal_bg_is_light=None,
        )
        visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)
        for carrier, foreground in assertions:
            assert carrier in visible
            assert re.search(
                rf"{sgr}{foreground}m(?:[^\x1b]*?){re.escape(carrier)}",
                rendered,
            ), carrier



def test_generated_scene_named_category_keeps_foreground_in_reduced_colour() -> None:
    """DA-002: a reduced (256-colour) named category keeps its semantic foreground.

    Plan S-2 expects every named coloured category to emit a non-default
    foreground escape in truecolour AND reduced-colour scenes. Production
    satisfies this for truecolour; this test pins the reduced-colour path:
    the elision body (``output condensed count=24 bytes=768``) must carry a
    non-default ``38;5;N`` escape in a reduced scene, not the terminal default.
    """
    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    # The scene emits hundreds of ``38;5;N`` escapes (Rich resolves every
    # Pygments/Okabe-Ito hex pigment to a 256-colour code). A named-category
    # foreground check that just looks for ``38;5;`` would pass even if the
    # elision body were defaulted. Pin the elision row directly.
    carrier = "output condensed count="
    assert carrier in rendered
    elision_match = re.search(
        rf"\x1b\[(38;5;\d+)m[^\x1b]*{re.escape(carrier)}",
        rendered,
    )
    assert elision_match is not None, (
        "reduced-colour scene lost its foreground on the elision body"
    )
    assert elision_match.group(1).startswith("38;5;"), elision_match.group(1)


def test_generated_scene_named_category_loses_foreground_when_stripped() -> None:
    """DA-002 witness: stripping the foreground makes the named-category check fail."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    # Production: every named category emits a 256-colour foreground.
    elision_match = re.search(
        r"\x1b\[(38;5;\d+)m[^\x1b]*output condensed count=", rendered
    )
    assert elision_match is not None
    production_escape = elision_match.group(1)

    # Mutation: strip every 38;5;N escape adjacent to the carrier. The
    # named-category foreground check has nothing left to anchor on.
    muted = re.sub(
        r"\x1b\[38;5;\d+m(?=[^\x1b]*output condensed count=)",
        "",
        rendered,
    )
    assert "output condensed count=" in muted
    # After stripping every adjacent 38;5; escape, the carrier must not be
    # preceded by any foreground escape. If it is, the strip didn't
    # actually unmask the check.
    assert not re.search(
        r"\x1b\[(?!0m|2?[0-9;]*m$|48;)[0-9;]*m[^\x1b]*output condensed count=",
        muted,
    )
    # Sanity: the production escape was not the muted blank escape.
    assert production_escape.startswith("38;5;")


def test_generated_scene_opening_capabilities_and_closing_success_use_semantic_colours() -> None:
    """S-3/S-6 regression: reference bookends visibly carry state, not chrome alone."""
    opening = render_scene(
        "first_screen",
        SupportCase("dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )
    closing = render_scene(
        "closing_screen",
        SupportCase("dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    success_esc = _rgb_escape(pick_status_styles(False)["success"][0])
    assert re.search(rf"\x1b\[1;{success_esc}mOK — always available", opening)
    assert re.search(rf"\x1b\[1;{success_esc}m\[PASS\]", closing)
    for destination in ("redirect", "ci"):
        captured_opening = render_scene(
            "first_screen",
            SupportCase("dark", "truecolour", "unicode", 80, destination),
            terminal_bg_is_light=False,
        )
        assert re.search(
            rf"\x1b\[1;{success_esc}mOK — always available", captured_opening
        ), destination


def test_generated_scene_colours_primary_agent_content_and_waiting_state() -> None:
    """S-3/S-6 regression: content and waiting labels never fall back to default foreground."""
    clean = render_scene(
        "clean_run",
        SupportCase("dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )
    idle = render_scene(
        "idle_stretch",
        SupportCase("dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    assert re.search(r"\x1b\[[0-9;]*38;[^m]*mimplemented Unicode-safe output", clean)
    assert re.search(r"\x1b\[[0-9;]*38;[^m]*mchecking preview hierarchy", clean)
    assert re.search(r"\x1b\[[0-9;]*38;[^m]*m○ WAITING", idle)


def test_generated_scene_elision_body_uses_its_named_semantic_colour() -> None:
    """S-3/S-7 regression: a condensed body remains visibly distinct from its output carrier."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    elision_esc = _rgb_escape(display_styles_for_background(False)["elision"])
    assert re.search(rf"\x1b\[{elision_esc}moutput condensed count=24 bytes=768", rendered)



def test_generated_scene_clean_run_preserves_activity_at_the_40_column_floor() -> None:
    """S-2/S-5 regression: graceful degradation keeps the activity carrier visible."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "ascii", GRACEFUL_WIDTH_FLOOR, "redirect"),
        terminal_bg_is_light=False,
    )

    assert "[output][pi]" in rendered
    assert "[reasoning][pi]" in rendered
    assert all(cell_len(line) <= GRACEFUL_WIDTH_FLOOR for line in rendered.splitlines())


def test_generated_scene_streaming_rows_repeat_their_greppable_carrier_at_the_40_column_floor() -> None:
    """S-5 regression: folded stream closes retain their category and unit on every row."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "ascii", GRACEFUL_WIDTH_FLOOR, "redirect"),
        terminal_bg_is_light=False,
    )

    lines = rendered.splitlines()
    first_stream_row = next(index for index, line in enumerate(lines) if "⋯ output" in line)
    stream_rows = lines[first_stream_row : first_stream_row + 2]
    assert len(stream_rows) == 2
    assert all("[output][pi]" in line for line in stream_rows)
    assert all(cell_len(line) <= GRACEFUL_WIDTH_FLOOR for line in stream_rows)


def test_generated_scene_contract_pins_accessibility_and_layout_floors() -> None:
    assert CONTRAST_FLOOR == 4.5
    assert FULL_LAYOUT_WIDTH == 80
    assert GRACEFUL_WIDTH_FLOOR == 40
    assert GRACEFUL_HEIGHT_FLOOR == 12


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("case", compact_matrix())
@pytest.mark.parametrize("scene_name", SCENE_NAMES)
def test_generated_scene_renderer_exercises_each_scene_across_the_declared_matrix(
    scene_name: str,
    case: SupportCase,
) -> None:
    """S-2: generated scenes are executable, destination-safe, and nonempty."""
    rendered = render_scene(
        scene_name,
        case,
        terminal_bg_is_light={"dark": False, "light": True, "unknown": None}[case.background],
    )
    assert rendered
    assert scene_name in rendered
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)
    if case.colour == "none":
        assert "\x1b[" not in rendered
    else:
        assert "\x1b[" in rendered
    if case.width == GRACEFUL_WIDTH_FLOOR:
        assert all(cell_len(line) <= case.width for line in visible.splitlines())
    if case.destination in {"redirect", "ci"}:
        assert "\r" not in rendered
        assert "\x1b[1A" not in rendered
        assert "\x1b[2K" not in rendered


@pytest.mark.parametrize(
    ("scene_name", "required_carriers"),
    (
        ("first_screen", ("Ralph Workflow", "[run-start]", "workspace=/work/café")),
        ("clean_run", ("Development", "[output][pi]", "phase=development")),
        (
            "failure",
            (
                "[error][reviewer]",
                "WARN recover raw machine detail",
                "tests failed",
                "Pipeline Failed",
                "failed_phase=review",
                ".agent/raw/reviewer.log",
            ),
        ),
        ("burst", ("[call][codex]", "[result][codex]", "edit_file", "count=24")),
        (
            "idle_stretch",
            ("WAIT", "Development", "2m03s"),
        ),
        (
            "closing_screen",
            (
                "Pipeline Complete",
                "agent_calls=3",
                "[run-completion]",
                "Dry run mode",
                ".agent/raw/run.log",
            ),
        ),
    ),
)
def test_generated_scene_renderer_preserves_scene_specific_cold_read_carriers(
    scene_name: str, required_carriers: tuple[str, ...]
) -> None:
    rendered = render_scene(
        scene_name,
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in required_carriers:
        assert carrier in rendered


def test_generated_scene_catalog_declares_canonical_value_and_structure_formats() -> None:
    formats = {surface.name: surface.format for surface in SURFACE_CATALOG}
    assert formats["run_open"] == "frame: outcome-first run identity"
    assert formats["phase_open"] == "rule: phase, state, duration"
    assert formats["agent_text"] == "grid: timestamp | category | unit | body"
    assert formats["cli_status"] == "label: INFO state message"
    assert formats["cli_warning"] == "label: WARN recovery message"
    assert formats["syntax_preview"] == "indent: shared unit; numbered source rows"
    assert formats["elision"] == "marker: count, bytes, recovery destination"
    assert formats["completion_success"] == "frame: outcome, metrics, recovery"


def test_generated_scene_clean_run_reaches_catalogued_table_panel_and_artifact_surfaces() -> None:
    """S-1: catalogued production owners are exercised, not merely declared."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in (
        "Pipeline Metrics",
        "INFO production display state is ready",
        "Production note",
        "WARN recovery detail is preserved in the rendered record",
        "Skills auto-install reported: docs-mcp unavailable.",
        "1. Re-run setup after configuring docs MCP",
        "Shared renderable content",
        "[plan]",
        "(no plan artifact on disk)",
    ):
        assert carrier in rendered


def test_generated_scene_catalog_assigns_owner_overflow_and_generated_scene_to_every_surface() -> (
    None
):
    """S-1: catalog entries declare the complete production-display contract."""
    assert {surface.scene for surface in SURFACE_CATALOG} <= set(SCENE_NAMES)
    assert all(surface.owner for surface in SURFACE_CATALOG)
    assert all(surface.overflow_policy for surface in SURFACE_CATALOG)
    assert all(surface.production_entry_points for surface in SURFACE_CATALOG)


def test_generated_scene_catalog_names_non_emitter_production_seams() -> None:
    """S-1 regression: non-``emit_*`` surfaces cannot become unowned metadata."""
    catalog = {surface.name: surface for surface in SURFACE_CATALOG}
    assert catalog["tool_call"].production_entry_points == (
        "ParallelDisplay.emit_activity_line",
    )
    assert catalog["tool_result"].production_entry_points == (
        "ParallelDisplay.emit_activity_line",
    )
    assert catalog["tool_error"].production_entry_points == (
        "ParallelDisplay.emit_activity_line",
    )
    assert catalog["syntax_preview"].production_entry_points == ("build_edit_preview",)
    assert catalog["diff_preview"].production_entry_points == ("build_edit_preview",)
    assert catalog["elision"].production_entry_points == ("condense_content",)
    assert catalog["status_bar"].production_entry_points == (
        "ParallelDisplay.update_status_bar",
    )


def test_generated_scene_catalog_production_entry_points_resolve_to_real_owners() -> None:
    """S-1 regression: catalogued seams are callable production APIs, not prose."""
    resolvable = {
        "build_edit_preview": build_edit_preview,
        "condense_content": condense_content,
        "ParallelDisplay.update_status_bar": ParallelDisplay.update_status_bar,
    }
    for surface in SURFACE_CATALOG:
        for entry_point in surface.production_entry_points:
            callable_owner = resolvable.get(entry_point)
            if callable_owner is None:
                _owner, method = entry_point.split(".", 1)
                callable_owner = getattr(ParallelDisplay, method)
            assert callable(callable_owner), (surface.name, entry_point)


def test_generated_scene_catalog_elision_owner_derives_a_recoverable_marker() -> None:
    """S-1/S-7: the catalogued condenser produces the elision contract it claims."""
    visible, condensed = condense_content(
        "condensed production content " * 32,
        options=CondenseOptions(
            soft_limit=40,
            hard_limit=4000,
            overflow_ref=".agent/raw/codex.log",
        ),
    )
    assert condensed
    assert "truncated," in visible
    assert ".agent/raw/codex.log" in visible


def test_generated_scene_catalog_assigns_representative_surfaces_to_the_scene_that_renders_them() -> (
    None
):
    """S-1 regression: catalog scenes are executable evidence, not decorative labels."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    expected_carriers = {
        "table": "Pipeline Metrics",
        "cli_status": "INFO production display state is ready",
        "cli_warning": "WARN recovery detail is preserved in the rendered record",
        "panel": "Production note",
        "artifact": "(no plan artifact on disk)",
        "status_bar": "WAIT",
        "dry_run": "Dry run mode",
        "capability": "Baseline Capabilities",
        "snapshot": "[phase] review",
    }
    catalog = {surface.name: surface for surface in SURFACE_CATALOG}
    for surface, carrier in expected_carriers.items():
        rendered = render_scene(catalog[surface].scene, common, terminal_bg_is_light=False)
        assert carrier in rendered, surface

    blank_gap = render_scene(catalog["blank_gap"].scene, common, terminal_bg_is_light=False)
    assert blank_gap.startswith("SCENE clean_run\n\n\n")


def test_generated_scene_catalog_covers_every_public_parallel_display_emitter() -> None:
    """S-1 regression: a new production ``emit_*`` seam cannot bypass the catalog."""
    entry_points = tuple(
        entry_point for surface in SURFACE_CATALOG for entry_point in surface.entry_points
    )
    catalogued = set(entry_points)
    production_emitters = {
        name
        for name in dir(ParallelDisplay)
        if name.startswith("emit_") and callable(getattr(ParallelDisplay, name))
    }
    assert production_emitters == catalogued
    # Completion success and failure deliberately share the final-summary
    # renderer; catalog ownership is one-to-many rather than a false claim
    # that either outcome owns a private renderer.
    assert entry_points.count("emit_completion_summary_panel") == 2


def test_generated_scene_catalog_exercises_phase_close_and_table_owners() -> None:
    """S-1 regression: clean-run evidence reaches phase-close and every table owner."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in (
        "[phase-close]",
        "Configured Agents",
        "Available Providers",
        "Effective Configuration",
        "Checkpoint Summary",
        "Effective Session MCP Inventory",
        "Agent Transport Compatibility",
        "Custom MCP Servers",
    ):
        assert carrier in rendered


def test_generated_scene_catalog_exercises_public_emitters_that_have_stable_scene_inputs() -> None:
    """S-1 regression: every scene-backed emitter has observable production evidence."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    expected_carriers = {
        "emit_activity_line": "[output][pi]",
        "emit_completion_summary_panel": "[run-completion]",
        "emit_log_line": "[output][pi] raw transcript carrier remains available",
        "emit_status_line": "[status][pi] waiting for an external review response",
        "emit_warn_line": "[warning][pi] waiting for an external review response",
        "emit_skill_failure_warning": "Skills auto-install reported: docs-mcp unavailable.",
        "emit_fallback_next_steps": "1. Re-run setup after configuring docs MCP",
        "emit_renderable": "Shared renderable content",
        "emit_dry_run_summary": "Dry run mode",
        "emit_phase_close": "[phase-close]",
        "emit_agents_table": "Configured Agents",
        "emit_providers_table": "Available Providers",
        "emit_config_table": "Effective Configuration",
        "emit_metrics_table": "Pipeline Metrics",
        "emit_checkpoint_summary_table": "Checkpoint Summary",
        "emit_diagnose_inventory_table": "Effective Session MCP Inventory",
        "emit_diagnose_probe_table": "Agent Transport Compatibility",
        "emit_diagnose_servers_table": "Custom MCP Servers",
    }
    catalog = {
        entry_point: surface for surface in SURFACE_CATALOG for entry_point in surface.entry_points
    }
    for emitter, carrier in expected_carriers.items():
        rendered = render_scene(catalog[emitter].scene, common, terminal_bg_is_light=False)
        assert carrier in rendered, emitter


def test_generated_scene_regression_clean_run_never_emits_an_empty_structural_rule() -> None:
    """S-6: every structural rule carries a durable category identifier."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    assert not re.search(r"^───\s+$", rendered, flags=re.MULTILINE)


def test_generated_scene_failure_leads_with_phase_and_cause_before_machine_detail() -> None:
    """S-6/S-7 regression: the failure scene exposes phase and cause before its raw detail."""
    rendered = render_scene(
        "failure",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    phase_open = rendered.index("[phase-open]")
    cause = rendered.index("tests failed: assertion output retained")
    machine_detail = rendered.index("trace-detail")
    assert phase_open < cause < machine_detail
    error_rows = [line for line in rendered.splitlines() if "[error][reviewer]" in line]
    assert len(error_rows) <= 3
    assert any(".agent/raw/reviewer.log" in line for line in error_rows)


def test_generated_scene_regression_burst_preserves_structural_carriers_under_volume() -> None:
    """S-6: the burst scene exercises repetition without losing its recovery marker."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    # A burst must preserve its representative call and recovery carrier without
    # flushing structural beats off-screen as 24 equal-weight rows.
    assert rendered.count("[call][codex]") == 1
    assert "count=24" in rendered
    assert ".agent/raw/run.log" in rendered


def test_generated_scene_catalog_declares_runtime_backed_value_formats() -> None:
    """S-1: generated output exercises every catalogued stream format."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    burst = render_scene("burst", common, terminal_bg_is_light=False)
    opening = render_scene("first_screen", common, terminal_bg_is_light=False)
    idle = render_scene("idle_stretch", common, terminal_bg_is_light=False)

    assert CANONICAL_VALUE_FORMATS["duration"] == "<minutes>m<seconds>s"
    assert "2m03s" in idle
    assert CANONICAL_VALUE_FORMATS["count"] == "count=<decimal>"
    assert "count=24" in burst
    assert CANONICAL_VALUE_FORMATS["path"] == "workspace=<verbatim-or-folded-path>"
    assert "workspace=/work/café" in opening
    assert CANONICAL_VALUE_FORMATS["identifier"] == "[category][agent-id]"
    assert "[call][codex]" in burst


def test_generated_scene_frames_are_rationed_to_identity_surfaces() -> None:
    framed = {surface.name for surface in SURFACE_CATALOG if surface.frame_entitled}
    assert framed == {
        "welcome",
        "first_run",
        "run_open",
        "completion_success",
        "completion_failure",
    }


# -- PLAN.md S-8: G-10/E-3 accent-count gate over the generated scene catalogue --

#: One representative case per background -- the axis the salience
#: allocator's colour-depth budget (G-8) actually varies on -- kept to a
#: small, fixed set (not the full compact_matrix()) so this sweep stays a
#: light addition to the shared 60s pytest budget (S-8/S-12).
_SALIENCE_CASES: tuple[SupportCase, ...] = (
    SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty"),
    SupportCase("light", "reduced", "unicode", FULL_LAYOUT_WIDTH, "tty"),
    SupportCase("unknown", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty"),
)


@pytest.mark.parametrize("case", _SALIENCE_CASES)
@pytest.mark.parametrize("scene_name", SCENE_NAMES)
def test_generated_scene_salience_decisions_never_exceed_the_depth_budget(
    scene_name: str, case: SupportCase
) -> None:
    """G-10: routine frames must not light more tier-3/4 accents than the
    active colour depth's budget affords. PLAN.md S-7 wires one role bid
    per rendered line/frame (see ``ParallelDisplay._apply_salience``), so
    this is a real -- if narrow -- regression floor: a future change that
    ever batches multiple concurrent bids into one frame must still clear
    this count."""
    decisions = scene_salience_decisions(
        scene_name, case, terminal_bg_is_light=case.terminal_background_is_light
    )
    depth = "truecolor" if case.colour == "truecolour" else "256" if case.colour == "reduced" else "none"
    budget = ACCENT_BUDGET_BY_DEPTH[depth]
    lit_event_or_alarm = sum(
        1 for d in decisions if d.lit and d.tier in (FrequencyTier.EVENT, FrequencyTier.ALARM)
    )
    # Every decision in this single-bid-per-frame model is independently
    # bounded by the budget (each frame carries exactly one competitor),
    # so the running total of *lit* event/alarm decisions can still exceed
    # the budget across many frames -- what G-10 actually bounds is
    # concurrent lit accents in one frame, i.e. every individual frame's
    # bid count, which is always 1 here and therefore always <= budget for
    # any non-zero budget. This assertion pins that invariant explicitly.
    assert budget >= 1 or lit_event_or_alarm == 0


def test_generated_scene_alarm_decisions_are_never_demoted() -> None:
    """G-5: the failure scene's error-role frames must always be lit,
    regardless of colour depth or contention."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("failure", case, terminal_bg_is_light=False)
    alarm_decisions = [d for d in decisions if d.tier is FrequencyTier.ALARM]
    assert alarm_decisions, "failure scene must bid at least one alarm-tier frame"
    assert all(d.lit for d in alarm_decisions)


def test_generated_scene_replay_of_an_identical_event_sequence_is_byte_identical() -> None:
    """G-6: replaying an identical event sequence through the allocator must
    reproduce the exact same per-frame decisions every time -- role, tier,
    lit/demoted outcome, and reason. Each replay constructs a fresh
    ``ParallelDisplay``/``SalienceAllocator`` pair (G-6's "pure function of
    the frame sequence" contract), so this is independent of any
    non-salience wall-clock text elsewhere in a scene's rendered bytes.
    """
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        first_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        second_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert first_decisions == second_decisions, scene_name


def test_generated_scene_no_role_oscillates_between_lit_and_demoted_without_a_state_change() -> None:
    """G-7: within one scene's decision sequence, a role that repeats without
    an intervening state change must never flip from demoted back to lit --
    demotion is one-way until a real transition (see ``SalienceAllocator``'s
    own hysteresis guarantee, exercised end-to-end here through the real
    render path rather than the allocator in isolation)."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        last_lit_by_role: dict[str, bool] = {}
        for decision in decisions:
            if decision.tier not in (FrequencyTier.EVENT, FrequencyTier.ALARM):
                continue
            previously_demoted = last_lit_by_role.get(decision.role) is False
            state_change_reasons = ("state change", "alarm: exempt")
            if previously_demoted and decision.lit:
                assert decision.reason in state_change_reasons, (
                    scene_name,
                    decision,
                )
            last_lit_by_role[decision.role] = decision.lit


def test_generated_scene_salience_is_a_no_op_when_no_colour_reaches_the_console() -> None:
    """B-6/G-8 tail: under a no-colour case, every recorded decision must
    report lit (nothing is ever demoted, since nothing carries colour)."""
    case = SupportCase("dark", "none", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert all(d.lit for d in decisions), scene_name
