"""Okabe-Ito theme helpers for Ralph CLI display."""

from __future__ import annotations

import os
from typing import Final

from rich.console import Console
from rich.theme import Theme

ORANGE: Final[str] = "#E69F00"
SKY_BLUE: Final[str] = "#56B4E9"
BLUISH_GREEN: Final[str] = "#009E73"
YELLOW: Final[str] = "#F0E442"
BLUE: Final[str] = "#0072B2"
VERMILLION: Final[str] = "#D55E00"
REDDISH_PURPLE: Final[str] = "#CC79A7"
BLACK: Final[str] = "#000000"

STATUS_STYLES: Final[dict[str, tuple[str, str, str]]] = {
    "success": (f"bold {BLUISH_GREEN}", "✓", "PASS"),
    "running": (SKY_BLUE, "◐", "RUN"),
    "warning": (f"bold {ORANGE}", "⚠", "WARN"),
    "error": (f"bold {VERMILLION}", "✗", "FAIL"),
    "skipped": (YELLOW, "○", "SKIP"),
    "pending": ("dim", "○", "WAIT"),
    "info": (BLUE, "ℹ", "INFO"),  # noqa: RUF001
}

_THEME_STYLES: Final[dict[str, str]] = {
    "theme.level.info": BLUE,
    "theme.level.success": f"bold {BLUISH_GREEN}",
    "theme.level.warn": f"bold {ORANGE}",
    "theme.level.error": f"bold {VERMILLION}",
    "theme.level.milestone": f"bold {SKY_BLUE}",
    "theme.cat.meta": "dim",
    "theme.cat.cont": BLUE,
    "theme.log.error": f"bold {VERMILLION}",
    "theme.log.info": BLUE,
    "theme.log.milestone": f"bold {SKY_BLUE}",
    "theme.log.success": f"bold {BLUISH_GREEN}",
    "theme.log.warn": f"bold {ORANGE}",
    "theme.panel.border": BLACK,
    "theme.panel.title": "bold",
    "theme.phase.commit": BLUE,
    "theme.phase.complete": f"bold {BLUISH_GREEN}",
    "theme.phase.development": BLUISH_GREEN,
    "theme.phase.development_analysis": REDDISH_PURPLE,
    "theme.phase.development_commit": BLUE,
    "theme.phase.failed": f"bold {VERMILLION}",
    "theme.phase.fix": VERMILLION,
    "theme.phase.planning": SKY_BLUE,
    "theme.phase.review": ORANGE,
    "theme.phase.review_analysis": REDDISH_PURPLE,
    "theme.phase.review_commit": BLUE,
    "theme.status.error": f"bold {VERMILLION}",
    "theme.status.info": BLUE,
    "theme.status.pending": "dim",
    "theme.status.running": SKY_BLUE,
    "theme.status.skipped": YELLOW,
    "theme.status.success": f"bold {BLUISH_GREEN}",
    "theme.status.warning": f"bold {ORANGE}",
    "theme.text.emphasis": "bold",
    "theme.text.muted": "dim",
}

RALPH_THEME: Final[Theme] = Theme(_THEME_STYLES)


def format_status(status_name: str) -> str:
    """Return Rich markup for a semantic status name."""
    try:
        style, icon, label = STATUS_STYLES[status_name]
    except KeyError as exc:
        known = ", ".join(sorted(STATUS_STYLES))
        raise KeyError(f"Unknown status {status_name!r}. Known statuses: {known}") from exc
    return f"[{style}]{icon} {label}[/]"


def make_console(
    *,
    no_color: bool | None = None,
    force_terminal: bool | None = None,
    width: int | None = None,
) -> Console:
    """Create a Console using Ralph's shared theme and predictable rendering."""
    resolved_no_color = no_color
    if resolved_no_color is None:
        if "NO_COLOR" in os.environ and force_terminal is None:
            resolved_no_color = True
        elif "FORCE_COLOR" in os.environ:
            resolved_no_color = False
    resolved_force_terminal = force_terminal
    if resolved_force_terminal is None:
        if "NO_COLOR" in os.environ:
            resolved_force_terminal = False
        elif "FORCE_COLOR" in os.environ:
            resolved_force_terminal = True
    return Console(
        theme=RALPH_THEME,
        no_color=resolved_no_color,
        force_terminal=resolved_force_terminal,
        width=width,
        highlight=False,
    )


__all__ = [
    "BLACK",
    "BLUE",
    "BLUISH_GREEN",
    "ORANGE",
    "RALPH_THEME",
    "REDDISH_PURPLE",
    "SKY_BLUE",
    "STATUS_STYLES",
    "VERMILLION",
    "YELLOW",
    "format_status",
    "make_console",
]
