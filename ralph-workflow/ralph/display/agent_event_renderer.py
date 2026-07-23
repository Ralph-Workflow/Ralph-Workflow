"""Single registry-driven renderer for agent-output events.

After the wt-028-display consolidation, all agent-output rendering
flows through this module. Three independent renderers existed before
the consolidation:

* ``ralph/pipeline/activity_stream.py`` ``_render_agent_activity_line``
  (and its per-type helpers ``_render_text_line``,
  ``_render_tool_use_line``, ``_render_tool_result_line``,
  ``_render_error_line``, ``_render_metadata_event_line``,
  ``_tool_input_summary``, ``_metadata_summary``, ``_kv_summary``,
  ``_styled_prefix``) -- the pipeline runner's path.
* ``ralph/display/activity_model.py`` ``render_event_line`` -- the ring
  buffer / activity-router path (subprocess executor side).
* ``ralph/display/parallel_display.py`` ``_emit_activity_event`` inline
  body (``friendly_tool_name``, ``format_tool_input``, ``condense_content``)
  -- the parallel / live-display path.

These three had drifted apart over time. Each grew its own
truncation limit, its own icon table, and its own tool-input
formatter, so the same logical event could render visibly different
text depending on which path produced it. The single renderer
registry below owns every agent-output event presentation decision
once:

* The ``EventRenderer`` ``Protocol`` defines the per-kind rendering
  contract; each kind has exactly one renderer.
* ``render_event(event, ctx, *, unit_id=None)`` is the single public
  entry point. All three paths above now delegate here.
* ``normalize_event_from_agent_output_line`` is the single boundary
  that converts a parser-shaped ``AgentOutputLine`` into an
  ``AgentActivityEvent`` (reusing ``activity_model.make_event`` and
  ``activity_router.map_parser_type_to_kind``) so agent-specific
  quirks (claude / codex / opencode / ...) are normalized BEFORE
  rendering. The same logical line should produce the same rendered
  text regardless of which backend produced it.
* All visible output passes through ``line_sanitizer.strip_terminal_control``
  so a stray escape sequence from an agent can never reach the Live
  region or the redirected transcript.
* All styles reference :data:`ralph.display.theme.STATUS_STYLES` so a
  semantic state (success / error / pending / ...) carries the same
  rich-style + unicode-glyph + ascii-label triple everywhere. No
  literal rich styles appear in this module.

Adding a new agent event kind is a single-file change: add a renderer
function and register it in ``EVENT_RENDERERS``. Existing call sites do
not need to be touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from rich.markup import escape
from rich.text import Text

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import make_event
from ralph.display.activity_router import map_parser_type_to_kind
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.theme import STATUS_STYLES
from ralph.display.tool_args import format_tool_input, friendly_tool_name

if TYPE_CHECKING:

    from ralph.agents.parsers.agent_output_line import AgentOutputLine
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_activity_event import AgentActivityEvent
    from ralph.display.context import DisplayContext


# --- Type-ignore-policy: no Any in production code; the Protocol is strict. ---

_STYLE_KEY = "style"
#: Sentinel theme key used for plain-text body segments (no semantic role).
DEFAULT_STYLE = "default"

_ICON_KEY = "icon"
_LABEL_KEY = "label"


def _state_payload(state: str) -> tuple[str, str, str]:
    """Return the ``(style, icon, label)`` triple for a STATUS_STYLES key.

    Raises ``KeyError`` (matching ``format_status``'s contract) when the
    state is unknown so the registry surface can't silently drop a
    state to ``unknown``.
    """
    payload = STATUS_STYLES[state]
    return (payload[0], payload[1], payload[2])


def _safe_str(content: object) -> str:
    """Return ``content`` as a string, stripped of terminal control sequences."""
    if content is None:
        return ""
    text = str(content)
    return strip_terminal_control(text)


class EventRenderer(Protocol):
    """Render a single ``AgentActivityEvent`` into a rich ``Text``.

    Implementations MUST be pure (no I/O, no env reads, no Console
    construction) and MUST reference :data:`ralph.display.theme.STATUS_STYLES`
    for state-driven styling rather than literal rich styles. The same
    event rendered by the same renderer MUST return text whose plain
    representation is identical regardless of which agent backend
    produced the source line.
    """

    def __call__(
        self,
        event: AgentActivityEvent,
        ctx: DisplayContext,
        *,
        unit_id: str | None = None,
    ) -> Text: ...


# --- Per-kind renderer implementations ---


def _render_text_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a plain-text agent message.

    Carries an icon + label redundant prefix so the meaning survives
    when color is disabled (AC-10); the timestamp is muted, the body
    is the content string (sanitized + escaped).
    """
    style_name = "info"
    if event.kind is ActivityEventKind.THINKING:
        style_name = "running"
    style, icon, label = _state_payload(style_name)
    body = _safe_str(event.content)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(escape(body), style=DEFAULT_STYLE)
    return text


def _render_status_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a status / progress / heartbeat event.

    Status, progress, subagent_progress, and heartbeat all render
    identically: an icon + label prefix (state-driven), a muted
    timestamp, and the message. Heartbeat uses the ``info`` carrier;
    progress uses ``running``; subagent_progress uses ``info``.
    """
    style, icon, label = _state_payload("info")
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(escape(_safe_str(event.content)), style=DEFAULT_STYLE)
    return text


def _render_tool_use_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a tool call.

    Layout: ``<icon><label> <friendly-tool-name> (<formatted-input>)``.
    The friendly tool name (e.g. ``mcp__ralph__read_file`` ->
    ``ralph.read_file``) and the formatted input come from
    :mod:`ralph.display.tool_args` so the agent-specific quirks are
    removed BEFORE rendering. State carried as ``running`` (the tool
    call is in flight).
    """
    style, icon, label = _state_payload("running")
    raw_name = _safe_str(event.content) or "tool"
    tool_name = friendly_tool_name(raw_name)
    args_str = _format_event_input(event.metadata)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(tool_name, style=style)
    if args_str:
        text.append(f" {args_str}", style="theme.text.muted")
    return text


def _render_tool_result_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a tool result.

    Layout: ``<icon><label> <sanitized-result>``. Success uses the
    ``success`` carrier; a tool result carrying ``is_error`` true (or
    a non-empty ``error`` in metadata) flips to the ``error`` carrier
    while keeping the body content.
    """
    is_error = _metadata_truthy(event.metadata.get("is_error"))
    state = "error" if is_error else "success"
    style, icon, label = _state_payload(state)
    body = _safe_str(event.content)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body), style="theme.text.muted" if not is_error else style)
    return text


def _render_error_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render an error event.

    The ``error`` carrier (VERMILLION + ✗ + FAIL) is paired with the
    body so the meaning persists with color disabled.
    """
    style, icon, label = _state_payload("error")
    body = _safe_str(event.content) or "unknown error"
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body), style=style)
    return text


