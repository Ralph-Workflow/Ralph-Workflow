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
from ralph.display._tool_correlation import tool_call_id
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
    module = cast("_ParallelDisplayModule", import_module("ralph.display.parallel_display"))
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
    transport = cast("AgentTransport | None", kwargs.get("transport"))
    display_context = cast("DisplayContext | None", kwargs.get("display_context"))
    raw_output_sink = cast("deque[str] | list[str] | None", kwargs.get("raw_output_sink"))
    rendered_output_sink = cast("deque[str] | list[str] | None", kwargs.get("rendered_output_sink"))
    session_id_sink = cast("Callable[[str], None] | None", kwargs.get("session_id_sink"))
    raw_line_sink = cast("Callable[[str], None] | None", kwargs.get("raw_line_sink"))
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
            cast("AgentTransport", agent_config.transport),
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
            if raw_line_sink is not None:
                raw_line_sink(text)
            session_id = extract_transport_session_id((text,))
            if session_id is not None and session_id_sink is not None:
                session_id_sink(session_id)
            yield text

    parallel_display_cls = _parallel_display_cls()
    subscriber = subscriber_for_display(display)
    emit_hook_raw: object = getattr(parser, "emit_subagent_activity", None)
    last_subagent_summary: list[str] = []
    tool_targets: dict[str, dict[str, object]] = {}
    for parsed_line in parser.parse(_iter_lines()):
        retained_line = _retain_tool_result_target(parsed_line, tool_targets)
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
            display.emit_parsed_event(
                agent_name,
                kind,
                retained_line.content,
                retained_line.metadata or {},
                timestamp=retained_line.timestamp,
            )
            record_on_subscriber = retained_line.type != "tool_use"
        else:
            if rendered is not None:
                emit_activity_line(display, None, rendered.plain, display_context=display_context)
            record_on_subscriber = True
        if subscriber is not None and record_on_subscriber:
            _record_activity_on_subscriber(subscriber, retained_line, rendered, agent_name)


def _tool_correlation_key(metadata: dict[str, object], tool_name: str) -> str:
    return tool_call_id(metadata) or tool_name


def _retain_tool_result_target(
    line: AgentOutputLine, tool_targets: dict[str, dict[str, object]]
) -> AgentOutputLine:
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
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_event_renderer import (
        normalize_event_from_agent_output_line,
        render_event,
    )

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
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    return None


render_agent_activity_line = _render_agent_activity_line
record_activity_on_subscriber = _record_activity_on_subscriber
truncate = _truncate
available_width = _available_width
terminal_width = _terminal_width
MAX_TEXT_LENGTH = _MAX_TEXT_LENGTH
MAX_TOOL_RESULT_BRIEF = _MAX_TOOL_RESULT_BRIEF
MAX_METADATA_SUMMARY_LENGTH = _MAX_METADATA_SUMMARY_LENGTH


def metadata_summary(metadata: dict[str, object]) -> str:
    from ralph.display.agent_event_renderer import _metadata_summary

    return _metadata_summary(metadata)
