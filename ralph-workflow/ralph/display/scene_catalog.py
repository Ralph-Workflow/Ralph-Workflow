"""Generated console scene catalog and executable support matrix."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from rich.console import Console

from rich.syntax import Syntax
from rich.text import Text

from ralph.display.surface_catalog import SURFACE_CATALOG, SurfaceSpec
from ralph.display.theme import (
    make_console,
    pick_status_styles,
    syntax_theme_for_background,
)

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
    """Render one deterministic reference scene through a real Rich console.

    This is intentionally a compact probe rather than a replacement display
    path. It exercises the canonical semantic palette and syntax theme for all
    declared destination modes, giving floor tests generated output instead of
    stored captures. Real pipeline scenes retain their focused display tests.
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
    styles = pick_status_styles(terminal_bg_is_light)
    console.print(Text(f"SCENE {scene_name}", style=styles["info"][0]))
    _render_scene_narrative(console, scene_name, styles, glyphs=case.glyphs)
    console.print(
        Syntax(
            "def cafe\u0301(value: str) -> int:\\n    return len(value)",
            "python",
            theme=syntax_theme_for_background(terminal_bg_is_light),
            line_numbers=True,
            word_wrap=True,
        )
    )
    return stream.getvalue()


def _render_scene_narrative(
    console: Console,
    scene_name: str,
    styles: dict[str, tuple[str, str, str]],
    *,
    glyphs: GlyphMode,
) -> None:
    """Render scene-specific greppable carriers through the canonical palette."""

    def marker(state: str) -> str:
        return styles[state][1] if glyphs == "unicode" else styles[state][2]

    if scene_name == "first_screen":
        console.print(
            Text(
                f"{marker('running')} RUN OPEN phase=planning project=/work/cafe\u0301",
                style=styles["running"][0],
            )
        )
    elif scene_name == "clean_run":
        console.print(
            Text(f"{marker('running')} phase=development agent=pi", style=styles["running"][0])
        )
        console.print(Text(f"{marker('success')} PASS success", style=styles["success"][0]))
    elif scene_name == "failure":
        console.print(
            Text(
                f"{marker('error')} FAIL error phase=review cause=tests failed",
                style=styles["error"][0],
            )
        )
        console.print(
            Text(
                f"{marker('warning')} WARN raw=assertion output retained",
                style=styles["warning"][0],
            )
        )
    elif scene_name == "burst":
        console.print(
            Text(f"{marker('running')} agent=codex tool=edit_file", style=styles["running"][0])
        )
        console.print(
            Text("REPEATED count=3 bytes=96 recovery=.agent/raw/run.log", style=styles["info"][0])
        )
    elif scene_name == "idle_stretch":
        console.print(
            Text(
                f"{marker('pending')} WAIT pending state=waiting elapsed=02:03",
                style=styles["pending"][0],
            )
        )
    else:
        console.print(
            Text(
                f"{marker('success')} RUN COMPLETE outcome=success elapsed=02:03",
                style=styles["success"][0],
            )
        )
    console.print(
        Text("ELIDED count=2 bytes=24 recovery=.agent/raw/run.log", style=styles["info"][0])
    )


def support_matrix() -> tuple[SupportCase, ...]:
    """Return the complete declared support matrix.

    Reference widths cover the graceful floor, fully laid-out threshold, and
    a wide terminal. Destination is orthogonal to colour: redirected output
    may retain colour when explicitly forced, while no-colour never emits ANSI.
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
    "render_scene",
    "support_matrix",
]
