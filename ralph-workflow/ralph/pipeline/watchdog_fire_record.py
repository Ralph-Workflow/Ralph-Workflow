"""Name the watchdog rule behind an ``AgentInactivityTimeoutError``.

Every watchdog kill, whether silence, a loop-breaker, the child ceiling or
the session ceiling, reaches the retry layer as one exception type, and the
retry text used to flatten it to "an inactivity timeout". This module turns
the exception's rule and its ``watchdog_snapshot`` into the detail line shown
to the retry agent and into one JSONL record per kill that survives the run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from ralph.agents.invoke import AgentInactivityTimeoutError

if TYPE_CHECKING:
    from pathlib import Path

#: Workspace-relative journal with one JSON object per watchdog kill.
WATCHDOG_FIRE_EVENTS_RELPATH: Final[str] = ".agent/watchdog-fire-events.jsonl"

#: Snapshot fields worth showing the retry agent, in display order. They answer
#: "was it silent, and was work still live?" (subagents, MCP calls, last signal).
_DETAIL_SNAPSHOT_KEYS: Final[tuple[str, ...]] = (
    "idle_elapsed_seconds",
    "invocation_elapsed_seconds",
    "live_subagent_count",
    "subagent_progress_count",
    "mcp_tool_call_count",
    "last_activity_kind",
    "last_alive_by",
    "current_subagent_tool_call",
)


def _snapshot(exc: AgentInactivityTimeoutError) -> dict[str, object]:
    snapshot = exc.diagnostic.get("watchdog_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _rule(exc: AgentInactivityTimeoutError) -> str | None:
    return exc.reason.value if exc.reason is not None else None


def watchdog_fire_detail(exc: Exception) -> str | None:
    """Return the retry-prompt detail naming the fired rule, else ``None``.

    ``None`` for any failure that is not a watchdog kill, so its retry text
    keeps its own wording.
    """
    if not isinstance(exc, AgentInactivityTimeoutError):
        return None
    snapshot = _snapshot(exc)
    facts = [
        f"{key}={snapshot[key]}" for key in _DETAIL_SNAPSHOT_KEYS if snapshot.get(key) is not None
    ]
    detail = f"watchdog rule {_rule(exc) or 'unclassified'}: {exc.stderr}"
    return f"{detail} ({', '.join(facts)})" if facts else detail


def record_watchdog_fire(
    workspace_root: Path,
    exc: Exception,
    *,
    agent: str,
    phase: str,
    recorded_at: float,
) -> None:
    """Append one record for a watchdog kill to the workspace journal.

    A no-op for any failure that is not a watchdog kill. The journal is the
    only durable answer to "which rule killed this agent?": the console line
    and the retry prompt are gone once the run's terminal closes.
    """
    if not isinstance(exc, AgentInactivityTimeoutError):
        return
    record = {
        "recorded_at": recorded_at,
        "agent": agent,
        "phase": phase,
        "rule": _rule(exc),
        "timeout_seconds": exc.timeout_seconds,
        "message": exc.stderr,
        "snapshot": _snapshot(exc),
    }
    journal = workspace_root / WATCHDOG_FIRE_EVENTS_RELPATH
    journal.parent.mkdir(parents=True, exist_ok=True)
    # filesystem-write-ok: append-only one-line-per-kill journal; write_text_if_changed's whole-file replace semantics would drop earlier kills.
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


__all__ = ["WATCHDOG_FIRE_EVENTS_RELPATH", "record_watchdog_fire", "watchdog_fire_detail"]
