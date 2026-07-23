"""Okabe-Ito theme helpers for Ralph CLI display.

Accessibility contract
----------------------

The palette is Okabe-Ito (a colorblind-safe palette researched by Okabe
& Ito, 2002 -- "Color Universal Design") so that no semantic state is
distinguishable by hue alone under the common forms of color-vision
deficiency (deuteranopia, protanopia, tritanopia). The pairing rules
below make that explicit so an accessibility decision is made once
here, not per call site:

* ``STATUS_STYLES`` carries a 3-tuple ``(rich_style, unicode_icon,
  ascii_label)`` per semantic state. Display code must always render
  the icon and the ASCII label alongside the color so a colorblind
  operator (or a no-color console) keeps every piece of meaning the
  color was carrying.
* ``success`` (BLUISH_GREEN, okabe-ito #009E73, lightness L*~52)
  vs ``error`` (VERMILLION, okabe-ito #D55E00, lightness L*~54). The
  lightnesses are similar in raw hex but visually distinct after
  Okabe-Ito's perceptual tuning: red/green pairings are the classic
  accessibility trap and the Okabe-Ito pair avoids it by using
  blueish-green and vermillion (orange-red) which differ in lightness
  AND hue. Display code MUST NOT use red/green as the sole
  differentiator for any state pair -- the accessibility test in
  ``tests/display/test_agent_output_accessibility.py`` asserts each
  state also carries a non-color carrier.
* Semantic roles are defined ONCE here: ``success``, ``running``,
  ``warning``, ``error``, ``skipped``, ``pending``, ``info``. New
  states must add a new entry here, never duplicate a hex string in
  a renderer.
"""

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
    "info": "i",
    "milestone": "◆",  # ◆
    "arrow": "→",  # →
    "start": "▶",  # ▶
    # New artistic glyphs
    "phase_marker": "■",  # ■ - phase start marker
    "iteration": "↻",  # ↻ - iteration indicator
    "budget": "▲",  # ▲ - budget indicator
    "review_pass": "✔",  # ✔ - review pass
    "review_fail": "✘",  # ✘ - review fail
    "outer_dev": "◎",  # ◎ - outer dev indicator (bullseye: clear outer cycle marker)
    "inner_analysis": "▸",  # ▸ - inner analysis indicator (triangle: direction/analysis)
    "proceed": "↑",  # ↑ - proceed arrow
    "revise": "↓",  # ↓ - revise arrow
    "rule": "───",  # ─── - section rule
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
    "outer_dev": "[OD]",
    "inner_analysis": "[IA]",
    "proceed": "^",
    "revise": "v",
    "rule": "---",
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
    "info": (BLUE, "\u2139", "INFO"),
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
    # Theme keys for iteration indicators
    "theme.outer_dev": f"bold {SKY_BLUE}",
    "theme.inner_analysis": REDDISH_PURPLE,
    "theme.review_pass": f"bold {BLUISH_GREEN}",
    "theme.review_fail": f"bold {VERMILLION}",
    "theme.proceed": f"bold {BLUISH_GREEN}",
    "theme.revise": f"bold {ORANGE}",
    # Theme keys for the persistent Status Bar (ralph/display/status_bar.py).
    # Dim styling de-emphasizes the working-directory path and structural
    # separators so the colored phase label (theme.phase.*) and the iteration
    # counts (theme.outer_dev / theme.inner_analysis) remain the prominent
    # state signal, matching 'prioritize the most operationally important
    # information when space is tight'. Without these keys Rich would silently
    # render the affected segments uncolored (dangling style references).
    "theme.status.bar_marker": "dim",
    "theme.status.path_marker": "dim",
    "theme.status.path": "dim",
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
            If None, defaults to None so Rich auto-detects terminal support via
            ``sys.stdout.isatty()`` (this is the production default: forcing
            ``force_terminal=False`` would hard-code ``Console.is_terminal=False``
            and break the StatusBar real-TTY gate in real PTY sessions).
        width: Optional terminal width override.

    Returns:
        Configured Console instance with Ralph's theme.
    """
    resolved_no_color = no_color if no_color is not None else False
    # Default ``force_terminal`` to ``None`` (not ``False``) so Rich auto-detects
    # via ``sys.stdout.isatty()``. Hard-coding ``False`` would set
    # ``Console.is_terminal = False`` even on a real PTY, which closes the
    # StatusBar real-TTY gate and prevents the persistent footer from rendering.
    resolved_force_terminal = force_terminal
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
