"""Development-timebox wrap-up state shared by MCP tool dispatch.

The pipeline publishes ``RALPH_DEV_WARN_EPOCH`` once for the uninterrupted
development phase. MCP reads that epoch for the tool-result warning, so
retries cannot restart the timer.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "development_wrapup_notice",
    "session_before_warning",
    "session_warning_fired",
    "session_warning_scope",
]

_SESSION_BEFORE_WARNING: ContextVar[bool | None] = ContextVar(
    "ralph_session_before_warning", default=None
)


def session_before_warning() -> bool:
    """Return whether the current dispatched tool call precedes the warning."""
    return _SESSION_BEFORE_WARNING.get() is True


def session_warning_fired() -> bool:
    """Return whether the current dispatched tool call follows the warning."""
    return _SESSION_BEFORE_WARNING.get() is False


@contextmanager
def session_warning_scope(before_warning: bool) -> Iterator[None]:
    """Publish the epoch-derived warning state for one tool dispatch."""
    token = _SESSION_BEFORE_WARNING.set(before_warning)
    try:
        yield
    finally:
        _SESSION_BEFORE_WARNING.reset(token)


def development_wrapup_notice() -> str:
    """Return the phase-wide development-timebox wind-down notice."""
    return (
        "⚠️ DEVELOPMENT-TIMEBOX WARNING — The uninterrupted development phase has passed "
        "its configured warning point. Do everything reasonably possible to complete the "
        "assigned task before submitting any artifact. If actionable work remains, return to "
        "it immediately. Use partial only as an exceptional last resort, when the remaining "
        "work is literally impossible through any developer action available in this run and "
        "requires a physical-world, operator-only, or externally controlled action. Difficulty, "
        "elapsed time, and exhausted budget never qualify. Never submit completed unless every "
        "reported item and piece of evidence is "
        "truthful. When genuinely complete, use declare_complete."
    )
