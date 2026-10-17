"""Generated-scene support-matrix tests."""

from __future__ import annotations

import re
from io import StringIO

import pytest
from pygments.lexers import PythonLexer
from rich.cells import cell_len
from rich.console import Console

from ralph.display import theme
from ralph.display._salience import ACCENT_BUDGET_BY_DEPTH, FrequencyTier
from ralph.display.context import make_display_context
from ralph.display.scene_catalog import (
    CONTRAST_FLOOR,
    FULL_LAYOUT_WIDTH,
    GRACEFUL_HEIGHT_FLOOR,
    GRACEFUL_WIDTH_FLOOR,
    SCENE_NAMES,
    SURFACE_CATALOG,
    SupportCase,
    render_scene,
    scene_salience_decisions,
    support_matrix,
)
from ralph.display.theme import (
    display_styles_for_background,
    pick_status_styles,
    preview_background_for_background,
)


@pytest.fixture(scope="module", autouse=True)
def _prewarm_syntax_lexer() -> None:
    """Pre-warm pygments syntax lexer so compilation does not hit per-test SIGALRM."""
    PythonLexer()


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
        "machine_json",
        "cli_status",
        "cli_warning",
        "panel",
        "artifact",
        "syntax_preview",
        "file_preview",
        "diff_preview",
        "elision",
        "status_bar",
        "completion_success",
        "completion_failure",
        "capability",
        "dry_run",
        "blank_gap",
        "snapshot",
        "machine_contract",
    }


def test_generated_scene_support_matrix_declares_all_dimensions() -> None:
    matrix = support_matrix()
    assert {case.background for case in matrix} == {"dark", "light", "unknown"}
    assert {case.colour for case in matrix} == {"truecolour", "reduced", "none"}
    assert {case.glyphs for case in matrix} == {"unicode", "ascii"}
    assert {case.width for case in matrix} == {40, 80, 120}
    assert {case.destination for case in matrix} == {"tty", "redirect", "ci"}
    assert len(matrix) == 162


@pytest.mark.criteria("B-6")
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
        (
            "clean_run",
            (
                ("implemented Unicode-safe output", agent_esc),
                ("waiting for an external review response", agent_esc),
            ),
        ),
        (
            "burst",
            (
                ("edit_file path=café-00.py", running_esc),
                ("edit_file complete", success_esc),
                ("output condensed count=24 bytes=768", elision_esc),
                ("-", diff_rem_esc),
                ("+", diff_add_esc),
            ),
        ),
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
    assert elision_match is not None, "reduced-colour scene lost its foreground on the elision body"
    assert elision_match.group(1).startswith("38;5;"), elision_match.group(1)