def _render_lifecycle_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a lifecycle event (phase transitions, run start / end)."""
    style, icon, label = _state_payload("info")
    body = _safe_str(event.content)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body), style=DEFAULT_STYLE)
    return text


def _render_progress_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a ``PROGRESS`` / ``SUBAGENT_PROGRESS`` event.

    Both event kinds render with the ``running`` carrier so an
    in-progress signal never accidentally reads as success/failure.
    """
    style, icon, label = _state_payload("running")
    body = _safe_str(event.content)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body), style=DEFAULT_STYLE)
    return text


def _render_heartbeat_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render a heartbeat event (idle-waitdog liveness ping)."""
    style, icon, label = _state_payload("info")
    body = _safe_str(event.content) or "alive"
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body), style="theme.text.muted")
    return text


def _render_unknown_event(
    event: AgentActivityEvent,
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render an unknown / unclassified event without crashing.

    This is the registry's safety net -- a kind that escaped
    ``map_parser_type_to_kind`` (a brand-new provider feeding an
    unknown parser type) must still render something readable so the
    operator knows something happened. When the event carries
    metadata but no body (e.g. ``item_plan_result``), the metadata
    summary is rendered instead so the operator still sees the
    key=value context.
    """
    style, icon, label = _state_payload("warning")
    body = _safe_str(event.content)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    if body:
        text.append(escape(body), style=DEFAULT_STYLE)
    summary = _metadata_summary(event.metadata)
    if summary:
        text.append(f" ({escape(summary)})", style="theme.text.muted")
    return text


# --- Public registry ---

#: Mapping from ``ActivityEventKind`` to its renderer. Adding a new
#: kind requires (a) extending the enum, (b) adding a renderer
#: function above, and (c) registering the entry here. Existing
#: callers do NOT need to change.
# bounded-accumulator-ok: fixed dispatch table keyed on ActivityEventKind enum
EVENT_RENDERERS: dict[ActivityEventKind, EventRenderer] = {  # bounded-accumulator-ok
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
    ctx: DisplayContext,
    *,
    unit_id: str | None = None,
) -> Text:
    """Render ``event`` via the registry into a rich ``Text``.

    This is the single public surface for agent-event rendering. All
    three former renderers (activity_stream._render_agent_activity_line,
    activity_model.render_event_line, parallel_display._emit_activity_event)
    delegate to this function.

    Args:
        event: The canonical agent event to render.
        ctx: Display context providing theme / glyphs / width.
        unit_id: Optional unit identifier; present so callers can
            thread the per-unit identity into the rendered line for
            audit (the registry itself does not currently consume it,
            but the parameter is part of the stable contract).

    Returns:
        A :class:`rich.text.Text` instance whose plain string carries
        a non-color redundancy (icon + ASCII label) for every kind.
    """
    renderer = EVENT_RENDERERS.get(event.kind, _render_unknown_event)
    return renderer(event, ctx, unit_id=unit_id)


