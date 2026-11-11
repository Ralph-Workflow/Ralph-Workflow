
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.display._salience import AllocationDecision

from rich.text import Text

from ralph.config.models import UnifiedConfig
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
from ralph.skills._capability_state import CapabilityState

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
_IDLE_STRETCH_FRAMES: Final[int] = 6

CANONICAL_VALUE_FORMATS: Final[dict[str, str]] = {
    "duration": "<minutes>m<seconds>s",
    "count": "count=<decimal>",
    "path": "workspace=<verbatim-or-folded-path>",
    "identifier": "[category][agent-id]",
}


@dataclass(frozen=True)
class _SceneCheckpointOptions:

    phase: str
    budget_progress: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class SupportCase:

    background: Background
    colour: ColourMode
    glyphs: GlyphMode
    width: int
    destination: Destination

    @property
    def terminal_background_is_light(self) -> bool | None:
        if self.background == "unknown":
            return None
        return self.background == "light"

    @property
    def color_system(self) -> RichColorSystem | None:
        if self.colour == "truecolour":
            return "truecolor"
        if self.colour == "reduced":
            return "256"
        return None

    @property
    def height(self) -> int:
        return GRACEFUL_HEIGHT_FLOOR if self.width == GRACEFUL_WIDTH_FLOOR else 40


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
        terminal_bg_is_light=terminal_bg_is_light,
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
    console.print(Text(f"SCENE {scene_name}", style="theme.cat.meta"))
    _drive_production_scene(
        display,
        console,
        context,
        scene_name,
        include_capability_summary=case.width == FULL_LAYOUT_WIDTH,
    )
    if scene_name in {"clean_run", "burst"}:
        _render_scene_previews(console, terminal_bg_is_light=terminal_bg_is_light)
    display.stop()
    return stream.getvalue()


def scene_salience_decisions(
    scene_name: str,
    case: SupportCase,
    *,
    terminal_bg_is_light: bool | None,
) -> tuple[AllocationDecision, ...]:
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
        terminal_bg_is_light=terminal_bg_is_light,
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
    _drive_production_scene(
        display,
        console,
        context,
        scene_name,
        include_capability_summary=case.width == FULL_LAYOUT_WIDTH,
    )
    display.stop()
    return display.salience_decisions


def _scene_environment(case: SupportCase) -> dict[str, str]:
    environment: dict[str, str] = {"RALPH_FORCE_ASCII": "1" if case.glyphs == "ascii" else "0"}
    if case.background != "unknown":
        environment["RALPH_TERMINAL_BG"] = case.background
    if case.colour == "none":
        environment["NO_COLOR"] = "1"
    elif case.destination in {"redirect", "ci"}:
        environment["FORCE_COLOR"] = "1"
    return environment


def _scene_snapshot(*, failed: bool) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="review" if failed else "complete",
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
            (
                "review",
                "revise" if failed else "proceed",
                "scene evidence",
                "2026-01-02T03:04:05+00:00",
            ),
        ),
        is_terminal_success=not failed,
        is_terminal_failure=failed,
    )


def _drive_first_screen(display: ParallelDisplay, *, include_capability_summary: bool) -> None:
    display.emit_welcome_banner(version="0.0.0-scene")
    display.emit_first_run_panel([Text("Production display scene", style="theme.text.muted")])
    display.emit_run_start(
        RunStartOrientation(
            prompt_path=_SCENE_PROMPT_PATH,
            workspace_root="/work/café",
            developer_agent="pi",
            plan_present=True,
        )
    )
    if include_capability_summary:
        display.emit_capability_summary(CapabilityState())


def _drive_clean_run(display: ParallelDisplay) -> None:
    display.emit_blank_line()
    entry = PhaseEntryModel(
        "development", "execution", "pi", outer_dev_iteration=1, outer_dev_cap=3
    )
    display.emit_phase_start_from_entry(entry)
    display.emit_activity_line("pi", "text", "implemented Unicode-safe output")
    display.emit_log_line("pi", "raw transcript carrier remains available")
    display.emit_status_line("pi", "waiting for an external review response")
    display.emit_warn_line("pi", "warning", "waiting for an external review response")
    display.emit_activity_line("pi", "thinking", "checking preview hierarchy")
    display.emit_phase_transition("development", "review")
    display.emit_phase_close("development", "scene close outcome")
    display.emit_agents_table({})
    display.emit_providers_table(["scene-provider"])
    display.emit_config_table(UnifiedConfig())
    display.emit_metrics_table({"events": 2, "artifacts": 1})
    display.emit_checkpoint_summary_table(
        _SceneCheckpointOptions("development", {"iterations": (1, 3)})
    )
    display.emit_diagnose_inventory_table([("scene-mcp", "scene", "stdio", "local")])
    display.emit_diagnose_probe_table([("scene-mcp", "yes", "yes", "yes", "yes")])
    display.emit_diagnose_servers_table([("scene-mcp", "stdio", "ready", 1, "scene")])
    display.emit_status("production display state is ready")
    display.emit_warning("recovery detail is preserved in the rendered record")
    display.emit_skill_failure_warning(["docs-mcp unavailable"])
    display.emit_fallback_next_steps(["Re-run setup after configuring docs MCP"])
    display.emit_info_panel(
        title="Production note", content="Preview and records stay recoverable."
    )
    display.emit_renderable(Text("Shared renderable content", style="theme.text.muted"))
    scene_workspace = Path("/scene-workspace-without-artifacts")
    display.emit_plan_artifact(scene_workspace)
    display.emit_development_artifact(scene_workspace)
    display.emit_review_artifact(scene_workspace)
    display.emit_fix_artifact(scene_workspace)
    display.emit_analysis_decision(scene_workspace, "analysis")
    display.emit_commit_message(scene_workspace)
    display.emit_missing_plan_hint()
    display.emit_phase_close_from_exit(
        PhaseExitModel(
            "development",
            "execution",
            "pi",
            artifact_outcome="artifacts ready",
            content_blocks=1,
        )
    )


