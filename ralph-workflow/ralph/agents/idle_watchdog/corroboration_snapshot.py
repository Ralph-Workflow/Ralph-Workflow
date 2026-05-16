from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process.child_liveness import AliveBy


@dataclass(frozen=True)
class CorroborationSnapshot:
    """Advisory snapshot of corroborating signals for WAITING_ON_CHILD diagnosis.

    All fields are Optional so callers without a given source can leave them None.
    Corroborators are advisory only; they NEVER affect WatchdogVerdict. The hard
    stop is determined solely by max_waiting_on_child_seconds and max_session_seconds.
    """

    workspace_event_count: int | None = None
    oldest_child_seconds: float | None = None
    scoped_child_active: bool | None = None
    scoped_child_count: int | None = None
    terminal_child_events_total: int | None = None
    last_activity_was_meaningful: bool | None = None
    alive_by: AliveBy | None = None


WaitingCorroborator = Callable[[], CorroborationSnapshot]