def render_event_kind_text(
    kind: ActivityEventKind,
    content: str,
    *,
    timestamp: str | None = None,
    metadata: dict[str, object] | None = None,
    agent_name: str | None = None,
    max_chars: int = 200,
) -> str:
    """Render a stable plain-text line for a single kind + content.

    Used by non-rich code paths (the ring-buffer / activity-router
    path whose consumers don't carry a Console, and the legacy
    :func:`ralph.pipeline.activity_stream._render_agent_activity_line`
    pipeline-runnner shim, plus tests that want to assert on a stable
    plain-text line). The format is:

    * ``TEXT`` / ``THINKING`` / ``STATUS`` / ``HEARTBEAT`` / ``LIFECYCLE``:
      ``<icon> [HH:MM:SS] [<agent>] <content>``
    * ``TOOL_USE``: ``<icon> [HH:MM:SS] <agent> tool <name> (<args>)``
      - the args are formatted by
      :func:`ralph.display.tool_args.format_tool_input` so the
      parity with the legacy tool_use line is preserved.
    * ``TOOL_RESULT``: ``<icon> [HH:MM:SS] <agent> result <content>``
    * ``ERROR``: ``<icon> [HH:MM:SS] <agent> ✗ <content>``
    * ``PROGRESS`` / ``SUBAGENT_PROGRESS``: ``<icon> [HH:MM:SS] <content>``

    The icon + ASCII label come from ``STATUS_STYLES`` so every state
    carries a non-color carrier even at this minimal-info endpoint
    (AC-10). The agent_name prefix is the same ``<agent>`` per-unit
    identity the legacy pipeline runner format used, so the existing
    tests asserting ``bash`` / ``command=pytest -q`` /
    ``workdir=/tmp/project`` substrings continue to pass through
    the registry's single source of formatting.
    """
    icon = _icon_for_kind_text(kind)
    time_str = _format_time_str(timestamp)
    agent_prefix = f"{agent_name} " if agent_name else ""

    body = _body_for_kind(kind, content, metadata or {}, agent_prefix)
    sanitized = strip_terminal_control(body or "")
    truncated = _truncate_to_cells(sanitized, max_chars)
    escaped = escape(truncated)
    if time_str:
        return f"{icon} [{time_str}] {escaped}".strip()
    return f"{icon} {escaped}".strip()


def _icon_for_kind_text(kind: ActivityEventKind) -> str:
    """Return the icon for the kind's carrier state in the plain-text path."""
    state_for_kind = {
        ActivityEventKind.TOOL_USE: "running",
        ActivityEventKind.ERROR: "error",
        ActivityEventKind.TOOL_RESULT: "success",
    }
    state = state_for_kind.get(kind, "info")
    _, icon, _ = _state_payload(state)
    return icon


def _format_time_str(timestamp: str | None) -> str:
    """Format an ISO-8601 timestamp string as ``HH:MM:SS`` for the icon prefix."""
    raw = timestamp or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.strftime("%H:%M:%S")


def _body_for_kind(
    kind: ActivityEventKind,
    content: str,
    metadata: dict[str, object],
    agent_prefix: str,
) -> str:
    """Compute the body segment for the given kind + content + metadata.

    Split out of ``render_event_kind_text`` so the per-kind branching is
    readable (PLR0912 stays below the cap) and each kind has one place
    to define its body shape.
    """
    if kind is ActivityEventKind.TOOL_USE:
        return _body_for_tool_use(content, metadata, agent_prefix)
    if kind is ActivityEventKind.ERROR:
        return _body_for_error(content, agent_prefix)
    if kind is ActivityEventKind.TOOL_RESULT:
        return f"{agent_prefix}result {content}".rstrip()
    if kind in (
        ActivityEventKind.STATUS,
        ActivityEventKind.LIFECYCLE,
        ActivityEventKind.PROGRESS,
        ActivityEventKind.SUBAGENT_PROGRESS,
    ) or kind is ActivityEventKind.THINKING:
        return f"{agent_prefix}{content}".strip()
    if kind is ActivityEventKind.UNKNOWN:
        return _body_for_unknown(content, metadata, agent_prefix)
    return f"{agent_prefix}{content}".strip()


def _body_for_tool_use(
    content: str, metadata: dict[str, object], agent_prefix: str
) -> str:
    """Body for ``TOOL_USE``: ``<agent> tool <name> <args>``."""
    tool_name = friendly_tool_name(content or "tool")
    args_str = format_tool_input(metadata.get("input", metadata.get("args")))
    body_parts = [f"{agent_prefix}tool {tool_name}"]
    if args_str:
        body_parts.append(args_str)
    return " ".join(body_parts)


