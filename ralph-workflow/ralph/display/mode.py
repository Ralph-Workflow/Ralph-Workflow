"""Terminal mode detection with explicit priority order."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rich.console import Console

NARROW_THRESHOLD: int = 60


def detect_mode(
    console: Console,
    env: dict[str, str],
) -> Literal["dashboard", "lines"]:
    if "NO_COLOR" in env:
        return "lines"
    if env.get("CI") or env.get("TERM") == "dumb":
        return "lines"
    if env.get("FORCE_COLOR"):
        return "dashboard"
    if not console.is_terminal or console.width <= NARROW_THRESHOLD:
        return "lines"
    return "dashboard"
