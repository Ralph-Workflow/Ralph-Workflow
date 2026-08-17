"""Parser for the Kimi Code CLI ``--output-format stream-json`` NDJSON wire format.

The Kimi Code CLI ``kimi`` binary, when invoked with
``-p <prompt> --output-format stream-json`` (the documented
non-interactive prompt mode), emits one JSON ``Message`` object per
line.  Source of truth: the official print-mode reference
(https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html)
plus the measured live v0.36.1 captures recorded in
``tests/agents/parsers/test_kimi_parser.py``.  The measured frame
vocabulary is:

  - ``role:"meta"`` frames carry runtime metadata under a ``type``
    subkey.  The measured shapes are ``system.version`` (a version
    banner) and ``session.resume_hint`` (the session-id frame whose
    ``session_id`` / ``command`` fields Ralph's session extractor
    reads from the raw line).  Surfaced as ``type='lifecycle'`` with
    non-empty content so the frames stay out of the operator-visible
    text stream while remaining observable in the activity stream.
  - ``role:"assistant"`` messages carry text as a string ``content``
    and optional ``tool_calls`` activity.  Text coalesces via
    :class:`TextAccumulator`; each tool call surfaces as
    ``type='tool_use'`` with the upstream ``id`` as
    ``metadata["tool_use_id"]`` so the subsequent ``role:"tool"``
    frame correlates.
  - ``role:"tool"`` messages carry a tool result as ``content`` keyed
    to ``tool_call_id``.  Surfaced as ``type='tool_result'``; a payload
    that declares an error (``is_error`` / ``isError`` truthy) surfaces
    as ``type='error'`` so the watchdog can see the failure.  (A
    top-level ``error`` key is already intercepted by
    :class:`NdjsonParserBase` before this parser runs.)
  - ``role:"user"`` messages are the input echo of JSON-input mode;
    suppressed (Ralph already knows the prompt it sent).

The documented envelope has NO terminal discriminator: no ``result``
frame, no ``stop`` frame, and no ``[DONE]`` sentinel is emitted by the
live binary.  Termination is the mechanically observable process exit
/ iterator exhaustion; :meth:`ParserTemplateBase.parse` then calls
:meth:`flush_accumulators` to emit the buffered text.  No assistant
message infers ``stop``.  The base class's ``[DONE]`` short-circuit is
preserved so a compatible wrapper that supplies the explicit sentinel
still terminates early.

Inherits from :class:`NdjsonParserBase` which owns the 6 shared NDJSON
behaviors: ``data:`` strip, ``[DONE]`` short-circuit, JSON parse
dispatch, lifecycle suppression, error extraction, and JSON-dict
dispatch.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

__all__ = ["KimiParser"]

_TEXT_KIND = "text"

# Sentinel flag mirroring the cursor parser's ``_handle_user`` idiom:
# the constant exists so the conditional ``yield`` body is recognized
# as a generator at type-check time while never executing at runtime.
_HANDLER_RETURNS_NO_EVENTS = False


def _message_content_text(content: object) -> str:
    """Return the visible text of a kimi message ``content`` field.

    The documented shape is a plain string; array-form content
    (``[{"type": "text", "text": "..."}]``) is accepted defensively
    because the print-mode reference documents it for user messages.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = cast("dict[str, object]", block).get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _tool_call_metadata(tool_call: dict[str, object]) -> tuple[str, dict[str, object], str | None]:
    """Return ``(tool_name, parsed_args, call_id)`` for one kimi tool_calls entry.

    The documented entry shape is
    ``{"type": "function", "id": "...", "function": {"name": "...",
    "arguments": "<json string>"}}``.  ``arguments`` is a JSON-encoded
    STRING (measured live: ``"{\\"command\\":\\"echo ...\\"}"``), so it is
    decoded into the canonical ``metadata["input"]`` dict the shared
    display payload builder reads; an undecodable payload degrades to
    an empty input dict (the raw call entry, including the original
    ``arguments`` string, is still preserved verbatim in metadata).
    """
    function = tool_call.get("function")
    name = "unknown"
    if isinstance(function, dict):
        function_dict = cast("dict[str, object]", function)
        raw_name = function_dict.get("name")
        if isinstance(raw_name, str) and raw_name:
            name = raw_name
        raw_arguments = function_dict.get("arguments")
        if isinstance(raw_arguments, str) and raw_arguments:
            try:
                decoded: object = json.loads(raw_arguments, strict=False)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                return name, cast("dict[str, object]", decoded), _tool_call_id(tool_call)
        if isinstance(raw_arguments, dict):
            return name, cast("dict[str, object]", raw_arguments), _tool_call_id(tool_call)
    return name, {}, _tool_call_id(tool_call)


def _tool_call_id(tool_call: dict[str, object]) -> str | None:
    call_id = tool_call.get("id")
    if isinstance(call_id, str) and call_id:
        return call_id
    return None


def _tool_payload_declares_error(obj: dict[str, object]) -> bool:
    """Return True when a ``role:"tool"`` frame declares a failed call."""
    for key in ("is_error", "isError"):
        flag = obj.get(key)
        if isinstance(flag, str):
            return flag.casefold() in {"true", "1", "yes"}
        if bool(flag):
            return True
    return False


