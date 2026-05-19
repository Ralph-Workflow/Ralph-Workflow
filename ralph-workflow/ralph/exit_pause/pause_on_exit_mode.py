"""PauseOnExitMode: when to pause before exiting."""

from __future__ import annotations

from enum import StrEnum


class PauseOnExitMode(StrEnum):
    """When to pause before exiting."""

    NEVER = "never"
    ALWAYS = "always"
    AUTO = "auto"
