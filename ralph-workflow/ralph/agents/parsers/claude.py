"""Parser for Claude's NDJSON streaming format."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from ._event_classification import is_lifecycle_event
from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message, stringify_text_blocks
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_CLAUDE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^claude(?:/[^:\s]+)?(?=[ :]|$)")

_CLAUDE_TOP_LEVEL_LIFECYCLE: Final[frozenset[str]] = frozenset(
    {"message_start", "message_stop", "content_block_stop"}
)

# ``claude -p --output-format=stream-json`` top-level ``system`` subtypes
# that carry no agent-authored content -- pure transport/session plumbing
# (session init metadata, SessionStart hook execution echoes, and periodic
# thinking-token progress ticks). These fire many times per turn (e.g. one
# ``thinking_tokens`` event per ~15-20 estimated tokens of reasoning) and
# must be suppressed at the source, the same way lifecycle events are,
# rather than falling through to the generic dispatch fallback where each
# one would surface as a bare, content-less ``type="system"`` line and
# flood operator-visible output. Session-ID capture for these subtypes
# already happens independently via ``extract_transport_session_id`` on the
# raw line, so suppressing them here does not lose any signal.
_CLAUDE_NOISE_SYSTEM_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"init", "hook_started", "hook_response", "thinking_tokens"}
)

# ``stream_event.event`` (partial-message streaming, active whenever the
# harness passes ``--include-partial-messages``, which every production
# invocation does) carries a nested ``message_delta`` once per turn with a
# ``delta.stop_reason``. Expected end-of-turn reasons carry no actionable
# signal beyond "the turn ended normally" and are suppressed; any other
# reason (e.g. ``max_tokens`` truncation, ``refusal``, or a future reason
# this parser has not seen yet) is operator-relevant and still surfaces.
_CLAUDE_EXPECTED_STOP_REASONS: Final[frozenset[str]] = frozenset({"end_turn", "tool_use"})

# ``rate_limit_event.rate_limit_info.status`` values that mean "nothing to
# report" -- the account is comfortably within quota. Any other status
# (e.g. ``allowed_warning``, ``rejected``) or overage usage is surfaced.
_CLAUDE_RATE_LIMIT_OK_STATUSES: Final[frozenset[str]] = frozenset({"allowed"})


class ClaudeParser(NdjsonParserBase):
    """Parser for Claude's NDJSON streaming output with robust delta accumulation.

    Text deltas are accumulated into coherent blocks before emission, flushing on:
    - ``content_block_stop`` (end of a content block)
    - ``message_stop`` (end of the message)
    - ``\\n\\n`` paragraph boundary (incremental surfacing of long responses)

    Thinking deltas (``thinking_delta``) are accumulated separately from text
    deltas and emitted as ``type="thinking"`` lines.

    Inherits from :class:`NdjsonParserBase` and delegates the NDJSON
    scaffolding (``data:`` strip, ``[DONE]`` short-circuit, JSON parse,
    error extraction, lifecycle interception) to the base layer via
    :meth:`classify_line`.  Claude-specific behavior stays in subclass
    hooks:

      * :meth:`classify_line` first tries the prefixed-transcript parser
        (``[claude]:``, ``claude/...:``) and only delegates to the base
        when that hook returns ``None``.
      * :meth:`_handle_lifecycle_event` carries the claude-specific
        lifecycle side effects (``message_start`` recording,
        ``message_stop`` flush, ``content_block_stop`` flush) and
        returns ``None`` for lifecycle events the subclass wants to
        dispatch (e.g. ``assistant`` / ``user`` / ``thinking``).
      * :meth:`_dispatch_json_object` maps the per-event vocabulary
        (stream_event, content_block_delta, content_block_start,
        assistant, result, error) to :class:`AgentOutputLine` types
        and drives the per-content-block accumulator state.
    """

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        # R5 (Trustworthy Idle Watchdog spec): bind the per-invocation
        # shared SubagentPidRegistry. The base's
        # ``_try_register_subagent_pid_from_obj`` hook fires for every
        # structured child-lifecycle event observed by the parser; for
        # Claude's NDJSON shape the standard events do not currently
        # carry an embedded ``pid`` field, so this hook is mostly
        # forward-compat. The constructor signature stays backward-compat
        # (the registry kwarg defaults to ``None``).
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        # Bind the per-transport source label so any registered PID is
        # attributed to ``claude`` (the canonical registry source token).
        # ``None`` keeps the hook no-op when the parser is constructed
        # without a registry.
        self._subagent_source_label: str | None = subagent_source_label
        self._text_accumulator: dict[  # bounded-accumulator-ok: drained
            tuple[str, int], TextAccumulator
        ] = {}
        self._thinking_accumulator: dict[  # bounded-accumulator-ok: drained
            tuple[str, int], TextAccumulator
        ] = {}
        self._fallback_accumulator: TextAccumulator | None = None
        self._fallback_thinking_accumulator: TextAccumulator | None = None
        self._current_message_id: str | None = None
        self._seen_content_blocks: set[tuple[str, int]] = set()  # bounded-accumulator-ok: cleared

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        """Classify a single raw NDJSON line.

        Order of operations:

          1. Strip the line and short-circuit on empty.
          2. Try the claude-specific prefixed-transcript parser (e.g.
             ``claude/sonnet: hello``).  If it returns a non-None list,
             yield from it and return.
          3. Delegate the remaining NDJSON path to
             :class:`NdjsonParserBase` which owns the ``data:`` prefix
             strip, ``[DONE]`` short-circuit, JSON parse dispatch,
             error extraction, and lifecycle-event interception.  The
             base calls back into :meth:`_handle_lifecycle_event` for
             the claude-specific lifecycle side effects (message_start
             recording, message_stop flush, content_block_stop flush)
             and into :meth:`_dispatch_json_object` for the per-event
             dispatch (which routes claude's ``assistant`` / ``user``
             / ``thinking`` events through the subclass hook rather
             than suppressing them as the base's default lifecycle
             policy would).
        """
        stripped = line.strip()
        if not stripped:
            return

        prefixed_lines = self._parse_prefixed_transcript_line(stripped)
        if prefixed_lines is not None:
            yield from prefixed_lines
            return

        yield from super().classify_line(stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        for key in list(self._text_accumulator.keys()):
            yield from self._flush_text_accumulator(key)
        for key in list(self._thinking_accumulator.keys()):
            yield from self._flush_thinking_accumulator(key)
        yield from self._flush_fallback_accumulator()
        yield from self._flush_fallback_thinking_accumulator()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        if event_type == "message_start":
            self._record_message_start(obj)
            return iter(())
        if event_type == "message_stop":
            flushed = self.flush_accumulators()
            self._current_message_id = None
            self._seen_content_blocks.clear()
            return flushed
        if event_type == "content_block_stop":
            return self._flush_content_block(obj)
        if event_type in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            return iter(())
        return None

    def _record_message_start(self, obj: dict[str, object]) -> None:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        msg_id = str(message.get("id", ""))
        if msg_id:
            self._current_message_id = msg_id

    def _flush_content_block(self, obj: dict[str, object]) -> Iterator[AgentOutputLine]:
        index = obj.get("index")
        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._text_accumulator:
                yield from self._flush_text_accumulator(key)
            if key in self._thinking_accumulator:
                yield from self._flush_thinking_accumulator(key)

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        # R5: register any embedded PID into the shared registry BEFORE
        # the per-event dispatch returns. The hook is a no-op when the
        # parser was constructed without a registry or when the event
        # does not carry a PID field.
        self._try_register_subagent_pid_from_obj(obj)

        if event_type == "stream_event":
            event = obj.get("event")
            if isinstance(event, dict):
                yield from self._parse_stream_inner(event, raw)
            else:
                yield AgentOutputLine(type="stream_event", raw=raw, metadata=obj)
        elif event_type == "content_block_delta":
            yield from self._parse_content_block_delta(obj, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(obj)
            yield from self._parse_content_block_start(obj, raw)
        elif event_type in ("assistant", "user"):
            yield from self._parse_role_message(obj, raw)
        elif event_type == "result":
            yield from self._parse_result_event(obj, raw)
        elif event_type == "error":
            yield from self._parse_error_event(obj, raw)
        elif event_type == "system":
            yield from self._parse_system_event(obj, raw)
        elif event_type == "rate_limit_event":
            yield from self._parse_rate_limit_event(obj, raw)
        else:
            yield self._unclassified_event_line(event_type, obj, raw)

    def _parse_system_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Classify a top-level ``system`` event by its ``subtype``.

        Pure plumbing subtypes (see :data:`_CLAUDE_NOISE_SYSTEM_SUBTYPES`)
        are suppressed entirely. Any other subtype -- known (e.g.
        ``status``, ``compact_boundary``) or a future one this parser has
        not seen yet -- still surfaces so operators are not left blind to
        it, with the subtype (and, when present, a same-event ``status``
        field observed on the ``status`` subtype in production, e.g.
        ``status="requesting"``) as its content instead of an empty line.
        """
        subtype = str(obj.get("subtype", ""))
        if subtype in _CLAUDE_NOISE_SYSTEM_SUBTYPES:
            return
        status = obj.get("status")
        if isinstance(status, str) and status:
            content = f"{subtype} ({status})" if subtype else status
        else:
            content = subtype or "unknown"
        yield AgentOutputLine(type="system", content=content, raw=raw, metadata=obj)

    def _parse_rate_limit_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Classify a top-level ``rate_limit_event``.

        Fires on essentially every turn boundary. When the account is
        comfortably within quota (see :data:`_CLAUDE_RATE_LIMIT_OK_STATUSES`)
        and not drawing on overage, the event is pure noise and is
        suppressed. Anything else -- an unusual status or active overage
        usage -- is operator-relevant and surfaces with that status as
        content.
        """
        info = obj.get("rate_limit_info")
        info_dict: dict[str, object] = info if isinstance(info, dict) else {}
        status = str(info_dict.get("status", ""))
        using_overage = bool(info_dict.get("isUsingOverage", False))
        if status in _CLAUDE_RATE_LIMIT_OK_STATUSES and not using_overage:
            return
        yield AgentOutputLine(
            type="rate_limit_event",
            content=status or "unknown",
            raw=raw,
            metadata=obj,
        )

    def _unclassified_event_line(
        self,
        event_type: str,
        obj: dict[str, object],
        raw: str,
    ) -> AgentOutputLine:
        """Build a self-describing line for a wire event with no dedicated handler.

        Covers event types this parser has never seen (a future addition to
        the ``claude -p`` wire format). Falls back to ``subtype`` when
        present so an unrecognized event is still identifiable to an
        operator rather than rendering as a bare, content-less line.
        """
        subtype = obj.get("subtype")
        content = str(subtype) if subtype else ""
        return AgentOutputLine(type=event_type, content=content, raw=raw, metadata=obj)

    def _track_content_block_start(self, obj: dict[str, object]) -> None:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict) or self._current_message_id is None:
            return
        index = obj.get("index")
        if not isinstance(index, int):
            return
        block_type = str(content_block.get("type", ""))
        key = (self._current_message_id, index)
        if block_type == "text":
            if key not in self._text_accumulator:
                self._text_accumulator[key] = TextAccumulator()
        elif block_type == "thinking" and key not in self._thinking_accumulator:
            self._thinking_accumulator[key] = TextAccumulator()

    def _parse_stream_inner(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(event.get("type", "unknown"))

        if event_type == "content_block_delta":
            yield from self._parse_content_block_delta(event, raw)
        elif event_type == "content_block_start":
            self._track_content_block_start(event)
            yield from self._parse_stream_content_block_start(event, raw)
        elif event_type == "error":
            yield from self._parse_stream_error(event, raw)
        elif event_type == "message_delta":
            yield from self._parse_stream_message_delta(event, raw)
        elif event_type not in _CLAUDE_TOP_LEVEL_LIFECYCLE:
            yield self._unclassified_event_line(event_type, event, raw)

    def _parse_stream_message_delta(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Classify a ``stream_event``-nested ``message_delta``.

        Fires once per completed turn with ``delta.stop_reason``. Expected
        end-of-turn reasons (see :data:`_CLAUDE_EXPECTED_STOP_REASONS`) are
        suppressed as routine turn-boundary noise; any other reason --
        truncation, refusal, or a future reason this parser has not seen
        yet -- is operator-relevant and surfaces with the reason as content.
        """
        delta = event.get("delta")
        delta_dict: dict[str, object] = delta if isinstance(delta, dict) else {}
        stop_reason = delta_dict.get("stop_reason")
        if stop_reason is None or str(stop_reason) in _CLAUDE_EXPECTED_STOP_REASONS:
            return
        yield AgentOutputLine(type="message_delta", content=str(stop_reason), raw=raw, metadata=event)

    def _parse_content_block_delta(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        delta = obj.get("delta")
        if not isinstance(delta, dict):
            return

        delta_type = str(delta.get("type", "text_delta" if "text" in delta else ""))

        if delta_type == "thinking_delta":
            yield from self._accumulate_thinking_delta(obj, delta, raw)
            return

        if delta_type != "text_delta":
            return

        text = str(delta.get("text", ""))
        if not text:
            return

        index = obj.get("index")
        block_key: tuple[str, int] | None = None

        if isinstance(index, int) and self._current_message_id is not None:
            block_key = (self._current_message_id, index)
            if block_key in self._text_accumulator:
                yield from self._text_accumulator[block_key].accumulate(
                    text, raw, kind="text", keep_current_when_empty=False
                )
                return

        if self._fallback_accumulator is None:
            self._fallback_accumulator = TextAccumulator()
        yield from self._fallback_accumulator.accumulate(
            text, raw, kind="text", keep_current_when_empty=True
        )

    def _accumulate_thinking_delta(
        self,
        obj: dict[str, object],
        delta: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(delta.get("thinking", delta.get("text", "")))
        if not text.strip() and "\n\n" not in text:
            return

        index = obj.get("index")

        if isinstance(index, int) and self._current_message_id is not None:
            key = (self._current_message_id, index)
            if key in self._thinking_accumulator:
                yield from self._thinking_accumulator[key].accumulate(
                    text, raw, kind="thinking", keep_current_when_empty=False
                )
                return

        if self._fallback_thinking_accumulator is None:
            self._fallback_thinking_accumulator = TextAccumulator()
        yield from self._fallback_thinking_accumulator.accumulate(
            text, raw, kind="thinking", keep_current_when_empty=True
        )

    def _flush_text_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._text_accumulator:
            return
        acc = self._text_accumulator.pop(key)
        yield from acc.flush(kind="text")

    def _flush_thinking_accumulator(self, key: tuple[str, int]) -> Iterator[AgentOutputLine]:
        if key not in self._thinking_accumulator:
            return
        acc = self._thinking_accumulator.pop(key)
        yield from acc.flush(kind="thinking", require_strip=True)

    def _flush_fallback_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_accumulator is None:
            return
        acc = self._fallback_accumulator
        self._fallback_accumulator = None
        yield from acc.flush(kind="text")

    def _flush_fallback_thinking_accumulator(self) -> Iterator[AgentOutputLine]:
        if self._fallback_thinking_accumulator is None:
            return
        acc = self._fallback_thinking_accumulator
        self._fallback_thinking_accumulator = None
        yield from acc.flush(kind="thinking", require_strip=True)

    def _parse_result_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        subtype = str(obj.get("subtype", ""))
        if subtype == "error":
            error = str(obj.get("error", "unknown error"))
            yield AgentOutputLine(type="error", content=error, raw=raw, metadata=obj)
            return

        result = str(obj.get("result", ""))
        if result:
            yield AgentOutputLine(type="text", content=result, raw=raw, metadata=obj)

    def _parse_content_block_start(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = obj.get("content_block")
        if not isinstance(content_block, dict):
            return

        yield from self._parse_content_block(content_block, raw)

    def _parse_error_event(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = extract_error_message(obj)
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=obj)

    def _parse_stream_content_block_start(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        content_block = event.get("content_block")
        if not isinstance(content_block, dict):
            return

        yield from self._parse_content_block(content_block, raw)

    def _parse_stream_error(
        self,
        event: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        error = event.get("error")
        if isinstance(error, dict):
            error_msg = str(error.get("message", error.get("code", "unknown error")))
        else:
            error_msg = "unknown error"
        yield AgentOutputLine(type="error", content=error_msg, raw=raw, metadata=event)

    def _parse_role_message(
        self,
        obj: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Classify a top-level ``assistant`` or ``user`` message event.

        Both wire shapes carry the same ``message.content`` block-list
        envelope; the only difference is which role authored it. A
        top-level ``user`` event is how ``claude -p`` echoes a tool_result
        back after a tool call -- without this branch that content
        (including tool failures) was silently dropped, since previously
        only ``assistant`` was dispatched here.
        """
        message = obj.get("message")
        if not isinstance(message, dict):
            return

        content = message.get("content")
        if not isinstance(content, list):
            return

        yield from self._parse_message_content(content, raw)

    def _parse_message_content(
        self,
        content: list[object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        for block in content:
            if not isinstance(block, dict):
                continue

            block_obj = cast("dict[str, object]", block)
            block_type = str(block_obj.get("type", ""))
            if block_type == "text":
                text = str(block_obj.get("text", ""))
                if text:
                    yield AgentOutputLine(type="text", content=text, raw=raw, metadata=block_obj)
                continue

            if block_type == "tool_use":
                tool_name = str(block_obj.get("name", "unknown"))
                yield AgentOutputLine(
                    type="tool_use", content=tool_name, raw=raw, metadata=block_obj
                )
                continue

            if block_type == "tool_result":
                yield from self._parse_tool_result(block_obj, raw)
                continue

            if block_type == "thinking":
                text = str(block_obj.get("thinking", block_obj.get("text", "")))
                if text.strip():
                    yield AgentOutputLine(
                        type="thinking", content=text, raw=raw, metadata=block_obj
                    )
                continue

            yield AgentOutputLine(
                type="error",
                content=f"unsupported content block type '{block_type}' in agent output",
                raw=raw,
                metadata=block_obj,
            )

    def _parse_plain_text_prefix(self, raw: str, text: str) -> list[AgentOutputLine]:
        if is_lifecycle_event(text) or text.startswith("system (status="):
            return []
        return [AgentOutputLine(type="text", content=text, raw=raw)]

    def _parse_structured_remainder(self, raw: str, remainder: str) -> list[AgentOutputLine] | None:
        for role in ("user", "assistant"):
            role_prefix = f" {role}: message="
            if remainder.startswith(role_prefix):
                return self._parse_prefixed_message_line(raw, remainder[len(role_prefix) :])
        if remainder.startswith(" message_delta") or remainder.startswith(" system: status="):
            return []
        if remainder.startswith(" ✗: "):
            return [AgentOutputLine(type="error", content=remainder[4:], raw=raw)]
        return None

    def _parse_prefixed_transcript_line(self, raw: str) -> list[AgentOutputLine] | None:
        if raw.startswith("[claude]:"):
            return []
        m = _CLAUDE_PREFIX_RE.match(raw)
        if m is None:
            return None
        remainder = raw[m.end() :]
        if remainder.startswith(": "):
            return self._parse_plain_text_prefix(raw, remainder[2:])
        if remainder.startswith(" tool: "):
            return self._parse_prefixed_tool_line(raw, remainder[7:])
        return self._parse_structured_remainder(raw, remainder)

    def _parse_prefixed_tool_line(self, raw: str, payload: str) -> list[AgentOutputLine]:
        payload = payload.strip()
        tool_name, has_details, detail_suffix = payload.partition(" (")
        metadata: dict[str, object] = {}
        if has_details and detail_suffix.endswith(")"):
            metadata["input"] = {"args": detail_suffix[:-1]}
        return [
            AgentOutputLine(
                type="tool_use",
                content=tool_name.strip() or "unknown",
                raw=raw,
                metadata=metadata,
            )
        ]

    def _parse_prefixed_message_line(
        self, raw: str, json_payload: str
    ) -> list[AgentOutputLine] | None:
        try:
            parsed: object = json.loads(json_payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None

        content = parsed.get("content")
        if not isinstance(content, list):
            return []

        return list(self._parse_message_content(content, raw))

    def _parse_content_block(
        self,
        content_block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        block_type = str(content_block.get("type", "unknown"))

        if block_type == "text":
            text = str(content_block.get("text", ""))
            if text:
                yield AgentOutputLine(type="text", content=text, raw=raw, metadata=content_block)
            return

        if block_type == "tool_use":
            tool_name = str(content_block.get("name", "unknown"))
            yield AgentOutputLine(
                type="tool_use", content=tool_name, raw=raw, metadata=content_block
            )
            return

        if block_type == "tool_result":
            yield from self._parse_tool_result(content_block, raw)
            return

        if block_type == "thinking":
            return

        yield AgentOutputLine(
            type="error",
            content=f"unsupported content block type '{block_type}' in agent output",
            raw=raw,
            metadata=content_block,
        )

    def _parse_tool_result(
        self,
        block: dict[str, object],
        raw: str,
    ) -> Iterator[AgentOutputLine]:
        """Classify a ``tool_result`` content block.

        Honors the wire-format ``is_error`` flag (matching the precedent
        already established by the Cursor, Pi, and Generic parsers): a
        failed tool call surfaces as ``type="error"`` instead of
        ``type="tool_result"`` so a tool failure is visually distinct and
        counted as a break signal, rather than reading as a routine result.
        The message text lives in ``content`` either way -- Claude's
        tool_result envelope has no separate ``error`` field to fall back
        to.
        """
        result_type = "error" if block.get("is_error") else "tool_result"
        content = block.get("content")
        if content is None:
            yield AgentOutputLine(type=result_type, content="", raw=raw, metadata=block)
            return

        if isinstance(content, list):
            tool_result = stringify_text_blocks(content, require_text_type=True)
            yield AgentOutputLine(
                type=result_type,
                content=tool_result,
                raw=raw,
                metadata=block,
            )
            return

        yield AgentOutputLine(type=result_type, content=str(content), raw=raw, metadata=block)
