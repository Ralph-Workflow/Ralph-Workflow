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
from ralph.display.activity_provider import ActivityProvider
from ralph.display.activity_router import map_parser_type_to_kind
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.theme import STATUS_STYLES
from ralph.display.tool_args import format_tool_input, friendly_tool_name

if TYPE_CHECKING:
    from ralph.agents.parsers.agent_output_line import AgentOutputLine
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

    ``escape_body`` controls whether the body segment is Rich-``escape()``'d
    before being appended to the returned ``Text``. The default
    (``True``) is the rich-Text path's contract: the body is printed
    through a Console with ``markup=True`` so literal ``[red]`` markers
    must be escaped. The plain-text path (:func:`render_event_kind_text`)
    passes ``False`` so the body surfaces verbatim through ``markup=False``
    consumer contexts (literal ``[result]`` content reaches the user
    unchanged).
    """

    def __call__(
        self,
        event: AgentActivityEvent,
        ctx: DisplayContext | None = None,
        *,
        unit_id: str | None = None,
        escape_body: bool = True,
    ) -> Text: ...


# --- Per-kind renderer implementations ---


def _format_body_with_unit(body: str, unit_id: str | None) -> str:
    """Prefix ``body`` with the unit identity when ``unit_id`` is set.

    Used by every per-kind renderer so the per-unit identity threads
    into the visible body, matching the legacy plain-text path's
    ``agent_name`` prefix contract (which existing tests rely on).
    """
    if not unit_id:
        return body
    return f"{unit_id} {body}"


def _has_explicit_unit_prefix(body: str, unit_id: str) -> bool:
    """Return True when ``body`` already starts with the ``unit_id`` prefix.

    Tool results sometimes arrive with the unit identity already
    baked into the body (e.g. the parser concatenates ``agent_name``
    into ``content`` upstream). The renderer's own
    :func:`_format_body_with_unit` would otherwise double-print
    ``bash bash /tmp/x``. Mirrors the legacy plain-text path's
    duplication guard.
    """
    if not unit_id or not body:
        return False
    prefix = f"{unit_id} "
    return body.startswith(prefix) or body == unit_id


def _tool_name_for_result(event: AgentActivityEvent) -> str:
    """Return the tool name to render on a TOOL_RESULT line, or ``""`` when unknown.

    The result renderer uses this to embed the friendly tool name
    so the operator can pair the result with the TOOL_USE call
    even when the two lines are separated by intervening output
    (other tools, status messages). The friendly-name normalization
    in :mod:`ralph.display.tool_args` is applied so the same
    ``mcp__ralph__read_file`` -> ``ralph.read_file`` mapping the
    TOOL_USE renderer uses is also applied here.
    """
    metadata = event.metadata or {}
    for key in ("tool_name", "name", "tool"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return friendly_tool_name(value)
    return ""


#: Bounded fallback cap for the plain-text path. The plain-text
#: renderer (:func:`render_event_kind_text`) uses
#: ``max_chars = _DEFAULT_PLAIN_MAX_CHARS`` cells by default; callers
#: pass an explicit ``max_chars`` to override. The
#: :class:`ParallelDisplay` delivery path applies its own
#: overflow-aware condenser on the FULL unabridged line emitted by
#: this renderer, so the overflow log records the complete original
#: payload (NOT a pre-truncated copy -- the regression the
#: analysis feedback flagged).
_DEFAULT_PLAIN_MAX_CHARS: int = 200


def _render_text_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a plain-text agent message.

    Carries an icon + label redundant prefix so the meaning survives
    when color is disabled (AC-10); the timestamp is muted, the body
    is the content string (sanitized; escaped when ``escape_body``
    is True so the rich Text path can safely print through a Console
    with ``markup=True``).
    """
    style_name = "info"
    if event.kind is ActivityEventKind.THINKING:
        style_name = "running"
    style, icon, label = _state_payload(style_name)
    body = _format_body_with_unit(_safe_str(event.content), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(escape(body) if escape_body else body, style=DEFAULT_STYLE)
    return text


def _render_status_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a status / progress / heartbeat event.

    Status, progress, subagent_progress, and heartbeat all render
    identically: an icon + label prefix (state-driven), a muted
    timestamp, and the message. Heartbeat uses the ``info`` carrier;
    progress uses ``running``; subagent_progress uses ``info``.
    """
    style, icon, label = _state_payload("info")
    body = _format_body_with_unit(_safe_str(event.content), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(escape(body) if escape_body else body, style=DEFAULT_STYLE)
    return text


#: Stable correlation marker so the operator can visually group a TOOL_USE
#: line with its TOOL_RESULT follow-up. Renders as a Unicode triangle (so
#: it is detectable on both UTF-8 terminals and ASCII fallbacks via the
#: carrier prefix the registry already prints) and survives color
#: disabling because the icon/label prefix on each line is itself the
#: non-color carrier.
#: The marker is intentionally stable (no per-event random suffix) so a
#: caller that wants to feed TOOL_USE / TOOL_RESULT pairs into a grep can
#: use a literal ``\u21b3`` to recover both lines of every tool pair in
#: order, regardless of which agent backend emitted them.
_TOOL_PAIR_MARKER: str = "\u21b3"

#: Indentation prefix for the TOOL_RESULT body so the result visually nests
#: under its TOOL_USE call. Two spaces + the correlation marker keeps the
#: group identifiable even when the icon column is stripped (e.g. by a
#: downstream ANSI / markup stripper).
_TOOL_RESULT_INDENT: str = "  " + _TOOL_PAIR_MARKER + " "


def _render_tool_use_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a tool call.

    Layout: ``<icon><label> <ts> <unit_id> <friendly-tool-name> (<args>)``.

    The friendly tool name (e.g. ``mcp__ralph__read_file`` ->
    ``ralph.read_file``) and the formatted input come from
    :mod:`ralph.display.tool_args` so the agent-specific quirks are
    removed BEFORE rendering. State carried as ``running`` (the tool
    call is in flight). The line carries the same timestamp cue as the
    text/status paths so a tool call is identifiable in scrollback
    without the operator having to inspect the registry kind, and
    ends with the stable :data:`_TOOL_PAIR_MARKER` so the operator
    can pair this line with its follow-up TOOL_RESULT in grep /
    scrollback.

    When ``unit_id`` is set the unit prefix threads into the body so
    the plain-text path matches the legacy ``agent_name`` contract.
    """
    style, icon, label = _state_payload("running")
    raw_name = _safe_str(event.content) or "tool"
    tool_name = friendly_tool_name(raw_name)
    args_str = _format_event_input(event.metadata)
    body_segments: list[str] = []
    if unit_id:
        body_segments.append(f"{unit_id}")
    body_segments.append(tool_name)
    if args_str:
        body_segments.append(args_str)
    body = " ".join(body_segments)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(
        escape(body) if escape_body else body,
        style=style,
    )
    text.append(f" {_TOOL_PAIR_MARKER}", style=style)
    return text


def _render_tool_result_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a tool result.

    Layout:
    ``<icon><label> <ts> <unit_id> [<tool_name>] <body>``.

    Success uses the ``success`` carrier; a tool result carrying
    ``is_error`` true (or a non-empty ``error`` in metadata) flips to
    the ``error`` carrier while keeping the body content. The
    ``is_error`` check is the SAME check the registry applies, so the
    plain-text path derived via :func:`render_event_kind_text` honors
    it byte-for-byte.

    The body is rendered UNABRIDGED. Condensation is a delivery concern
    handled by the caller's :class:`RawOverflowLog` + condenser path --
    NOT a presentation concern of the registry. Rendering the full
    content here is required so the caller's overflow-aware condenser
    sees the complete original payload and the overflow log records
    the full unabridged line (otherwise the deliverable silently loses
    data -- a 1000-char tool result would land in the overflow log as
    ~400 chars, truncating the audit trail). Plain-text consumers that
    want a bounded line apply their own cell-aware
    :func:`_truncate_to_cells` cap (see :func:`render_event_kind_text`).

    The line opens with the same icon + label carrier as the
    ``TOOL_USE`` renderer, carries the same timestamp cue, and
    prepends the result body with the :data:`_TOOL_RESULT_INDENT`
    group marker so the result visually nests under its paired
    tool call (AC-05 / grouping).
    """
    is_error = _metadata_truthy(event.metadata.get("is_error"))
    state = "error" if is_error else "success"
    style, icon, label = _state_payload(state)
    raw_body = _safe_str(event.content)
    if unit_id and not _has_explicit_unit_prefix(raw_body, unit_id):
        body = _format_body_with_unit(raw_body, unit_id)
    else:
        body = raw_body
    tool_ref = _tool_name_for_result(event)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(_format_timestamp(event.timestamp), style="theme.text.muted")
    text.append(" ", style="theme.text.muted")
    text.append(_TOOL_RESULT_INDENT, style=style)
    if tool_ref:
        text.append(
            escape(tool_ref) if escape_body else tool_ref,
            style=style,
        )
        text.append(" ", style=style)
    text.append(
        escape(body) if escape_body else body,
        style="theme.text.muted" if not is_error else style,
    )
    return text


def _render_error_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render an error event.

    The ``error`` carrier (VERMILLION + ✗ + FAIL) is paired with the
    body so the meaning persists with color disabled.
    """
    style, icon, label = _state_payload("error")
    body = _format_body_with_unit(_safe_str(event.content) or "unknown error", unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body) if escape_body else body, style=style)
    return text


