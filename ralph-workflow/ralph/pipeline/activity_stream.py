"""Activity stream rendering and artifact handoff for the pipeline runner."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger
from rich.text import Text

from ralph.agents.invoke import extract_transport_session_id
from ralph.agents.parsers import AgentOutputLine, AgentParser, get_parser, resolve_parser_key
from ralph.config.enums import AgentTransport
from ralph.display.activity_router import map_parser_type_to_kind
from ralph.display.parallel_display import (
    ParallelDisplay,
    emit_activity_line,
    subscriber_for_display,
)
from ralph.display.tool_args import format_tool_input
from ralph.mcp.server._activity_sink import invoke_subagent_sink

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable, Iterable, Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry
    from ralph.config.agent_config import AgentConfig
    from ralph.display.context import DisplayContext
    from ralph.display.subscriber import PipelineSubscriber

if TYPE_CHECKING:

    class _ParallelDisplayModule(Protocol):
        ParallelDisplay: type[ParallelDisplay]


_MAX_TEXT_LENGTH = 200
_MAX_TOOL_RESULT_BRIEF = 80
_MAX_METADATA_SUMMARY_LENGTH = 120
_MAX_RETAINED_TOOL_TARGETS = 128


def _parallel_display_cls() -> type[ParallelDisplay]:
    module = cast(
        "_ParallelDisplayModule", import_module("ralph.display.parallel_display")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return module.ParallelDisplay


def _terminal_width() -> int:
    return shutil.get_terminal_size().columns or 80


def _available_width(prefix_len: int) -> int:
    return max(40, _terminal_width() - prefix_len - 2)


def stream_parsed_agent_activity(
    lines: Iterable[object],
    parser_type: str,
    agent_name: str,
    display: ParallelDisplay | None = None,
    *,
    agent_config: AgentConfig | None = None,
    **kwargs: object,
) -> None:
    """Stream and render parsed agent output lines.

    Accepts and forwards the per-invocation
    ``subagent_pid_registry=`` and ``subagent_source_label=`` kwargs
    into the resolved parser so the parser's structured-event hook
    registers any embedded PID into the shared registry (R1 / R5 of
    the Trustworthy Idle Watchdog spec). Both kwargs are optional;
    legacy callers continue to work without them.
    """
    transport = cast(
        "AgentTransport | None", kwargs.get("transport")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    display_context = cast(
        "DisplayContext | None", kwargs.get("display_context")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    raw_output_sink = cast(
        "deque[str] | list[str] | None", kwargs.get("raw_output_sink")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    rendered_output_sink = cast(
        "deque[str] | list[str] | None", kwargs.get("rendered_output_sink")
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    session_id_sink = cast("Callable[[str], None] | None", kwargs.get("session_id_sink"))
    subagent_pid_registry = cast(
        "SubagentPidRegistry | None",
        kwargs.get("subagent_pid_registry"),
    )
    subagent_source_label = cast(
        "str | None",
        kwargs.get("subagent_source_label"),
    )

    if agent_config is not None:
        parser_key = resolve_parser_key(
            agent_config.cmd,
            agent_config.json_parser,
            cast(
                "AgentTransport", agent_config.transport
            ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        )
    else:
        parser_key = (
            "claude_interactive" if transport == AgentTransport.CLAUDE_INTERACTIVE else parser_type
        )
    parser = _resolve_parser(
        parser_key,
        subagent_pid_registry=subagent_pid_registry,
        subagent_source_label=subagent_source_label,
    )

    def _iter_lines() -> Iterator[str]:
        for line in lines:
            text = str(line)
            if raw_output_sink is not None:
                raw_output_sink.append(text)
            session_id = extract_transport_session_id((text,))
            if session_id is not None and session_id_sink is not None:
                session_id_sink(session_id)
            yield text

    parallel_display_cls = _parallel_display_cls()
    subscriber = subscriber_for_display(display)
    emit_hook_raw: object = getattr(parser, "emit_subagent_activity", None)
    # Cache for the latest sanitized subagent summary so the
    # SUBAGENT_PROGRESS display event can re-use the exact string the
    # sink received (avoids re-sanitizing or re-emitting raw payload).
    last_subagent_summary: list[str] = []
    tool_targets: dict[str, dict[str, object]] = {}
    for parsed_line in parser.parse(_iter_lines()):
        retained_line = _retain_tool_result_target(parsed_line, tool_targets)
        # Forward parsed lines to the per-parser subagent sink so the
        # idle watchdog's per-channel evidence surface stays fresh for
        # ALL parsers (Claude, OpenCode, Codex, Gemini, Pi, Agy,
        # Generic, ClaudeInteractive).  The contextvar is bound by the
        # line readers (_process_reader / _pty_line_reader) before the
        # first yield so the sink reaches the per-run watchdog
        # closure.  The call is wrapped in try/except so a buggy
        # parser hook cannot crash the activity stream.
        if callable(emit_hook_raw):
            emit_hook = cast(
                "Callable[[AgentOutputLine, Callable[[str], None]], None]",
                emit_hook_raw,
            )
            last_subagent_summary.clear()
            try:
                _capture_summary_into(retained_line, emit_hook, last_subagent_summary)
            except Exception:
                logger.debug("parser.emit_subagent_activity failed", exc_info=True)
        rendered = _render_agent_activity_line(retained_line, agent_name)
        if rendered is not None and rendered_output_sink is not None:
            rendered_output_sink.append(rendered.plain)
        if isinstance(display, parallel_display_cls):
            kind = map_parser_type_to_kind(retained_line.type)
            # DA-002 (wt-028-display S-2 / AC-01): the parser-to-display
            # handoff forwards the source-event timestamp the parser
            # extracted so the rendered record carries ``[hh:mm:ss]``
            # from the agent's own clock rather than the display
            # clock. ``None`` keeps the pre-fix display-clock
            # fallback for any parser that does not yet yield a
            # source timestamp.
            display.emit_parsed_event(
                agent_name,
                kind,
                retained_line.content,
                retained_line.metadata or {},
                timestamp=retained_line.timestamp,
            )
            # emit_parsed_event already records a tool_use on the display's
            # subscriber; recording it again here would double-count the repeat
            # counter (a single call would render "(x2)"). Record only non-tool
            # lines here on the parallel path.
            record_on_subscriber = retained_line.type != "tool_use"
        else:
            if rendered is not None:
                emit_activity_line(display, None, rendered.plain, display_context=display_context)
            record_on_subscriber = True
        if subscriber is not None and record_on_subscriber:
            _record_activity_on_subscriber(subscriber, retained_line, rendered, agent_name)


def _tool_correlation_key(metadata: dict[str, object], tool_name: str) -> str:
    """Return the documented cross-parser call identifier, falling back to tool name."""
    for field in ("tool_call_id", "toolCallId", "tool_use_id", "id"):
        value = metadata.get(field)
        if isinstance(value, str) and value:
            return value
    return tool_name


def _retain_tool_result_target(
    line: AgentOutputLine, tool_targets: dict[str, dict[str, object]]
) -> AgentOutputLine:
    """Carry a tool call's structured target into its matching result metadata."""
    metadata = line.metadata
    tool = metadata.get("tool")
    tool_name = tool if isinstance(tool, str) else (line.content or "")
    key = _tool_correlation_key(metadata, tool_name)
    if line.type == "tool_use":
        raw_input = metadata.get("input", metadata.get("args", metadata.get("arguments")))
        target = format_tool_input(raw_input)
        if target:
            retained: dict[str, object] = {"target": target, "tool_name": tool_name}
            payload: object = raw_input
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = None
            if isinstance(payload, dict):
                for field in ("path", "file_path", "filePath", "filename"):
                    value = payload.get(field)
                    if isinstance(value, str) and value:
                        retained["tool_path"] = value
                        break
                for field in ("line_start", "offset"):
                    value = payload.get(field)
                    if isinstance(value, int) and not isinstance(value, bool):
                        retained[field] = value
            if key:
                if len(tool_targets) >= _MAX_RETAINED_TOOL_TARGETS:
                    tool_targets.pop(next(iter(tool_targets)))
                tool_targets[key] = retained
        return line
    if line.type != "tool_result" or not key or metadata.get("target"):
        return line
    retained_target = tool_targets.pop(key, None)
    if not retained_target:
        return line
    return replace(line, metadata={**metadata, **retained_target})