def _drive_failure(display: ParallelDisplay) -> None:
    display.emit_phase_start("review", agent_name="reviewer")
    raw_machine_detail = (
        "tests failed: assertion output retained; trace-detail condensed count=48 bytes=624"
    )
    display.emit_activity_line(
        "reviewer",
        "error",
        raw_machine_detail,
        condensed_ref=".agent/raw/reviewer.log",
        condensed_flag=True,
    )
    display.emit_warn_line(
        "reviewer",
        "warning",
        "raw machine detail is retained in .agent/raw/reviewer.log",
    )
    display.emit_warning("recover raw machine detail from .agent/raw/reviewer.log")
    display.emit_snapshot(_scene_snapshot(failed=True))
    display.emit_completion_summary_panel(
        _scene_snapshot(failed=True), options=CompletionSummaryOptions(elapsed_seconds=123.0)
    )


def _drive_burst(display: ParallelDisplay) -> None:
    display.emit_activity_line(
        "codex",
        "tool_use",
        "edit_file path=café-00.py",
        tool_signature=("edit_file", "café-00.py"),
    )
    display.emit_activity_line("codex", "tool_result", "edit_file complete")
    display.emit_activity_line(
        "codex",
        "raw",
        "output condensed count=24 bytes=768",
        condensed_flag=True,
        condensed_ref=".agent/raw/run.log",
    )


def _drive_idle_stretch(display: ParallelDisplay) -> None:
    for tick in range(_IDLE_STRETCH_FRAMES):
        display.emit_activity_line(
            "pi",
            "tool_use",
            f"path=café-{tick:02d}.py",
            tool_signature=("read_file", f"café-{tick:02d}.py"),
        )
        display.emit_activity_line("pi", "tool_result", f"read_file complete ({tick})")
    display.emit_activity_line("pi", "warning", "transport stall detected")
    model = StatusBarModel(
        workspace_root="/work/café",
        phase_label="Development",
        phase_style="theme.phase.development",
        elapsed_seconds=123.0,
        agent_name="pi",
        attention="waiting",
    )
    display.update_status_bar(model)


def _drive_closing_screen(display: ParallelDisplay) -> None:
    display.emit_dry_run_summary(
        phase="review",
        iterations=1,
        details={"recovery": ".agent/raw/run.log"},
    )
    display.emit_run_end(phase="complete", total_agent_calls=3)
    display.emit_completion_summary_panel(
        _scene_snapshot(failed=False), options=CompletionSummaryOptions(elapsed_seconds=123.0)
    )


def _drive_production_scene(
    display: ParallelDisplay,
    console: Console,
    context: DisplayContext,
    scene_name: str,
    *,
    include_capability_summary: bool,
) -> None:
    if scene_name == "first_screen":
        _drive_first_screen(display, include_capability_summary=include_capability_summary)
    elif scene_name == "clean_run":
        _drive_clean_run(display)
    elif scene_name == "failure":
        _drive_failure(display)
    elif scene_name == "burst":
        _drive_burst(display)
    elif scene_name == "idle_stretch":
        _drive_idle_stretch(display)
    else:
        _drive_closing_screen(display)


def _render_scene_previews(console: Console, *, terminal_bg_is_light: bool | None) -> None:
    write_preview = build_edit_preview(
        "write_file",
        {"path": "café.py", "content": "def café(value: str) -> int:\n    return len(value)"},
        width=console.width,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    diff_preview = build_edit_preview(
        "edit_file",
        {
            "path": "café.py",
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


def compact_matrix() -> tuple[SupportCase, ...]:
    backgrounds: tuple[Background, ...] = ("dark", "light", "unknown")
    colours: tuple[ColourMode, ...] = ("truecolour", "reduced", "none")
    glyph_modes: tuple[GlyphMode, ...] = ("unicode", "ascii")
    destinations: tuple[Destination, ...] = ("tty", "redirect", "ci")
    main = tuple(
        SupportCase(background, colour, glyphs, FULL_LAYOUT_WIDTH, destination)
        for background in backgrounds
        for colour in colours
        for glyphs in glyph_modes
        for destination in destinations
    )
    width_witness = tuple(
        SupportCase(background, "truecolour", "unicode", GRACEFUL_WIDTH_FLOOR, destination)
        for background in backgrounds
        for destination in destinations
    ) + tuple(
        SupportCase("dark", "truecolour", "unicode", 120, destination)
        for destination in destinations
    )
    return main + width_witness


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
    "compact_matrix",
    "floor_matrix",
    "render_scene",
    "scene_salience_decisions",
    "support_matrix",
]
