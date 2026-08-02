"""Generated console scene catalog and executable support matrix."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from rich.console import Console

from rich.text import Text

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.edit_preview import build_edit_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_entry_model import PhaseEntryModel
from ralph.display.phase_exit_model import PhaseExitModel
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.status_bar import StatusBarModel
from ralph.display.surface_catalog import SURFACE_CATALOG, SurfaceSpec
from ralph.display.theme import diff_fill_styles, make_console

Background = Literal["dark", "light", "unknown"]
ColourMode = Literal["truecolour", "reduced", "none"]
GlyphMode = Literal["unicode", "ascii"]
Destination = Literal["tty", "redirect", "ci"]
RichColorSystem = Literal["auto", "standard", "256", "truecolor", "windows"]

CONTRAST_FLOOR: Final[float] = 4.5
FULL_LAYOUT_WIDTH: Final[int] = 80
GRACEFUL_WIDTH_FLOOR: Final[int] = 40
GRACEFUL_HEIGHT_FLOOR: Final[int] = 12
INDENT_UNIT: Final[str] = "  "
_SCENE_PROMPT_PATH: Final[str] = ".agent/PROMPT.md"

CANONICAL_VALUE_FORMATS: Final[dict[str, str]] = {
    "duration": "<minutes>m<seconds>s",
    "count": "count=<decimal>",
    "path": "workspace=<verbatim-or-folded-path>",
    "identifier": "[category][agent-id]",
}


@dataclass(frozen=True)
class SupportCase:
    """One deterministic rendering configuration in the support matrix."""

    background: Background
    colour: ColourMode
    glyphs: GlyphMode
    width: int
    destination: Destination

    @property
    def terminal_background_is_light(self) -> bool | None:
        """Return the background preference consumed by semantic display themes."""
        if self.background == "unknown":
            return None
        return self.background == "light"

    @property
    def color_system(self) -> RichColorSystem | None:
        """Return the Rich color-system name for this support case."""
        if self.colour == "truecolour":
            return "truecolor"
        if self.colour == "reduced":
            return "256"
        return None

    @property
    def height(self) -> int:
        """Return a stable height that supports the catalog's reference scenes."""
        return GRACEFUL_HEIGHT_FLOOR


SCENE_NAMES: Final[tuple[str, ...]] = (
    "first_screen",
    "clean_run",
    "failure",
    "burst",
    "idle_stretch",
    "closing_screen",
)


def render_scene(
    scene_name: str,
    case: SupportCase,
    *,
    terminal_bg_is_light: bool | None,
) -> str:
    """Render a deterministic reference scene through production display seams.

    The catalog is a driver, not a second renderer: lifecycle, activity,
    status-bar, completion, and preview text are produced by the public owners
    used by a live run. Fixed clocks and in-memory streams retain the support
    matrix's deterministic, bounded test profile.
    """
    if scene_name not in SCENE_NAMES:
        raise ValueError(f"unknown scene {scene_name!r}")
    if terminal_bg_is_light != case.terminal_background_is_light:
        raise ValueError("terminal background must match the support case")
    stream = StringIO()
    console = make_console(
        file=stream,
        no_color=case.colour == "none",
        force_terminal=case.destination in {"tty", "ci"} and case.colour != "none",
        color_system=case.color_system,
        width=case.width,
        height=case.height,
    )
    context = make_display_context(
        console=console,
        env=_scene_environment(case),
        force_width=case.width,
        force_height=case.height,
    )
    display = ParallelDisplay(
        context,
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        monotonic=lambda: 123.0,
    )
    # Generated captures are deterministic matrix fixtures: bypass the
    # terminal probe after construction so the requested case, rather than
    # the host terminal, selects the semantic palette.
    display._terminal_bg_is_light = case.terminal_background_is_light
    console.print(Text(f"SCENE {scene_name}", style="theme.cat.meta"))
    _drive_production_scene(display, console, context, scene_name)
    if scene_name in {"clean_run", "burst"}:
        _render_scene_previews(console, terminal_bg_is_light=terminal_bg_is_light)
    display.stop()
    return stream.getvalue()


