from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

from rich.markup import escape
from rich.text import Text

from ralph.display._channel_prefix_stripper import (
    _PARSER_CHANNEL_PREFIXES,
    _PARSER_CHANNEL_PREFIXES_SPACELESS,
    strip_parser_channel_prefix,
)
from ralph.display._edit_preview_render import highlight_code_spans
from ralph.display._tool_correlation import tool_call_id
from ralph.display._tool_result_dedup import strip_duplicate_tool_prefix
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import make_event
from ralph.display.activity_provider import ActivityProvider
from ralph.display.activity_router import map_parser_type_to_kind
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.presented_entry import outcome_is_failure
from ralph.display.theme import (
    _DISPLAY_IDENTITY_ACTIVE_SET,
    STATUS_STYLES,
    identity_color,
    pick_status_styles,
)
from ralph.display.tool_args import format_tool_input, friendly_tool_name

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.display.context import DisplayContext


_STYLE_KEY = "style"
_ICON_KEY = "icon"
_LABEL_KEY = "label"


_UNIT_ID_DELIMITER: str = " "


def _state_payload(state: str) -> tuple[str, str, str]:
    payload = STATUS_STYLES[state]
    return (payload[0], payload[1], payload[2])


def _state_payload_for_context(
    state: str,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
) -> tuple[str, str, str]:
    payload = pick_status_styles(terminal_bg_is_light, surface_hex=surface_hex)[state]
    return (payload[0], payload[1], payload[2])


def _safe_str(content: object) -> str:
    if content is None:
        return ""
    text = str(content)
    return strip_terminal_control(text)


_INTERNAL_CHANNEL_PREFIXES = _PARSER_CHANNEL_PREFIXES
_INTERNAL_CHANNEL_PREFIXES_SPACELESS = _PARSER_CHANNEL_PREFIXES_SPACELESS


def _strip_internal_channel_prefix(content: str) -> str:
    return strip_parser_channel_prefix(content)


def _normalized_event_content(event: AgentActivityEvent) -> str:
    return _strip_internal_channel_prefix(_safe_str(event.content))


class EventRenderer(Protocol):
    def __call__(
        self,
        event: AgentActivityEvent,
        ctx: DisplayContext | None = None,
        *,
        unit_id: str | None = None,
        escape_body: bool = True,
    ) -> Text: ...


_FENCED_BLOCK_RE = re.compile(
    r"(?P<open>^[ \t]*```[^\n]*\n)(?P<code>.*?)(?P<close>^[ \t]*```[ \t]*$)",
    re.MULTILINE | re.DOTALL,
)


