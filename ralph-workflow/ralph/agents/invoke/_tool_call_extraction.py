"""Tool-call extraction from raw activity lines.

Extracted from ``_process_reader.py`` to keep the line-reader module
under the 1000-line policy cap enforced by
``tests/integration/test_policy_file_rules.py``. The helpers are
unchanged - they are best-effort extractors of
``(tool_name, tool_args)`` pairs from the canonical envelope
shapes consumed by the tool-call repetition breaker
(``RepetitionTracker.mark_tool_call``).

Recognised envelope shapes:

* ``{"type": "tool_use", "name": "...", "input": {...}}``
* ``{"type": "tool_use", "tool_name": "...", "arguments": {...}}``
* ``{"type": "stream_event", "event": {"type": "content_block_start",
  "content_block": {"type": "tool_use", "name": "...", "input": {...}}}}``
  (Claude content_block_start wrapped in stream_event)
* ``{"event": "tool_use", "tool_name": "...", "arguments": {...}}``
* ``{"tool": "<name>", "input": {...}}`` (raw provider shorthand)
* ``claude tool: <name>`` (plain-text marker from Claude execution strategy)
* ``[plain] tool: <name>`` (plain-text marker from GenericParser convention)
"""

from __future__ import annotations

import json
from typing import cast

_PLAIN_TEXT_TOOL_PREFIXES = ("claude tool:", "[plain] tool:")


def extract_tool_call_from_activity_signal(
    raw: str,
) -> tuple[str, dict[str, object]] | None:
    """Best-effort extract ``(tool_name, tool_args)`` from a TOOL_USE raw line.

    The helper walks a few known envelope shapes so the tool-call
    circuit breaker (``RepetitionTracker.mark_tool_call``) sees a
    stable fingerprint per (tool_name, tool_args) pair regardless of
    transport.  Returns ``None`` when the line is not recognisably a
    tool-use line OR the structure is not understood so the watchdog
    can skip the observation rather than fingerprint a meaningless
    blob.

    The ``tool_name`` is the literal string after trimming; an empty /
    missing name falls back to ``"unknown"`` so the fingerprint is
    always well-formed.  The ``tool_args`` is the dict of input
    arguments extracted from the envelope; ``None`` is treated as an
    empty dict inside the tracker.  Plain-text markers carry no
    arguments, so the fingerprint is ``(name, {})``.
    """
    plain = _extract_plain_text_tool_call(raw)
    if plain is not None:
        return plain
    obj = _parse_tool_use_envelope(raw)
    if obj is None:
        return None
    return _extract_tool_call_from_dict(obj)


def _extract_plain_text_tool_call(raw: str) -> tuple[str, dict[str, object]] | None:
    """Extract a ``claude tool: <name>`` or ``[plain] tool: <name>`` marker."""
    stripped = raw.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    for prefix in _PLAIN_TEXT_TOOL_PREFIXES:
        if lower.startswith(prefix):
            tool_name = stripped[len(prefix) :].strip()
            return (tool_name or "unknown"), {}
    return None


def _parse_tool_use_envelope(raw: str) -> dict[str, object] | None:
    """Parse a JSON tool-use envelope, unwrapping ``stream_event`` if present.

    Returns ``None`` when the raw line is not a recognisable JSON
    tool-use envelope; returns the inner dict (after unwrapping
    ``stream_event`` -> ``content_block``) on success.
    """
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    if obj.get("type") == "stream_event" or "event" in obj:
        event = obj.get("event")
        if isinstance(event, dict):
            inner_obj = cast("dict[str, object]", event)
            content_block = inner_obj.get("content_block")
            if isinstance(content_block, dict):
                inner_obj = cast("dict[str, object]", content_block)
            return inner_obj
    return obj


def _extract_tool_call_from_dict(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    """Extract ``(tool_name, tool_args)`` from a recognised tool-use dict.

    Internal helper for :func:`extract_tool_call_from_activity_signal`.
    Returns ``None`` when the dict does not look like a tool-use.
    """
    if not _is_tool_use_dict(obj):
        return None
    return _resolve_tool_name_and_args(obj)


def _is_tool_use_dict(obj: dict[str, object]) -> bool:
    type_field = obj.get("type")
    event_field = obj.get("event")
    return (
        type_field in {"tool_use", "assistant_tool_use", "mcp_tool_call"}
        or event_field in {"tool_use", "mcp_tool_call"}
        or "tool_name" in obj
    )


def _resolve_tool_name_and_args(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    tool_name_raw = obj.get("name") or obj.get("tool_name") or obj.get("tool")
    if tool_name_raw is None:
        tool_name = "unknown"
    elif not isinstance(tool_name_raw, str):
        return None
    else:
        tool_name = tool_name_raw.strip() or "unknown"

    args_field = obj.get("input")
    if not isinstance(args_field, dict):
        args_field = obj.get("arguments")
    if not isinstance(args_field, dict):
        args_field = obj.get("args")
    if not isinstance(args_field, dict):
        args_field = {}
    return tool_name, cast("dict[str, object]", args_field)
