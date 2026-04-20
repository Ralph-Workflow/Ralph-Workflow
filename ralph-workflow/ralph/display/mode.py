"""Terminal mode detection for Ralph's copy-paste-first transcript output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rich.console import Console

NARROW_THRESHOLD: int = 60


def detect_mode(
    console: Console,
    env: dict[str, str],
) -> Literal["lines"]:
    del console, env
    return "lines"
