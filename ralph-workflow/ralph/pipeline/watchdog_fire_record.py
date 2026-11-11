
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.agents.invoke import AgentInactivityTimeoutError

if TYPE_CHECKING:
    from pathlib import Path

WATCHDOG_FIRE_EVENTS_RELPATH: Final[str] = ".agent/watchdog-fire-events.jsonl"

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


_MAX_RECORDED_TEXT: Final[int] = 512


_MAX_CLIP_DEPTH: Final[int] = 6


def _clip(value: object, depth: int = 0) -> object:
    if isinstance(value, str) and len(value) > _MAX_RECORDED_TEXT:
        return value[:_MAX_RECORDED_TEXT] + "...[truncated]"
    if isinstance(value, (list, dict)) and depth >= _MAX_CLIP_DEPTH:
        return "...[nested too deep]"
    if isinstance(value, list):
        return [_clip(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {str(key): _clip(item, depth + 1) for key, item in value.items()}
    return value


def _snapshot(exc: AgentInactivityTimeoutError) -> dict[str, object]:
    snapshot = exc.diagnostic.get("watchdog_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _rule(exc: AgentInactivityTimeoutError) -> str | None:
    return exc.reason.value if exc.reason is not None else None


def watchdog_fire_detail(exc: Exception) -> str | None:
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
    if not isinstance(exc, AgentInactivityTimeoutError):
        return
    record = {
        "recorded_at": recorded_at,
        "agent": agent,
        "phase": phase,
        "rule": _rule(exc),
        "timeout_seconds": exc.timeout_seconds,
        "message": _clip(exc.stderr),
        "snapshot": {key: _clip(value) for key, value in _snapshot(exc).items()},
    }
    journal = workspace_root / WATCHDOG_FIRE_EVENTS_RELPATH
    try:
        line = json.dumps(record, default=str) + "\n"
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except (OSError, TypeError, ValueError) as error:
        logger.warning("Could not record watchdog fire to {}: {}", journal, error)


__all__ = ["WATCHDOG_FIRE_EVENTS_RELPATH", "record_watchdog_fire", "watchdog_fire_detail"]
