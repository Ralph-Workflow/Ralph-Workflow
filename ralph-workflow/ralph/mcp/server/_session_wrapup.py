
from __future__ import annotations

import os as _os
import time as _time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ralph.mcp.protocol.cycle_deadline_env import read_published_epoch
from ralph.mcp.protocol.env import DEV_DEADLINE_EPOCH_ENV

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
    return _SESSION_BEFORE_WARNING.get() is True


def session_warning_fired() -> bool:
    return _SESSION_BEFORE_WARNING.get() is False


@contextmanager
def session_warning_scope(before_warning: bool) -> Iterator[None]:
    token = _SESSION_BEFORE_WARNING.set(before_warning)
    try:
        yield
    finally:
        _SESSION_BEFORE_WARNING.reset(token)


def development_wrapup_notice() -> str:
    deadline_epoch = read_published_epoch(DEV_DEADLINE_EPOCH_ENV, _os.environ.get)
    if deadline_epoch is None:
        return _STATIC_DEVELOPMENT_WRAPUP_NOTICE

    remaining_minutes = max(0, int((deadline_epoch - _time.time()) // 60))
    action = (
        "if actionable plan work remains, dispatch an **independent ready group** "
        "(steps with no `Depends on:` path between any pair and pairwise disjoint "
        "`Files:` lists) concurrently rather than trimming scope, then "
    )
    return (
        "⚠️ DEVELOPMENT-TIMEBOX WARNING — The uninterrupted development phase has passed "
        f"its configured warning point. Approximately **{remaining_minutes} minutes remaining** "
        "before the session is force-cut. Do everything reasonably possible to complete the "
        f"assigned task before submitting any artifact: {action}"
        "**submit the development result before the cut** rather than after it. Use partial only "
        "as an exceptional last resort, when the remaining work is literally impossible through "
        "any developer action available in this run and requires a physical-world, operator-only, "
        "or externally controlled action. Difficulty, elapsed time, and exhausted budget never "
        "qualify. Never submit completed unless every reported item and piece of evidence is "
        "truthful. When genuinely complete, use declare_complete."
    )


_STATIC_DEVELOPMENT_WRAPUP_NOTICE = (
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
