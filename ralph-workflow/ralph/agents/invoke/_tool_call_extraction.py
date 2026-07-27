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
* ``{"type": "tool_use", "part": {"type": "tool", "tool": "...",
  "state": {"input": {...}}}}`` (OpenCode part-nested tool envelope)
* ``{"type": "tool_execution_start", "toolName": "...", "args": {...}}``
  (pi.dev top-level tool event)
* ``{"type": "message_update", "assistantMessageEvent":
  {"type": "toolcall_end", "toolCall": {"name": "...", "input": {...}}}}``
  (pi.dev assistant-message tool event)
* ``{"type": "tool_call", "tool_call": {"editToolCall": {"args": {...}}}}``
  (Cursor Agent CLI live stream-json tool event)
* ``claude tool: <name>`` (plain-text marker from Claude execution strategy)
* ``[plain] tool: <name>`` (plain-text marker from GenericParser convention)
"""

from __future__ import annotations

import json
from typing import cast

_PLAIN_TEXT_TOOL_PREFIXES = ("claude tool:", "[plain] tool:")
_CURSOR_TOOL_CALL_METADATA_KEYS = frozenset(
    {
        "toolCallId",
        "startedAtMs",
        "completedAtMs",
        "hookAdditionalContexts",
    }
)


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
    assistant_event = obj.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        return cast("dict[str, object]", assistant_event)
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
    resolved = _resolve_tool_name_and_args(obj)
    if resolved is None:
        return None
    tool_name, tool_args = resolved
    if tool_name == "unknown" and not tool_args:
        # Nothing distinguishing was recovered, so every such envelope would
        # collapse onto ONE fingerprint and N unrelated tool calls would look
        # to the repetition breaker like one call repeated N times. Skip the
        # observation instead -- an unreadable envelope is not evidence of a
        # wedge.
        return None
    return resolved


def _is_tool_use_dict(obj: dict[str, object]) -> bool:
    type_field = obj.get("type")
    event_field = obj.get("event")
    return (
        type_field
        in {
            "tool_use",
            "assistant_tool_use",
            "mcp_tool_call",
            "tool_execution_start",
            "toolcall_end",
        }
        or event_field in {"tool_use", "mcp_tool_call"}
        or "tool_name" in obj
        or "toolName" in obj
        or "toolCall" in obj
        or (type_field == "tool_call" and "tool_call" in obj)
    )


def _resolve_tool_name_and_args(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    opencode_part = obj.get("part")
    if isinstance(opencode_part, dict):
        return _resolve_opencode_part_tool_call(cast("dict[str, object]", opencode_part))

    cursor_tool_call = obj.get("tool_call")
    if isinstance(cursor_tool_call, dict):
        return _resolve_cursor_tool_call(cursor_tool_call)

    tool_call = obj.get("toolCall")
    if isinstance(tool_call, dict):
        return _resolve_flat_tool_call(cast("dict[str, object]", tool_call))

    return _resolve_flat_tool_call(obj)


def _resolve_flat_tool_call(
    obj: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    """Resolve ``(name, args)`` from a dict that carries both at its top level.

    Shared by the pi.dev ``toolCall`` wrapper and the plain top-level envelope;
    both spell the name and the arguments with the same set of aliases.
    """
    tool_name_raw = (
        obj.get("name") or obj.get("tool_name") or obj.get("tool") or obj.get("toolName")
    )
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


def _resolve_opencode_part_tool_call(
    part: dict[str, object],
) -> tuple[str, dict[str, object]] | None:
    """Extract ``(tool, input)`` from OpenCode's ``part``-nested tool envelope.

    OpenCode carries the tool name and its arguments INSIDE ``part``, never at
    the top level::

        {"type": "tool_use", "sessionID": "ses_...",
         "part": {"type": "tool", "tool": "todowrite", "callID": "call_...",
                  "state": {"status": "completed", "input": {...},
                            "output": "..."}}}

    Read through the top level (as every other branch here does) and EVERY
    OpenCode tool call resolves to the same ``("unknown", {})`` fingerprint,
    so five consecutive calls to five DIFFERENT tools look to
    :class:`~ralph.agents.idle_watchdog.repetition_tracker.RepetitionTracker`
    like one identical call repeated five times. The watchdog then fires
    ``REPEATED_IDENTICAL_TOOL_CALL`` and kills a healthy agent mid-run --
    observed live against OpenCode 1.17.15 with
    ``ralph smoke-interactive-opencode``.

    ``callID`` is deliberately NOT part of the fingerprint: it is unique per
    call, so including it would make every call distinct and disable the
    breaker for OpenCode entirely.

    Returns ``None`` when the part is not a tool part or carries no usable
    tool name, so the caller skips the observation rather than feeding the
    breaker a meaningless blob.
    """
    if str(part.get("type", "")) != "tool":
        return None
    tool_name_raw = part.get("tool")
    if not isinstance(tool_name_raw, str):
        return None
    tool_name = tool_name_raw.strip()
    if not tool_name:
        return None

    args_field = part.get("input")
    if not isinstance(args_field, dict):
        state = part.get("state")
        if isinstance(state, dict):
            nested = cast("dict[str, object]", state).get("input")
            if isinstance(nested, dict):
                args_field = nested
    if not isinstance(args_field, dict):
        args_field = {}
    return tool_name, cast("dict[str, object]", args_field)


def _resolve_cursor_tool_call(
    cursor_tool_call: object,
) -> tuple[str, dict[str, object]] | None:
    """Extract Cursor ``tool_call.<toolName>ToolCall.args`` from live stream-json."""
    if not isinstance(cursor_tool_call, dict):
        return None
    cursor_tool_call_dict = cast("dict[str, object]", cursor_tool_call)
    for key, value in cursor_tool_call_dict.items():
        if key in _CURSOR_TOOL_CALL_METADATA_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        payload = cast("dict[str, object]", value)
        args_field = payload.get("args")
        if not isinstance(args_field, dict):
            args_field = payload.get("input")
        if not isinstance(args_field, dict):
            args_field = {}
        return key, cast("dict[str, object]", args_field)
    return None
