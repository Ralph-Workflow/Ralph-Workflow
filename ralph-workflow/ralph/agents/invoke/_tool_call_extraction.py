"""Tool-call extraction from raw activity lines.

Extracted from ``_process_reader.py`` to keep the line-reader module
under the 1000-line policy cap enforced by
``ralph.testing.audit_repo_structure`` (wired into ``make verify``). The helpers are
unchanged - they are best-effort extractors of
``(tool_name, tool_args)`` pairs from the canonical envelope
shapes consumed by the tool-call repetition breaker
(``RepetitionTracker.mark_tool_call``).

Recognised envelope shapes:

* ``{"type": "tool_use", "name": "...", "input": {...}}``
* ``{"type": "tool_use", "tool_name": "...", "arguments": {...}}``
* ``{"type": "assistant", "message": {"content": [{"type": "tool_use",
  "name": "...", "input": {...}}]}}`` (Claude stream-json assistant message --
  the COMPLETE call; preferred over the streaming placeholder below)
* ``{"type": "stream_event", "event": {"type": "content_block_start",
  "content_block": {"type": "tool_use", "name": "...", "input": {...}}}}``
  (Claude content_block_start wrapped in stream_event; skipped when its
  ``input`` is still the empty streaming placeholder)
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

    Walks the envelope shapes listed in the module docstring so the tool-call
    circuit breaker (``RepetitionTracker.mark_tool_call``) sees a stable
    fingerprint per ``(tool_name, tool_args)`` pair. Reaches every transport
    that classifies a line as TOOL_USE -- today opencode, claude,
    claude_interactive, pi, and cursor. codex, agy, nanocoder, and generic all
    route through ``GenericExecutionStrategy``, which never emits TOOL_USE, so
    the breaker is unreachable on those four.

    Returns ``None`` when the line is not recognisably a tool-use line, when
    the structure is not understood, or when nothing distinguishing could be
    recovered. An empty / missing name falls back to ``"unknown"``.
    """
    plain = _extract_plain_text_tool_call(raw)
    if plain is not None:
        return plain
    top_level = _parse_json_object(raw)
    if top_level is None:
        return None
    nested = _extract_claude_message_tool_use(top_level)
    if nested is not None:
        return _extract_tool_call_from_dict(nested)
    obj = _unwrap_tool_use_envelope(top_level)
    if _is_streaming_tool_placeholder(obj):
        return None
    return _extract_tool_call_from_dict(obj)


def _parse_json_object(raw: str) -> dict[str, object] | None:
    """Parse ``raw`` into a JSON object, or ``None`` when it is not one."""
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return cast("dict[str, object]", parsed)


def _extract_claude_message_tool_use(obj: dict[str, object]) -> dict[str, object] | None:
    """Return the ``tool_use`` block nested in a Claude assistant/user message.

    Claude's ``--output-format=stream-json`` does not put the tool call at the
    top level; it nests the COMPLETE call -- name and fully-populated
    ``input`` -- in ``message.content[]``::

        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}

    ``_is_tool_use_dict`` rejects ``type == "assistant"``, so this shape used to
    return ``None`` and the ONLY Claude frame reaching the breaker was the
    argless streaming placeholder (see :func:`_is_streaming_tool_placeholder`).
    That left five different ``Bash`` commands fingerprinting identically as
    ``Bash|{}`` -- a false ``REPEATED_IDENTICAL_TOOL_CALL`` kill. Preferring
    this shape gives the breaker the real arguments instead.
    """
    if obj.get("type") not in {"assistant", "user"}:
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = cast("dict[str, object]", message).get("content")
    if not isinstance(content, list):
        return None
    blocks = [
        cast("dict[str, object]", block)
        for block in cast("list[object]", content)
        if isinstance(block, dict) and cast("dict[str, object]", block).get("type") == "tool_use"
    ]
    if len(blocks) != 1:
        # A message batching several parallel calls cannot be represented as
        # ONE (name, args) fingerprint. Taking the first block silently
        # discarded the rest, so N genuinely different parallel calls all keyed
        # to the first one and a fully progressing agent looked 100% repetitive.
        # Claude Code splits parallel calls into one frame each, so this is a
        # guard against a shape that is rare rather than a live false kill --
        # skipping the observation is the safe answer either way.
        return None
    return blocks[0]


def _is_streaming_tool_placeholder(obj: dict[str, object]) -> bool:
    """Return True for a ``content_block_start`` tool frame with no arguments yet.

    Claude opens a tool call with ``"input": {}`` and streams the real
    arguments afterwards as ``input_json_delta`` frames. Fingerprinting the
    placeholder keys every call of one tool to ``<name>|{}`` regardless of what
    it was actually called with, so N different invocations of the same tool
    look like one call repeated N times. The complete call arrives separately
    in the assistant message, which is where the breaker gets its fingerprint.
    """
    if obj.get("type") != "tool_use":
        return False
    tool_input = obj.get("input")
    return isinstance(tool_input, dict) and not tool_input and "id" in obj


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


def _unwrap_tool_use_envelope(obj: dict[str, object]) -> dict[str, object]:
    """Unwrap ``stream_event`` / ``assistantMessageEvent`` wrappers.

    Returns the inner dict (after unwrapping ``stream_event`` ->
    ``content_block``), or ``obj`` unchanged when no wrapper is present.
    """
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
        return key, _strip_per_call_keys(cast("dict[str, object]", args_field))
    return None


#: Keys that vary on EVERY call and must never enter a fingerprint. Cursor
#: embeds its per-call id inside ``args`` (verified against a live
#: ``cursor-agent --output-format stream-json`` run), so leaving them in made
#: two byte-identical ``ls -la`` shell calls look like two different calls and
#: the breaker could never fire on this transport.
_PER_CALL_ARG_KEYS = frozenset(
    {
        "toolCallId",
        "tool_call_id",
        "callID",
        "call_id",
        "conversationId",
        "conversation_id",
        "startedAtMs",
        "completedAtMs",
    }
)


def _strip_per_call_keys(args: dict[str, object]) -> dict[str, object]:
    """Drop per-call identifiers so identical calls share one fingerprint."""
    return {key: value for key, value in args.items() if key not in _PER_CALL_ARG_KEYS}
