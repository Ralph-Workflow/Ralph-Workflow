"""Development-timebox wrap-up state shared by MCP tool dispatch.

The pipeline publishes ``RALPH_DEV_WARN_EPOCH`` once for the uninterrupted
development phase. MCP reads that epoch for both the tool-result nag and the
identity-scoped completion confirmation, so retries cannot restart the timer.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "development_wrapup_notice",
    "request_completion_admission",
    "reset_completion_admissions",
    "session_before_warning",
    "session_warning_fired",
    "session_warning_scope",
]

_SESSION_BEFORE_WARNING: ContextVar[bool | None] = ContextVar(
    "ralph_session_before_warning", default=None
)
_PENDING_COMPLETION_ADMISSIONS: dict[tuple[str, str], None] = {}  # bounded-accumulator-ok: cleared at every attempt boundary.
_COMPLETION_ADMISSIONS_LOCK = Lock()


def request_completion_admission(identity: tuple[str, str]) -> bool:
    """Record a first completion admission or consume its confirmation."""
    with _COMPLETION_ADMISSIONS_LOCK:
        if identity in _PENDING_COMPLETION_ADMISSIONS:
            del _PENDING_COMPLETION_ADMISSIONS[identity]
            return False
        _PENDING_COMPLETION_ADMISSIONS[identity] = None
        return True


def reset_completion_admissions() -> None:
    """Discard pending confirmations at a fresh-attempt boundary."""
    with _COMPLETION_ADMISSIONS_LOCK:
        _PENDING_COMPLETION_ADMISSIONS.clear()


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
        "it immediately. Use partial only as an exceptional last resort, when no productive "
        "in-scope action remains and available information or authority cannot complete the "
        "work. Never submit completed unless every reported item and piece of evidence is "
        "truthful. When genuinely complete, use declare_complete."
    )