class KimiParser(NdjsonParserBase):
    """Parser for the Kimi Code CLI ``--output-format stream-json`` wire format.

    Text accumulates via :class:`TextAccumulator` and flushes ONLY on a
    structural boundary (a tool call, a tool result, an error, or
    iterator exhaustion) — never on an assistant message, because the
    documented envelope has no per-message terminal discriminator.
    """

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        # R5: bind the per-invocation shared SubagentPidRegistry +
        # per-transport source label. Kimi's measured stream-json
        # envelope does not carry a ``pid`` field; the hook is
        # forward-compat for frames that carry one.
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: TextAccumulator = TextAccumulator()

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        # The base class post-processes the iterator to attach
        # ``source_timestamp`` to any AgentOutputLine that lacks one,
        # so the per-role dispatcher does not thread the parameter.
        del source_timestamp  # accepted for override compatibility; ignored
        role = str(obj.get("role", ""))
        if role == "assistant":
            yield from self._handle_assistant(obj, raw)
            return
        if role == "tool":
            yield from self._handle_tool(obj, raw)
            return
        if role == "meta":
            yield from self._handle_meta(obj, raw)
            return
        if role == "user":
            yield from self._handle_user(obj, raw)
            return
        # Forward-compat: any unknown role passes through with the role
        # as the AgentOutputLine type so a future Kimi release that
        # adds roles does not silently drop them.
        yield AgentOutputLine(type=role or "unknown", raw=raw, metadata=obj)

    def _handle_assistant(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """``assistant`` messages emit text and optional ``tool_calls`` activity.

        An assistant message NEVER emits ``stop``: the documented
        envelope has no terminal discriminator, so termination is
        iterator exhaustion (or the base ``[DONE]`` sentinel).
        """
        text = _message_content_text(obj.get("content"))
        if text:
            yield from self._text_accumulator.accumulate(
                text, raw, kind=_TEXT_KIND, keep_current_when_empty=False
            )
        raw_tool_calls = obj.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return
        if not any(isinstance(call, dict) for call in raw_tool_calls):
            return
        # A tool call is a structural boundary: drain pending text so
        # the runtime sees the preceding model text before the call.
        yield from self.flush_accumulators()
        for call in raw_tool_calls:
            if not isinstance(call, dict):
                continue
            call_dict = cast("dict[str, object]", call)
            tool_name, args, call_id = _tool_call_metadata(call_dict)
            metadata: dict[str, object] = {"tool": tool_name, "input": args, **call_dict}
            if call_id is not None:
                metadata["tool_use_id"] = call_id
            summary = self._tool_summary(tool_name, args)
            yield AgentOutputLine(
                type="tool_use",
                content=summary,
                raw=raw,
                metadata=metadata,
            )

    def _tool_summary(self, tool_name: str, args: dict[str, object]) -> str:
        """Return a short identifying summary for one tool call."""
        for key in ("file_path", "path", "command", "query", "pattern"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return f"{tool_name} {value}"
        return tool_name

    def _handle_tool(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """``tool`` messages emit tool results (or errors when declared)."""
        if _tool_payload_declares_error(obj):
            error_text = _message_content_text(obj.get("content")) or "tool execution failed"
            yield AgentOutputLine(type="error", content=error_text, raw=raw, metadata=obj)
            return
        # A tool result is a structural boundary for buffered text.
        yield from self.flush_accumulators()
        content = _message_content_text(obj.get("content"))
        call_id = obj.get("tool_call_id")
        metadata: dict[str, object] = dict(obj)
        if isinstance(call_id, str) and call_id:
            metadata["tool_use_id"] = call_id
            metadata["tool"] = f"tool_call {call_id}"
        else:
            metadata["tool"] = "tool_call"
        yield AgentOutputLine(type="tool_result", content=content, raw=raw, metadata=metadata)

    def _handle_meta(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """``meta`` frames surface as observable lifecycle events.

        ``system.version`` carries the CLI version banner;
        ``session.resume_hint`` carries the session id Ralph's session
        extractor reads from the raw line (the parser itself never
        re-derives it).  Any future ``meta`` type degrades observably
        instead of disappearing.
        """
        meta_type = str(obj.get("type", ""))
        content = _message_content_text(obj.get("content"))
        if meta_type == "system.version":
            version = obj.get("version")
            banner = version if isinstance(version, str) and version else meta_type
            content = content or f"kimi {banner}"
        elif meta_type == "session.resume_hint":
            session_id = obj.get("session_id")
            content = content or (
                f"kimi session {session_id}" if isinstance(session_id, str) and session_id else "kimi session"
            )
        else:
            content = content or f"kimi meta {meta_type or 'unknown'}"
        yield AgentOutputLine(type="lifecycle", content=content, raw=raw, metadata=obj)

    def _handle_user(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        """``user`` messages are the input echo of JSON-input mode; suppressed.

        Mirrors the cursor parser's behavior of not re-emitting input
        Ralph already sent.  The conditional ``yield`` below is never
        executed; it exists so the method is recognized as a generator.
        """
        if _HANDLER_RETURNS_NO_EVENTS:  # pragma: no cover
            yield from ()
        return

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Drain the text accumulator (the only termination-side flush)."""
        yield from self._text_accumulator.flush(kind=_TEXT_KIND)