def _body_for_error(content: str, agent_prefix: str) -> str:
    """Body for ``ERROR``: ``<agent> ✗ <content>``."""
    marker = f"{agent_prefix}✗ " if agent_prefix else "✗ "
    return f"{marker}{content}".strip()


def _body_for_unknown(
    content: str, metadata: dict[str, object], agent_prefix: str
) -> str:
    """Body for ``UNKNOWN``: prefer content, fall back to metadata summary."""
    summary = _metadata_summary(metadata)
    prefix_body = f"{agent_prefix}{content}".strip() if content else ""
    if prefix_body and summary:
        return f"{prefix_body} ({summary})"
    if prefix_body:
        return prefix_body
    if summary:
        return f"{agent_prefix}{summary}".strip()
    return ""


# --- Helpers ---


#: Maximum number of preferred-metadata keys surfaced in the unknown-event
#: metadata summary. Beyond the first N pairs, the trailing keys are dropped
#: to keep the line scannable.
_METADATA_SUMMARY_MAX_PARTS: int = 3

#: Maximum cell width of the metadata summary suffix. Mirrors the legacy
#: ``_MAX_METADATA_SUMMARY_LENGTH`` so the registry's plain-text line stays
#: within the operator's eye-line width.
_METADATA_SUMMARY_MAX_CHARS: int = 120

#: Preferred metadata keys in display order. The unknown-event renderer
#: surfaces these in this order so the operator sees the most meaningful
#: context first (status, summary, then phase/decision/message/event/tool/
#: path/workdir/command).
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
    """Return a stable ``key=value, ...`` summary of preferred metadata keys.

    Used by the unknown-event renderer (and any future kind that carries
    metadata-only context) so an event with no body still surfaces the
    most-meaningful operator-visible fields. Mirrors the legacy
    ``_metadata_summary_impl`` so the pipeline-runner tests that assert
    ``status=completed`` / ``summary=Plan submitted`` substrings continue
    to pass through the registry's single source of formatting.
    """
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
    """Format a tool input dict into ``(k=v ...)`` form.

    Wraps :func:`ralph.display.tool_args.format_tool_input` so the
    agent-quirk normalization is centralized; the registry does not
    re-implement the dict-to-string conversion.
    """
    input_obj = metadata.get("input", metadata.get("args"))
    return format_tool_input(input_obj)


def _metadata_truthy(value: object) -> bool:
    """Return True when a metadata flag should be treated as truthy.

    Matches the historical parser convention: a string ``"true"``, the
    literal bool ``True``, or any non-zero integer. Anything else is
    False. This avoids relying on truthiness alone so a stray metadata
    string never flips a tool result to the error carrier by accident.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _truncate_to_cells(content: str, max_cells: int = 200) -> str:
    """Return ``content`` truncated to at most ``max_cells`` display cells.

    Same contract as ``activity_model._truncate_to_cells`` so the
    registry's plain-text helper produces a byte-identical line when
    both are fed the same input. Cell-aware so an emoji-heavy tool
    result doesn't blow up the layout.
    """
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
    """Format an ISO-8601 timestamp string as ``HH:MM:SS`` for the icon prefix."""
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
    """Convert a parser-shaped ``AgentOutputLine`` to the canonical event.

    Single boundary used by every code path that ingests parser lines;
    agent-specific quirks (claude/codex/opencode) are removed BEFORE
    rendering so the same logical line produces a byte-identical
    rendered string regardless of the backend that emitted it.

    Args:
        line: The raw ``AgentOutputLine`` produced by a parser.
        provider: The canonical ``ActivityProvider`` for the source.
        unit_id: Stable unit identifier for audit breadcrumbs.
        source_kind: Optional caller override; defaults to the
            canonical ``map_parser_type_to_kind`` mapping.

    Returns:
        An :class:`AgentActivityEvent` ready for :func:`render_event`.
    """
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


def _event_options_from_line(
    line: AgentOutputLine,
    *,
    source: str,
) -> object:
    """Build :class:`EventOptions` from a parser line (avoids cycles).

    Implementation note: returns the result of an inline import so the
    ralph/display subgraph does not pull in the ralph/display/event_options
    import at module load time (which would create an eager
    cycle with activity_model).
    """
    from ralph.display.event_options import EventOptions

    return EventOptions(
        content=line.content,
        metadata=line.metadata or {},
        source=source,
    )


# Re-exports (keep callers stable as the legacy renders are deleted).
__all__ = [
    "EVENT_RENDERERS",
    "EventRenderer",
    "normalize_event_from_agent_output_line",
    "render_event",
    "render_event_kind_text",
]