def _scene_environment(case: SupportCase) -> dict[str, str]:
    """Return explicit environment inputs for one support-matrix case."""
    environment: dict[str, str] = {"RALPH_FORCE_ASCII": "1" if case.glyphs == "ascii" else "0"}
    if case.background != "unknown":
        environment["RALPH_TERMINAL_BG"] = case.background
    if case.colour == "none":
        environment["NO_COLOR"] = "1"
    elif case.destination in {"redirect", "ci"}:
        environment["FORCE_COLOR"] = "1"
    return environment


def _scene_snapshot(*, failed: bool) -> PipelineSnapshot:
    """Build fixed production completion input without pipeline execution."""
    return PipelineSnapshot(
        phase="failed" if failed else "complete",
        previous_phase="review",
        review_issues_found=failed,
        interrupted_by_user=False,
        last_error="tests failed: assertion output retained" if failed else None,
        pr_url=None,
        push_count=0,
        total_agent_calls=3,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=_SCENE_PROMPT_PATH,
        prompt_preview=(),
        run_id="scene-run",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        plan_summary="Render production display surfaces",
        plan_scope_items=("display",),
        plan_total_steps=1,
        plan_current_step=1,
        decision_log=(
            ("review", "revise" if failed else "proceed", "scene evidence", "2026-01-02T03:04:05+00:00"),
        ),
        is_terminal_success=not failed,
        is_terminal_failure=failed,
    )


def _drive_production_scene(
    display: ParallelDisplay,
    console: Console,
    context: DisplayContext,
    scene_name: str,
) -> None:
    """Drive public production display entry points with fixed scenario data."""
    if scene_name == "first_screen":
        display.emit_welcome_banner(version="0.0.0-scene")
        display.emit_first_run_panel([Text("Production display scene")])
        display.emit_run_start(
            RunStartOrientation(
                prompt_path=_SCENE_PROMPT_PATH,
                workspace_root="/work/café",
                developer_agent="pi",
                plan_present=True,
            )
        )
    elif scene_name == "clean_run":
        entry = PhaseEntryModel(
            "development", "execution", "pi", outer_dev_iteration=1, outer_dev_cap=3
        )
        display.begin_phase("development")
        display.emit_phase_start_from_entry(entry)
        if context.width > GRACEFUL_WIDTH_FLOOR:
            display.emit_activity_line("pi", "text", "implemented Unicode-safe output")
            display.emit_activity_line("pi", "thinking", "checking preview hierarchy")
        display.emit_phase_transition("development", "review")
        display.emit_metrics_table({"events": 2, "artifacts": 1})
        display.emit_info_panel(title="Production note", content="Preview and records stay recoverable.")
        display.emit_missing_plan_hint()
        display.emit_phase_close_from_exit(
            PhaseExitModel(
                "development", "execution", "pi", artifact_outcome="artifacts ready", content_blocks=1
            )
        )
    elif scene_name == "failure":
        raw_machine_detail = "tests failed: assertion output retained; " + "trace-detail " * 48
        display.emit_activity_line("reviewer", "error", raw_machine_detail)
        display.emit_warn_line(
            "reviewer",
            "warning",
            "raw machine detail is retained in .agent/raw/reviewer.log",
        )
        display.emit_completion_summary_panel(
            _scene_snapshot(failed=True), options=CompletionSummaryOptions(elapsed_seconds=123.0)
        )
    elif scene_name == "burst":
        display.emit_activity_line(
            "codex", "tool_use", "edit_file path=café.py", tool_signature=("edit_file", "café.py")
        )
        display.emit_activity_line("codex", "tool_result", "edit_file complete")
        display.emit_activity_line(
            "codex",
            "raw",
            "output condensed count=3 bytes=96",
            condensed_flag=True,
            condensed_ref=".agent/raw/run.log",
        )
    elif scene_name == "idle_stretch":
        model = StatusBarModel(
            workspace_root="/work/café",
            phase_label="Development",
            phase_style="theme.phase.development",
            elapsed_seconds=123.0,
            agent_name="pi",
            attention="waiting",
        )
        # The public push seam writes a durable state transition on redirected
        # and CI streams; real TTYs retain the sole transient Live footer.
        display.update_status_bar(model)
    else:
        display.emit_run_end(phase="complete", total_agent_calls=3)
        display.emit_completion_summary_panel(
            _scene_snapshot(failed=False), options=CompletionSummaryOptions(elapsed_seconds=123.0)
        )