def _capture_summary_into(
    parsed_line: AgentOutputLine,
    emit_hook: Callable[[AgentOutputLine, Callable[[str], None]], None],
    sink_buffer: list[str],
) -> None:
    """Invoke the parser hook with a capturing sink that records the summary.

    Mirrors the activity-stream ``emit_subagent_activity`` invocation
    but records the emitted summary into ``sink_buffer`` so the
    parallel display can re-use the same sanitized string for the
    ``SUBAGENT_PROGRESS`` display event.  A buggy hook that raises
    is swallowed here too so the activity stream continues.
    """

    def _capturing_sink(summary: str) -> None:
        sink_buffer.append(summary)
        try:
            invoke_subagent_sink(summary)
        except Exception:
            return

    try:
        emit_hook(parsed_line, _capturing_sink)
    except Exception:
        return


def _record_activity_on_subscriber(
    subscriber: PipelineSubscriber,
    parsed_line: AgentOutputLine,
    rendered: Text | None,
    agent_name: str,
) -> None:
    try:
        if parsed_line.type == "thinking" and parsed_line.content.strip():
            line_text = parsed_line.content.strip()
        else:
            line_text = "" if rendered is None else rendered.plain
        metadata = parsed_line.metadata
        tool_name: str | None = None
        metadata_tool = metadata.get("tool")
        if isinstance(metadata_tool, str) and metadata_tool.strip():
            tool_name = metadata_tool.strip()
        elif parsed_line.type == "tool_use":
            stripped = parsed_line.content.strip()
            if stripped:
                tool_name = stripped
        path = _format_metadata_value(metadata.get("path")) or None
        workdir = _format_metadata_value(metadata.get("workdir")) or None
        command = _format_metadata_value(metadata.get("command")) or None
        subscriber.record_activity(
            unit_id=agent_name,
            line=line_text,
            agent_name=agent_name,
            tool_name=tool_name,
            path=path,
            workdir=workdir,
            command=command,
        )
    except Exception:
        logger.debug("subscriber.record_activity failed", exc_info=True)


