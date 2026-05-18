"""ExitOutcome: possible outcomes that affect pause behavior."""

from __future__ import annotations

from enum import StrEnum


class ExitOutcome(StrEnum):
    """Possible outcomes that affect pause behavior."""

    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"