def _render_lifecycle_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a lifecycle event (phase transitions, run start / end)."""
    style, icon, label = _state_payload("info")
    body = _format_body_with_unit(_safe_str(event.content), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body) if escape_body else body, style=DEFAULT_STYLE)
    return text


def _render_progress_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a ``PROGRESS`` / ``SUBAGENT_PROGRESS`` event.

    Both event kinds render with the ``running`` carrier so an
    in-progress signal never accidentally reads as success/failure.
    """
    style, icon, label = _state_payload("running")
    body = _format_body_with_unit(_safe_str(event.content), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body) if escape_body else body, style=DEFAULT_STYLE)
    return text


def _render_heartbeat_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a heartbeat event (idle-waitdog liveness ping)."""
    style, icon, label = _state_payload("info")
    body = _format_body_with_unit(_safe_str(event.content) or "alive", unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    text.append(escape(body) if escape_body else body, style="theme.text.muted")
    return text


def _render_unknown_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
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
    body = _format_body_with_unit(_safe_str(event.content), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    if body:
        text.append(escape(body) if escape_body else body, style=DEFAULT_STYLE)
    summary = _metadata_summary(event.metadata)
    if summary:
        text.append(
            f" ({escape(summary) if escape_body else summary})",
            style="theme.text.muted",
        )
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
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render ``event`` via the registry into a rich ``Text``.

    This is the single public surface for agent-event rendering. All
    three former renderers (activity_stream._render_agent_activity_line,
    activity_model.render_event_line, parallel_display._emit_activity_event)
    delegate to this function.

    Args:
        event: The canonical agent event to render.
        ctx: Display context providing theme / glyphs / width. The
            canonical renderers do not currently consume ``ctx`` (they
            read ``STATUS_STYLES`` directly) but the parameter is part
            of the stable contract so future renderers can pick it up
            without breaking call sites.
        unit_id: Optional unit identifier; threads into the rendered
            line so the per-unit identity surfaces in both the rich-Text
            and the plain-text paths.
        escape_body: When ``True`` (default) the body segment is
            Rich-``escape()``'d before being appended. The plain-text
            path (:func:`render_event_kind_text`) passes ``False`` so
            literal content reaches the consumer unchanged.

    Returns:
        A :class:`rich.text.Text` instance whose plain string carries
        a non-color redundancy (icon + ASCII label) for every kind.
    """
    renderer = EVENT_RENDERERS.get(event.kind, _render_unknown_event)
    return renderer(event, ctx, unit_id=unit_id, escape_body=escape_body)


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
    path whose consumers don't carry a Console, and the
    :func:`ralph.pipeline.activity_stream._render_agent_activity_line`
    pipeline-runner shim, plus tests that want to assert on a stable
    plain-text line). After the wt-028-display consolidation, this
    function is a thin adapter over the canonical
    :func:`render_event` registry: it builds the same
    ``AgentActivityEvent`` the registry expects, calls the registry
    with ``escape_body=False`` (so literal ``[result]`` content
    reaches the plain-text consumer unchanged -- :data:`escape()` is
    only needed when the Text will be printed through a Console with
    ``markup=True``), then extracts ``text.plain`` and applies
    cell-aware truncation. The icon + ASCII label + state carrier
    all flow from :data:`ralph.display.theme.STATUS_STYLES` via the
    registry, so the plain-text path cannot drift from the rich-Text
    path. The ``agent_name`` prefix threads through the registry's
    ``unit_id`` parameter so legacy tests asserting ``bash`` /
    ``command=pytest -q`` / ``workdir=/tmp/project`` substrings
    continue to pass through the registry's single source of
    formatting.

    A ``TOOL_RESULT`` event carrying ``is_error=True`` metadata
    renders with the ``error`` carrier (e.g. ``✗ FAIL``) so an
    error never accidentally reads as success (AC-10, AC-05).
    """
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
    """Construct an ``AgentActivityEvent`` for the plain-text path.

    Normalizes the (kind, content, metadata) tuple the plain-text
    callers pass into the canonical :class:`AgentActivityEvent`
    shape the registry expects. Uses ``UNKNOWN`` as the
    ``ActivityProvider`` because the plain-text path is provider-
    agnostic: it is the canonical registry's job to keep the same
    rendered string across providers (AC-07).
    """
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
    """Construct a canonical :class:`AgentActivityEvent` from loose render args.

    Production ingestion sites that still receive loose render
    arguments (e.g. the ``_emit_activity_event`` callback in
    :mod:`ralph.display.parallel_display` and the
    ``render_event_line`` adapter in :mod:`ralph.display.activity_model`)
    call this to build the typed event BEFORE calling
    :func:`render_event` so the registry owns every rendering
    decision.

    Uses ``UNKNOWN`` as the ``ActivityProvider`` because the
    ingestion sites that hold loose args are provider-agnostic --
    agent-specific quirks have already been removed upstream by
    :func:`normalize_event_from_agent_output_line` so the registry
    renders the same line regardless of the originating provider
    (AC-07).
    """
    return AgentActivityEvent(
        provider=ActivityProvider.UNKNOWN,
        kind=kind,
        content=content or "",
        metadata=metadata or {},
        source=source or "",
        sequence=0,
        timestamp=timestamp or datetime.now(UTC).isoformat(),
    )


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


# Re-exports (keep callers stable as the legacy renders are deleted).
__all__ = [
    "EVENT_RENDERERS",
    "EventRenderer",
    "make_event_for_emit",
    "normalize_event_from_agent_output_line",
    "render_event",
    "render_event_kind_text",
]