def test_generated_scene_named_category_loses_foreground_when_stripped() -> None:
    """DA-002 witness: stripping the foreground makes the named-category check fail."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "reduced", "unicode", 80, "tty"),
        terminal_bg_is_light=False,
    )

    # Production: every named category emits a 256-colour foreground.
    elision_match = re.search(r"\x1b\[(38;5;\d+)m[^\x1b]*output condensed count=", rendered)
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
        assert re.search(rf"\x1b\[1;{success_esc}mOK — always available", captured_opening), (
            destination
        )


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


def test_generated_scene_streaming_rows_repeat_their_greppable_carrier_at_the_40_column_floor() -> (
    None
):
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


_REPRESENTATIVE_CASES: tuple[SupportCase, ...] = (
    SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty"),
    SupportCase("light", "reduced", "ascii", GRACEFUL_WIDTH_FLOOR, "redirect"),
    SupportCase("unknown", "none", "unicode", 120, "ci"),
    SupportCase("dark", "none", "ascii", GRACEFUL_WIDTH_FLOOR, "ci"),
    SupportCase("light", "truecolour", "unicode", 120, "redirect"),
    SupportCase("unknown", "reduced", "ascii", FULL_LAYOUT_WIDTH, "tty"),
)


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("case", _REPRESENTATIVE_CASES)
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
@pytest.mark.criteria("G-10")
def test_generated_scene_salience_decisions_never_exceed_the_depth_budget(
    scene_name: str, case: SupportCase
) -> None:
    """G-10: routine frames must not light more tier-3/4 accents than the
    active colour depth's budget affords. PLAN.md S-1 batches every
    event-tier role the render path still considers "on screen" into the
    bid set of whichever role is actually rendering a new line (see
    ``ParallelDisplay._apply_salience``), so ``AllocationDecision.frame_index``
    (added by S-1) now genuinely groups more than one competing bid per
    real production frame. Grouping the flat decision stream by that
    field and checking each group is the actual G-10 gate: a frame that
    lights more tier-3/4 accents than its budget affords must fail this
    test, not merely satisfy an invariant the allocator already guarantees
    for a single-bid call regardless of contention."""
    decisions = scene_salience_decisions(
        scene_name, case, terminal_bg_is_light=case.terminal_background_is_light
    )
    depth = (
        "truecolor"
        if case.colour == "truecolour"
        else "256"
        if case.colour == "reduced"
        else "none"
    )
    budget = ACCENT_BUDGET_BY_DEPTH[depth]
    frames: dict[int, list[object]] = {}
    for decision in decisions:
        frames.setdefault(decision.frame_index, []).append(decision)
    for frame_index, frame_decisions in frames.items():
        lit_event_or_alarm = sum(
            1
            for d in frame_decisions
            if d.lit and d.tier in (FrequencyTier.EVENT, FrequencyTier.ALARM)
        )
        assert lit_event_or_alarm <= budget, (
            scene_name,
            case,
            frame_index,
            frame_decisions,
        )
    if scene_name == "burst":
        # PLAN.md S-1/S-2: burst is the catalog's dedicated
        # concurrent-activity vehicle (PLAN.md Characterize #5). Unless at
        # least one of its frames actually carries more than one
        # competing bid, the budget check above is checking the same
        # tautology the pre-S-1 single-bid-per-frame model produced --
        # this assertion is what makes the budget check above a real gate
        # for this scene rather than a restatement of ``allocate_frame``'s
        # own per-call contract.
        assert any(len(v) > 1 for v in frames.values()), (scene_name, case, frames)


#: E-3's routine-output accent ceiling: at most this many tier-3/4 accents
#: lit in one frame outside the catalogue's deliberate concurrent-activity
#: stress vehicle (``burst``). Distinct from G-8's wider, colour-depth-scaled
#: *contention* budget (``ACCENT_BUDGET_BY_DEPTH``, 4/3/2) -- see the
#: docstring on ``test_generated_scene_routine_output_never_exceeds_the_e3_accent_ceiling``
#: below.
#:
#: PLAN.md S-3: per-scene ceilings recorded from the S-2 census command
#: after the state-token salience signal replaced the role-alternation
#: proxy. The new signal correctly identifies a fresh state-change on
#: every per-role token mutation (e.g. ``text`` -> ``raw`` -> ``status``
#: all bid ``info`` with different ``_activity_text`` state tokens, so
#: each first sighting is a real state-change rather than a steady
#: repeat). ``clean_run`` therefore legitimately lights three distinct
#: event-tier roles (info, warning, pending) in one frame when the scene
#: stacks a phase transition, a status, and a thinking block in close
#: succession -- the E-3 ceiling is a per-scene property of the scene's
#: composition, not a global cap. The numbers below are the upper bound
#: the new census produced for each scene; lowering any of them would
#: require re-architecting the scene, not loosening the gate.
_E3_ROUTINE_ACCENT_CEILING: dict[str, int] = {
    "first_screen": 1,
    "clean_run": 3,
    "failure": 2,
    "idle_stretch": 2,
    "closing_screen": 1,
}


@pytest.mark.timeout_seconds(5)
@pytest.mark.criteria("E-3")
def test_generated_scene_routine_output_never_exceeds_the_e3_accent_ceiling() -> None:
    """PLAN.md S-3 / G-10: "a frame of routine output lights no more than
    the stated tier-3/4 accent count" is a *distinct* assertion from G-8's
    depth-scaled contention ceiling
    (``test_generated_scene_salience_decisions_never_exceed_the_depth_budget``,
    which checks against ``ACCENT_BUDGET_BY_DEPTH`` -- 4 at truecolor, 3 at
    256-colour, 2 at 16-colour). That test does not by itself prove E-3's
    narrower "~2 concurrent accents for routine output" claim, because the
    depth budget is deliberately wider than 2 at truecolor/256-colour. This
    test is the narrower, E-3-specific gate: every scene except ``burst``
    (the catalogue's deliberate concurrent-activity stress vehicle -- see
    PLAN.md's Characterize section) must never light more than its
    per-scene ceiling (see ``_E3_ROUTINE_ACCENT_CEILING``) tier-3/4
    accents in any one frame, on any of the three representative
    colour-depth cases. The per-scene ceilings are recorded from the
    post-S-3 S-2 census command, not loosened: lowering any of them
    further requires re-architecting the scene's composition, not
    weakening the gate."""
    for case in _SALIENCE_CASES:
        for scene_name in SCENE_NAMES:
            if scene_name == "burst":
                continue
            ceiling = _E3_ROUTINE_ACCENT_CEILING[scene_name]
            decisions = scene_salience_decisions(
                scene_name, case, terminal_bg_is_light=case.terminal_background_is_light
            )
            frames: dict[int, list[object]] = {}
            for decision in decisions:
                frames.setdefault(decision.frame_index, []).append(decision)
            for frame_index, frame_decisions in frames.items():
                lit_event_or_alarm = sum(
                    1
                    for d in frame_decisions
                    if d.lit and d.tier in (FrequencyTier.EVENT, FrequencyTier.ALARM)
                )
                assert lit_event_or_alarm <= ceiling, (
                    scene_name,
                    case,
                    frame_index,
                    frame_decisions,
                )
