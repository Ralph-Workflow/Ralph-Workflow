"""Okabe-Ito theme helpers for Ralph CLI display."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from rich.console import Console
from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Mapping

ORANGE: Final[str] = "#E69F00"
SKY_BLUE: Final[str] = "#56B4E9"
BLUISH_GREEN: Final[str] = "#009E73"
YELLOW: Final[str] = "#F0E442"
BLUE: Final[str] = "#0072B2"
VERMILLION: Final[str] = "#D55E00"
REDDISH_PURPLE: Final[str] = "#CC79A7"
BLACK: Final[str] = "#000000"

# Glyph tables for Unicode and ASCII modes
UNICODE_GLYPHS: Final[dict[str, str]] = {
    "success": "✓",  # ✓
    "error": "✗",  # ✗
    "warning": "⚠",  # ⚠
    "running": "◐",  # ◐
    "pending": "○",  # ○
    "info": "ℹ",  # noqa: RUF001
    "milestone": "◆",  # ◆
    "arrow": "→",  # →
    "start": "▶",  # ▶
    # New artistic glyphs
    "phase_marker": "■",  # ■ - phase start marker
    "iteration": "↻",  # ↻ - iteration indicator
    "budget": "▲",  # ▲ - budget indicator
    "review_pass": "✔",  # ✔ - review pass
    "review_fail": "✘",  # ✘ - review fail
    "fixer": "⚙",  # ⚙ - fixer/gear indicator
    "outer_dev": "⊞",  # ⊞ - outer dev indicator
    "inner_analysis": "≴",  # ≴ - inner analysis indicator
    "proceed": "↑",  # ↑ - proceed arrow
    "revise": "↓",  # ↓ - revise arrow
}

ASCII_GLYPHS: Final[dict[str, str]] = {
    "success": "[OK]",
    "error": "[X]",
    "warning": "[!]",
    "running": "[*]",
    "pending": "[ ]",
    "info": "[i]",
    "milestone": "*",
    "arrow": "->",
    "start": ">",
    # New ASCII glyphs
    "phase_marker": "[]",
    "iteration": "~",
    "budget": "^",
    "review_pass": "[+]",
    "review_fail": "[-]",
    "fixer": "[G]",
    "outer_dev": "[OD]",
    "inner_analysis": "[IA]",
    "proceed": "^",
    "revise": "v",
}

_RALPH_FORCE_ASCII_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def detect_glyph_capability(stream: object, env: Mapping[str, str]) -> bool:
    """Return False when glyphs should fall back to ASCII, True for Unicode.

    Heuristic order (highest to lowest precedence):
    1. RALPH_FORCE_ASCII env var (any truthy value) → ASCII
    2. stream.encoding exists and 'utf' not in encoding.lower() → ASCII
    3. TERM=dumb → ASCII
    4. Otherwise → Unicode
    """
    # Check explicit env override first
    force_ascii = env.get("RALPH_FORCE_ASCII", "").lower().strip()
    if force_ascii in _RALPH_FORCE_ASCII_TRUTHY:
        return False

    # Check stream encoding
    encoding: object = getattr(stream, "encoding", None)
    if encoding is not None:
        encoding_str = str(encoding).lower()
        if "utf" not in encoding_str:
            return False

    # Check TERM=dumb
    term = env.get("TERM", "")
    return term != "dumb"

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
    "theme.status.failure": f"bold {VERMILLION}",
    "theme.status.info": BLUE,
    "theme.status.pending": "dim",
    "theme.status.running": SKY_BLUE,
    "theme.status.skipped": YELLOW,
    "theme.status.success": f"bold {BLUISH_GREEN}",
    "theme.status.warning": f"bold {ORANGE}",
    "theme.text.dim_italic": "dim italic",
    "theme.text.emphasis": "bold",
    "theme.text.muted": "dim",
    "theme.banner.ascii": f"bold {SKY_BLUE}",
    "theme.banner.border": SKY_BLUE,
    "theme.banner.tagline": "dim",
    "theme.banner.title": f"bold {SKY_BLUE}",
    "theme.banner.version": f"bold {BLUISH_GREEN}",
    "theme.banner.welcome": "bold",
    # New theme keys for iteration indicators
    "theme.fixer_iteration": VERMILLION,
    "theme.outer_dev": f"bold {SKY_BLUE}",
    "theme.inner_analysis": REDDISH_PURPLE,
    "theme.review_pass": f"bold {BLUISH_GREEN}",
    "theme.review_fail": f"bold {VERMILLION}",
    "theme.proceed": f"bold {BLUISH_GREEN}",
    "theme.revise": f"bold {ORANGE}",
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
    """Create a Console using Ralph's shared theme and predictable rendering.

    This is a pure constructor - no environment reads. All decisions about
    no_color and force_terminal must be passed explicitly via the corresponding
    arguments. The caller is responsible for resolving environment variables
    before calling this function.

    Args:
        no_color: If True, disables color output. If False, enables color.
            If None, defaults to False (color enabled).
        force_terminal: If True, forces terminal detection on. If False, forces it off.
            If None, defaults to False.
        width: Optional terminal width override.

    Returns:
        Configured Console instance with Ralph's theme.
    """
    resolved_no_color = no_color if no_color is not None else False
    resolved_force_terminal = force_terminal if force_terminal is not None else False
    return Console(
        theme=RALPH_THEME,
        no_color=resolved_no_color,
        force_terminal=resolved_force_terminal,
        width=width,
        highlight=False,
    )


__all__ = [
    "ASCII_GLYPHS",
    "BLACK",
    "BLUE",
    "BLUISH_GREEN",
    "ORANGE",
    "RALPH_THEME",
    "REDDISH_PURPLE",
    "SKY_BLUE",
    "STATUS_STYLES",
    "UNICODE_GLYPHS",
    "VERMILLION",
    "YELLOW",
    "detect_glyph_capability",
    "format_status",
    "make_console",
]
