"""Generated console scene catalog and executable support matrix."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Final, Literal

from rich.syntax import Syntax
from rich.text import Text

from ralph.display.theme import (
    SYNTAX_BACKGROUND_TRANSPARENT,
    make_console,
    pick_status_styles,
    syntax_theme_for_background,
)

Background = Literal["dark", "light", "unknown"]
ColourMode = Literal["truecolour", "reduced", "none"]
GlyphMode = Literal["unicode", "ascii"]
Destination = Literal["tty", "redirect"]
RichColorSystem = Literal["auto", "standard", "256", "truecolor", "windows"]

CONTRAST_FLOOR: Final[float] = 4.5
FULL_LAYOUT_WIDTH: Final[int] = 80
GRACEFUL_WIDTH_FLOOR: Final[int] = 40
GRACEFUL_HEIGHT_FLOOR: Final[int] = 12
INDENT_UNIT: Final[str] = "  "


@dataclass(frozen=True)
class _SurfaceSpec:
    """One user-visible display surface and its structural entitlement."""

    name: str
    owner: str
    frame_entitled: bool = False


SurfaceSpec = _SurfaceSpec


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

SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec("welcome", "parallel_display", True),
    SurfaceSpec("first_run", "parallel_display", True),
    SurfaceSpec("run_open", "parallel_display", True),
    SurfaceSpec("phase_open", "parallel_display"),
    SurfaceSpec("phase_close", "parallel_display"),
    SurfaceSpec("phase_transition", "parallel_display"),
    SurfaceSpec("agent_text", "agent_event_renderer"),
    SurfaceSpec("reasoning", "agent_event_renderer"),
    SurfaceSpec("tool_call", "agent_event_renderer"),
    SurfaceSpec("tool_result", "agent_event_renderer"),
    SurfaceSpec("tool_error", "agent_event_renderer"),
    SurfaceSpec("raw_warning_status", "parallel_display"),
    SurfaceSpec("table", "parallel_display"),
    SurfaceSpec("panel", "parallel_display"),
    SurfaceSpec("artifact", "parallel_display"),
    SurfaceSpec("syntax_preview", "edit_preview"),
    SurfaceSpec("diff_preview", "edit_preview"),
    SurfaceSpec("elision", "content_condenser"),
    SurfaceSpec("status_bar", "status_bar"),
    SurfaceSpec("completion_success", "completion_summary", True),
    SurfaceSpec("completion_failure", "completion_summary", True),
)


def render_scene(
    scene_name: str,
    case: SupportCase,
    *,
    terminal_bg_is_light: bool | None,
) -> str:
    """Render one deterministic reference scene through a real Rich console.

    This is intentionally a compact probe rather than a replacement display
    path. It exercises the canonical semantic palette and syntax theme for all
    declared destination modes, giving floor tests generated output instead of
    stored captures. Real pipeline scenes retain their focused display tests.
    """
    if scene_name not in SCENE_NAMES:
        raise ValueError(f"unknown scene {scene_name!r}")
    stream = StringIO()
    console = make_console(
        file=stream,
        no_color=case.colour == "none",
        force_terminal=case.destination == "tty" and case.colour != "none",
        color_system=case.color_system,
        width=case.width,
        height=case.height,
    )
    styles = pick_status_styles(terminal_bg_is_light)
    console.print(Text(f"[{scene_name}] ", style=styles["info"][0]), end="")
    for state in ("running", "pending", "warning", "success", "error"):
        style, glyph, label = styles[state]
        marker = glyph if case.glyphs == "unicode" else label
        console.print(Text(f"{marker} {label} ", style=style), end="")
    console.print(Text("ELIDED count=2 bytes=24 recovery=.agent/raw/run.log", style=styles["info"][0]))
    console.print(
        Syntax(
            "def café(value: str) -> int:\\n    return len(value)",
            "python",
            theme=syntax_theme_for_background(terminal_bg_is_light),
            line_numbers=True,
            word_wrap=True,
            background_color=SYNTAX_BACKGROUND_TRANSPARENT,
        )
    )
    return stream.getvalue()


def support_matrix() -> tuple[SupportCase, ...]:
    """Return the complete declared support matrix.

    Reference widths cover the graceful floor, fully laid-out threshold, and
    a wide terminal. Destination is orthogonal to colour: redirected output
    may retain colour when explicitly forced, while no-colour never emits ANSI.
    """
    backgrounds: tuple[Background, ...] = ("dark", "light", "unknown")
    colours: tuple[ColourMode, ...] = ("truecolour", "reduced", "none")
    glyph_modes: tuple[GlyphMode, ...] = ("unicode", "ascii")
    destinations: tuple[Destination, ...] = ("tty", "redirect")
    return tuple(
        SupportCase(background, colour, glyphs, width, destination)
        for background in backgrounds
        for colour in colours
        for glyphs in glyph_modes
        for width in (GRACEFUL_WIDTH_FLOOR, FULL_LAYOUT_WIDTH, 120)
        for destination in destinations
    )


__all__ = [
    "CONTRAST_FLOOR",
    "FULL_LAYOUT_WIDTH",
    "GRACEFUL_HEIGHT_FLOOR",
    "GRACEFUL_WIDTH_FLOOR",
    "INDENT_UNIT",
    "SCENE_NAMES",
    "SURFACE_CATALOG",
    "SupportCase",
    "SurfaceSpec",
    "support_matrix",
]