def _stylize_fenced_code(
    text: Text,
    body_start: int,
    body: str,
    *,
    language: str,
    ctx: DisplayContext | None,
) -> None:
    match = _FENCED_BLOCK_RE.search(body)
    if match is None:
        return
    code = match.group("code")
    if not code:
        return
    spans = highlight_code_spans(
        code,
        language,
        terminal_bg_is_light=ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    code_start = body_start + match.start("code")
    for start, end, style in spans:
        text.stylize(style, code_start + start, code_start + end)


def _format_body_with_unit(body: str, unit_id: str | None) -> str:
    if not unit_id:
        return body
    return f"{unit_id} {body}"


def _identity_style_for(
    unit_id: str | None,
    *,
    active: Iterable[str] | None = None,
    ctx: DisplayContext | None = None,
) -> str:
    if not unit_id:
        return ""
    terminal_bg_is_light = ctx.terminal_background_is_light if ctx is not None else None
    surface_hex = ctx.terminal_background_hex if ctx is not None else None
    active_names = active if active is not None else (*_DISPLAY_IDENTITY_ACTIVE_SET, unit_id)
    return identity_color(
        unit_id,
        active=active_names,
        terminal_bg_is_light=terminal_bg_is_light,
        surface_hex=surface_hex,
    )


def _split_body_with_unit(body: str, unit_id: str | None) -> tuple[str, str]:
    if not unit_id or not body:
        return "", body
    prefix = f"{unit_id} "
    if body.startswith(prefix):
        return prefix, body[len(prefix) :]
    if body == unit_id:
        return unit_id, ""
    return "", body


def _append_body_with_unit(
    text: Text,
    body: str,
    unit_id: str | None,
    body_style: str,
    *,
    ctx: DisplayContext | None = None,
    escape_body: bool = True,
) -> None:
    prefix, rest = _split_body_with_unit(body, unit_id)
    if prefix:
        prefix_rendered = escape(prefix) if escape_body else prefix
        text.append(prefix_rendered, style=_identity_style_for(unit_id, ctx=ctx))
    if rest:
        rest_rendered = escape(rest) if escape_body else rest
        text.append(rest_rendered, style=body_style)


def _has_explicit_unit_prefix(body: str, unit_id: str) -> bool:
    if not unit_id or not body:
        return False
    prefix = f"{unit_id} "
    return body.startswith(prefix) or body == unit_id


def _tool_name_for_result(event: AgentActivityEvent) -> str:
    metadata = event.metadata or {}
    for key in ("tool_name", "name", "tool"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return friendly_tool_name(value)
    return ""


_DEFAULT_PLAIN_MAX_CHARS: int = 200


def _render_text_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style_name = "info"
    if event.kind is ActivityEventKind.THINKING:
        style_name = "running"
    style, icon, label = _state_payload_for_context(
        style_name,
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    body_start = len(text.plain)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    metadata = event.metadata or {}
    language = metadata.get("language")
    if metadata.get("syntax_highlight") is True and isinstance(language, str) and language:
        _stylize_fenced_code(text, body_start, body, language=language, ctx=ctx)
    return text


def _render_status_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "info",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    return text


_TOOL_PAIR_MARKER: str = "\u21b3"

_TOOL_RESULT_INDENT: str = _TOOL_PAIR_MARKER + " "


def _render_tool_use_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "running",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    raw_name = _normalized_event_content(event) or "tool"
    tool_name = friendly_tool_name(raw_name)
    args_str = _format_event_input(event.metadata)
    body_segments: list[str] = [tool_name]
    if args_str:
        body_segments.append(args_str)
    call_id = tool_call_id(event.metadata)
    if call_id:
        body_segments.append(f"call_id={call_id}")
    body = " ".join(body_segments)
    text = Text()
    text.append(f"{icon} {label} {_TOOL_PAIR_MARKER} ", style=style)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    return text


def _append_tool_result_body(
    text: Text,
    body: str,
    unit_id: str | None,
    body_style: str,
    tool_name: str,
    ctx: DisplayContext | None,
    escape_body: bool,
) -> None:
    body = strip_duplicate_tool_prefix(body, tool_name)
    _append_body_with_unit(text, body, unit_id, body_style, ctx=ctx, escape_body=escape_body)


def _render_tool_result_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    is_error = outcome_is_failure(event.metadata)
    state = "error" if is_error else "success"
    style, icon, label = _state_payload_for_context(
        state,
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    raw_body = _normalized_event_content(event)
    if not raw_body:
        result_meta = event.metadata.get("result")
        if isinstance(result_meta, dict):
            serialised = json.dumps(result_meta, sort_keys=True, default=str)
            raw_body = f"result={serialised}"
    body = raw_body
    tool_ref = _tool_name_for_result(event)
    metadata = event.metadata or {}
    target = metadata.get("target")
    if not isinstance(target, str) or not target:
        target = next(
            (
                f"{key}={_safe_str(value)}"
                for key in ("path", "command", "workdir")
                if isinstance(value := metadata.get(key), str) and value
            ),
            _format_event_input(metadata),
        )
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_TOOL_RESULT_INDENT, style=style)
    if tool_ref:
        text.append(
            escape(tool_ref) if escape_body else tool_ref,
            style=style,
        )
        text.append(" ", style=style)
    if target:
        text.append(escape(target) if escape_body else target, style="theme.text.muted")
        text.append(" ", style=style)
    body_style = "theme.display.agent_text" if not is_error else style
    _append_tool_result_body(text, body, unit_id, body_style, tool_ref, ctx, escape_body)
    return text


def _render_error_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "error",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _normalized_event_content(event) or "unknown error"
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    return text


def _render_lifecycle_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "info",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    if event.metadata.get("_transcript_claimed_outcome") is True:
        text.append("[transcript] ", style="theme.text.muted")
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    return text


def _render_progress_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "running",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    return text


def _render_heartbeat_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "info",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _format_body_with_unit(_normalized_event_content(event) or "alive", unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    _append_body_with_unit(
        text, body, unit_id, "theme.display.agent_text", ctx=ctx, escape_body=escape_body
    )
    return text


def _render_unknown_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    style, icon, label = _state_payload_for_context(
        "info",
        ctx.terminal_background_is_light if ctx is not None else None,
        surface_hex=ctx.terminal_background_hex if ctx is not None else None,
    )
    body = _normalized_event_content(event)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    if body:
        _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    summary = _metadata_summary(event.metadata)
    if summary:
        text.append(
            f" ({escape(summary) if escape_body else summary})",
            style="theme.text.muted",
        )
    return text


EVENT_RENDERERS: dict[ActivityEventKind, EventRenderer] = {
    ActivityEventKind.TEXT: _render_text_event,
    ActivityEventKind.THINKING: _render_text_event,
    ActivityEventKind.STATUS: _render_status_event,
    ActivityEventKind.TOOL_USE: _render_tool_use_event,
    ActivityEventKind.TOOL_RESULT: _render_tool_result_event,
    ActivityEventKind.ERROR: _render_error_event,
    ActivityEventKind.LIFECYCLE: _render_lifecycle_event,
    ActivityEventKind.HEARTBEAT: _render_heartbeat_event,
    ActivityEventKind.PROGRESS: _render_progress_event,
    ActivityEventKind.SUBAGENT_PROGRESS: _render_progress_event,
    ActivityEventKind.UNKNOWN: _render_unknown_event,
}


def render_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    active_identities: Iterable[str] | None = None,
    escape_body: bool = True,
) -> Text:
    renderer = EVENT_RENDERERS.get(event.kind, _render_unknown_event)
    rendered = renderer(event, ctx, unit_id=unit_id, escape_body=escape_body)
    if unit_id and active_identities is not None:
        start = rendered.plain.find(unit_id)
        if start >= 0:
            rendered.stylize(
                _identity_style_for(unit_id, active=active_identities, ctx=ctx),
                start,
                start + len(unit_id),
            )
    return rendered


def render_event_kind_text(
    kind: ActivityEventKind,
    content: str,
    *,
    timestamp: str | None = None,
    metadata: dict[str, object] | None = None,
    agent_name: str | None = None,
    max_chars: int = 200,
) -> str:
    event = _build_plain_event(
        kind,
        content,
        timestamp=timestamp,
        metadata=metadata,
        source=agent_name,
    )
    text = render_event(event, ctx=None, unit_id=agent_name, escape_body=False)
    plain = text.plain
    return _truncate_to_cells(plain, max_chars)


def _build_plain_event(
    kind: ActivityEventKind,
    content: str,
    *,
    timestamp: str | None,
    metadata: dict[str, object] | None,
    source: str | None,
) -> AgentActivityEvent:
    return make_event_for_emit(
        kind,
        content,
        timestamp=timestamp,
        metadata=metadata,
        source=source,
    )


def make_event_for_emit(
    kind: ActivityEventKind,
    content: str | None,
    *,
    timestamp: str | None = None,
    metadata: dict[str, object] | None = None,
    source: str | None = None,
) -> AgentActivityEvent:
    return AgentActivityEvent(
        provider=ActivityProvider.UNKNOWN,
        kind=kind,
        content=content or "",
        metadata=metadata or {},
        source=source or "",
        sequence=0,
        timestamp=timestamp or datetime.now(UTC).isoformat(),
    )


_METADATA_SUMMARY_MAX_PARTS: int = 3

_METADATA_SUMMARY_MAX_CHARS: int = 120

_METADATA_SUMMARY_PREFERRED_KEYS: tuple[str, ...] = (
    "status",
    "summary",
    "phase",
    "decision",
    "message",
    "event",
    "tool",
    "path",
    "workdir",
    "command",
)


def _metadata_summary(metadata: dict[str, object] | None) -> str:
    if not metadata:
        return ""
    parts: list[str] = []
    for key in _METADATA_SUMMARY_PREFERRED_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            parts.append(f"{key}={value}")
            if len(parts) >= _METADATA_SUMMARY_MAX_PARTS:
                break
    if not parts:
        return ""
    joined = ", ".join(parts)
    return _truncate_to_cells(joined, _METADATA_SUMMARY_MAX_CHARS)


def _format_event_input(metadata: dict[str, object]) -> str:
    input_obj = metadata.get("input", metadata.get("args"))
    return format_tool_input(input_obj)


def _metadata_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _truncate_to_cells(content: str, max_cells: int = 200) -> str:
    from rich.cells import cell_len

    if cell_len(content) <= max_cells:
        return content
    truncated: list[str] = []
    used = 0
    for char in content:
        char_cells = cell_len(char)
        if used + char_cells > max_cells:
            break
        truncated.append(char)
        used += char_cells
    return "".join(truncated) + "…"


def _format_timestamp(iso_ts: str | None) -> str:
    raw = iso_ts or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.strftime("%H:%M:%S")


def normalize_event_from_agent_output_line(
    line: AgentOutputLine,
    *,
    provider: ActivityProvider,
    unit_id: str = "",
    source_kind: ActivityEventKind | None = None,
) -> AgentActivityEvent:
    from typing import cast

    from ralph.display.event_options import EventOptions

    kind = source_kind or map_parser_type_to_kind(line.type)
    return make_event(
        provider=provider,
        kind=kind,
        options=cast(
            "EventOptions | None",
            EventOptions(
                content=line.content,
                metadata=line.metadata or {},
                source=unit_id,
            ),
        ),
    )
