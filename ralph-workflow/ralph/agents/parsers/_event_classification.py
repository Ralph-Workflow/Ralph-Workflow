"""Shared event-classification surface for the agent output parsers.

This module owns the lifecycle-suppression and session-metadata detection
contract for every parser in :mod:`ralph.agents.parsers`. Per-parser
modules (claude, opencode, codex, gemini, generic, claude_interactive)
import from here so the canonical lifecycle set, lifecycle-kind set,
and session-metadata detection are not duplicated across parsers.

Two surfaces are exposed:

- :data:`LIFECYCLE_EVENT_TYPES` / :func:`is_lifecycle_event` — the
  strict superset of every per-transport lifecycle event type that the
  wire-format parsers (claude, opencode, codex, gemini, generic) suppress.
- :data:`LIFECYCLE_KINDS` / :func:`is_lifecycle_kind` — the
  lifecycle-kind set the claude-interactive transcript parser uses
  to flag bare lifecycle events it produces via
  ``InteractiveTranscriptEvent(kind="lifecycle", text=...)``.

Session-metadata recognition (:func:`is_session_metadata_event`) routes
through the canonical :func:`extract_transport_session_id` from
:mod:`ralph.agents.invoke._session` so every parser observes the
exact same JSON shape.
"""

from __future__ import annotations

import json
from typing import Final


def _extract_transport_session_id_inline(
    raw_output: list[str] | tuple[str, ...],
) -> str | None:
    """Lazy-loaded accessor that defers the ``ralph.agents.invoke._session`` import.

    Importing ``ralph.agents.invoke._session`` eagerly triggers
    ``ralph.agents.invoke.__init__`` which transitively imports
    ``strategy_for_transport`` from ``ralph.agents.execution_state``,
    which in turn re-imports from ``ralph.agents.invoke`` — a
    circular import that breaks test collection. We resolve the
    function lazily so the parsers package never needs to load
    the full ``ralph.agents.invoke`` package.
    """
    from ralph.agents.invoke._session import extract_transport_session_id  # noqa: PLC0415

    return extract_transport_session_id(raw_output)


LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "thread.started",
        "turn.started",
        "message_started",
        "heartbeat",
        "ping",
        "ready",
        "start",
        "begin",
        "user",
        "assistant",
        "thinking",
    }
)


def is_lifecycle_event(event_type: str) -> bool:
    """Return True when ``event_type`` is a wire-format lifecycle event.

    Single owner of the lifecycle check for every wire-format parser.
    """
    return event_type in LIFECYCLE_EVENT_TYPES


LIFECYCLE_KINDS: Final[frozenset[str]] = frozenset({"lifecycle"})


def is_lifecycle_kind(kind: str) -> bool:
    """Return True when ``kind`` is a transcript-event lifecycle kind.

    Single owner of the lifecycle-kind check for the
    ``claude_interactive`` family of parsers.
    """
    return kind in LIFECYCLE_KINDS


def is_session_metadata_event(parsed_json: object) -> bool:
    """Return True when ``parsed_json`` is a transport-level session event.

    Delegates to the canonical :func:`extract_transport_session_id` so
    every parser observes the same JSON shape.
    """
    if not isinstance(parsed_json, dict):
        return False
    try:
        serialized = json.dumps(parsed_json)
    except (TypeError, ValueError):
        return False
    return _extract_transport_session_id_inline((serialized,)) is not None


__all__ = [
    "LIFECYCLE_EVENT_TYPES",
    "LIFECYCLE_KINDS",
    "is_lifecycle_event",
    "is_lifecycle_kind",
    "is_session_metadata_event",
]