def _render_scene_previews(console: Console, *, terminal_bg_is_light: bool | None) -> None:
    """Render real write and diff previews through the production preview builder."""
    write_preview = build_edit_preview(
        "write_file",
        {"path": "café.py", "content": "def café(value: str) -> int:\n    return len(value)"},
        width=console.width,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    diff_preview = build_edit_preview(
        "edit_file",
        {
            "path": "café.py",
            "edits": [{"oldText": "return 0", "newText": "return len(value)", "start_line": 2}],
        },
        width=console.width,
        terminal_bg_is_light=terminal_bg_is_light,
        diff_fills=diff_fill_styles(terminal_bg_is_light),
    )
    if write_preview is not None:
        console.print(write_preview)
    if diff_preview is not None:
        console.print(diff_preview)


def support_matrix() -> tuple[SupportCase, ...]:
    """Return the complete declared support matrix.

    The full matrix is a documentation aid; tests that drive the matrix
    across every scene should use :func:`floor_matrix` instead, which
    keeps the same declared dimensions but limits the width sweep to
    the floor and the full layout, so the immutable 60-second combined
    test budget holds.
    """
    backgrounds: tuple[Background, ...] = ("dark", "light", "unknown")
    colours: tuple[ColourMode, ...] = ("truecolour", "reduced", "none")
    glyph_modes: tuple[GlyphMode, ...] = ("unicode", "ascii")
    destinations: tuple[Destination, ...] = ("tty", "redirect", "ci")
    return tuple(
        SupportCase(background, colour, glyphs, width, destination)
        for background in backgrounds
        for colour in colours
        for glyphs in glyph_modes
        for width in (GRACEFUL_WIDTH_FLOOR, FULL_LAYOUT_WIDTH, 120)
        for destination in destinations
    )


def floor_matrix() -> tuple[SupportCase, ...]:
    """Return the floor-bullet matrix for the per-scene visual-floor tests.

    The product criteria declares that the support matrix MUST include
    at minimum an undetermined background, a reduced-colour mode, a
    no-colour mode, an ASCII-only glyph mode, a narrowest width no
    wider than the standard default terminal, and one destination for
    each of TTY, redirect, and CI. The floor matrix therefore carries
    every background x every colour x every glyph x every destination,
    but only the graceful-width floor and the full-layout width: 120
    columns adds a third width point without changing the floor
    bullets and is covered by the per-surface preview/regression
    tests instead. That keeps the per-scene matrix to 54 cases
    (3 x 3 x 2 x 3) rather than the support matrix's 162, so the
    matrix-driven test fits the immutable combined 60 s budget.
    """
    backgrounds: tuple[Background, ...] = ("dark", "light", "unknown")
    colours: tuple[ColourMode, ...] = ("truecolour", "reduced", "none")
    glyph_modes: tuple[GlyphMode, ...] = ("unicode", "ascii")
    destinations: tuple[Destination, ...] = ("tty", "redirect", "ci")
    return tuple(
        SupportCase(background, colour, glyphs, width, destination)
        for background in backgrounds
        for colour in colours
        for glyphs in glyph_modes
        for width in (GRACEFUL_WIDTH_FLOOR, FULL_LAYOUT_WIDTH)
        for destination in destinations
    )


__all__ = [
    "CANONICAL_VALUE_FORMATS",
    "CONTRAST_FLOOR",
    "FULL_LAYOUT_WIDTH",
    "GRACEFUL_HEIGHT_FLOOR",
    "GRACEFUL_WIDTH_FLOOR",
    "INDENT_UNIT",
    "SCENE_NAMES",
    "SURFACE_CATALOG",
    "SupportCase",
    "SurfaceSpec",
    "floor_matrix",
    "render_scene",
    "support_matrix",
]