def _resolve_parser(
    parser_type: str,
    *,
    subagent_pid_registry: SubagentPidRegistry | None = None,
    subagent_source_label: str | None = None,
) -> AgentParser:
    """Resolve a parser instance by ``parser_type``.

    R1 / R5 (Trustworthy Idle Watchdog spec): when the caller threads a
    shared ``SubagentPidRegistry`` plus a per-transport source label
    through this helper, the registry is forwarded into
    ``get_parser`` so the parser's structured-event handler can
    register any embedded PID into the registry. The registration
    flows back to ``ProcessMonitor.spawned_subagent_count()`` through
    the existing per-transport ``SubagentPidSource`` seam, so the
    watchdog sees real subagent PIDs as they appear in the agent's
    stream (defense-in-depth against the broader
    ``descendant_snapshot()`` count).

    Both kwargs are keyword-only and default to ``None`` so legacy
    callers (the smoke plumbing, commit plumbing) that invoke
    ``_resolve_parser(parser_type)`` continue to work without
    changes.
    """
    try:
        return get_parser(
            parser_type,
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
    except ValueError:
        logger.warning("Unknown parser '{}'; falling back to generic", parser_type)
        return get_parser(
            "generic",
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )


def _truncate(text: str, max_length: int) -> str:
    if max_length <= 1 or len(text) <= max_length:
        return text
    return text[:max_length] + "…"


def _render_agent_activity_line(output: AgentOutputLine, agent_name: str) -> Text | None:
    """Render an agent event through the single registry.

    After the wt-028-display consolidation, this function constructs a
    canonical :class:`AgentActivityEvent` from the parser-shaped
    :class:`AgentOutputLine` via the shared normalizer
    (:func:`ralph.display.agent_event_renderer.normalize_event_from_agent_output_line`)
    so agent-specific quirks (claude / codex / opencode / ...) are
    removed BEFORE rendering. It then delegates every presentation
    decision to the single registry via
    :func:`ralph.display.agent_event_renderer.render_event` and
    extracts the plain text from the returned :class:`rich.text.Text`
    so downstream callers that expect a styled Text keep working
    unchanged.

    The previous per-type helpers (``_render_text_line`` /
    ``_render_tool_use_line`` / ``_render_tool_result_line`` /
    ``_render_error_line`` / ``_render_metadata_event_line`` /
    ``_tool_input_summary`` / ``_metadata_summary`` / ``_kv_summary``
    / ``_styled_prefix``) were the pipeline runner's competing
    formatter; they have been deleted and this function is the
    single rendering seam on the pipeline-runnner side.

    Production call sites construct typed events at ingestion and call
    :func:`render_event` directly. The plain-text adapter
    ``render_event_kind_text`` is kept only at the final presentation
    boundary (for paths that don't carry a Console / unit_id and need
    a plain string back), not in the rendering seam.
    """
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_event_renderer import (
        normalize_event_from_agent_output_line,
        render_event,
    )

    # The pipeline runner's caller chain does not always carry a
    # parser-shaped ``provider`` hint (it predates the typed-event
    # boundary). Use ``UNKNOWN`` so the canonical normalizer never has
    # to invent one; backend-specific quirks are already removed by
    # ``make_event`` -> ``map_parser_type_to_kind`` so the registry
    # renders the same line regardless of which provider fed the
    # ``AgentOutputLine`` here.
    event = normalize_event_from_agent_output_line(
        output, provider=ActivityProvider.UNKNOWN, unit_id=agent_name
    )
    from ralph.display.agent_event_renderer import _truncate_to_cells

    text = render_event(event, unit_id=agent_name, escape_body=False)
    plain = _truncate_to_cells(text.plain, 200)
    if not plain:
        return None
    return Text(plain)


def _format_metadata_value(value: object) -> str | None:
    """Return the metadata value if it's a non-empty string, else ``None``.

    Used by :func:`_record_activity_on_subscriber` to extract clean
    string values for ``path`` / ``workdir`` / ``command`` slots.
    """
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


# NOTE: the per-type render helpers ``_render_text_line``,
# ``_render_tool_use_line``, ``_render_tool_result_line``,
# ``_render_error_line``, ``_render_metadata_event_line``,
# ``_tool_input_summary``, ``_metadata_summary``, ``_kv_summary``,
# ``_styled_prefix`` were the pipeline runner's competing
# agent-output formatter. After the wt-028-display consolidation
# they are deleted; :mod:`ralph.display.agent_event_renderer` is
# the single source of truth for agent-event presentation
# decisions.


render_agent_activity_line = _render_agent_activity_line
record_activity_on_subscriber = _record_activity_on_subscriber
# ``truncate`` / ``available_width`` / ``terminal_width`` / ``MAX_*``
# were removed when the per-type render helpers were consolidated into
# the agent-event renderer registry. External callers should use
# :mod:`ralph.display.agent_event_renderer` directly instead.
truncate = _truncate
available_width = _available_width
terminal_width = _terminal_width
# Truncation limits exposed for backward compatibility with tests that
# assert the legacy ``MAX_TEXT_LENGTH`` / ``MAX_TOOL_RESULT_BRIEF``
# constants. The registry's own cell-aware truncation uses 200 cells
# by default (see ``_METADATA_SUMMARY_MAX_CHARS`` and the
# ``_truncate_to_cells`` helper); these aliases pin the historical
# values so the suite's outer surface stays unchanged.
MAX_TEXT_LENGTH = _MAX_TEXT_LENGTH
MAX_TOOL_RESULT_BRIEF = _MAX_TOOL_RESULT_BRIEF
MAX_METADATA_SUMMARY_LENGTH = _MAX_METADATA_SUMMARY_LENGTH


def metadata_summary(metadata: dict[str, object]) -> str:
    """Backwards-compatible metadata summary shim.

    Returns the registry's stable ``key=value, ...`` summary for the
    preferred metadata keys (status / summary / phase / decision /
    message / event / tool / path / workdir / command). Kept as a
    free function so the existing pipeline-runner test that asserts
    ``runner_module.metadata_summary`` still works.
    """
    from ralph.display.agent_event_renderer import _metadata_summary

    return _metadata_summary(metadata)
