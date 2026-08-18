"""Single registry-driven renderer for agent-output events.

All agent-output rendering flows through this module's single registry.

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


# --- Type-ignore-policy: no Any in production code; the Protocol is strict. ---

_STYLE_KEY = "style"
_ICON_KEY = "icon"
_LABEL_KEY = "label"


_UNIT_ID_DELIMITER: str = " "


def _state_payload(state: str) -> tuple[str, str, str]:
    """Return the ``(style, icon, label)`` triple for a status key."""
    payload = STATUS_STYLES[state]
    return (payload[0], payload[1], payload[2])


def _state_payload_for_context(
    state: str,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
) -> tuple[str, str, str]:
    """Resolve a semantic state through the caller's resolved terminal palette."""
    payload = pick_status_styles(terminal_bg_is_light, surface_hex=surface_hex)[state]
    return (payload[0], payload[1], payload[2])


def _safe_str(content: object) -> str:
    """Return ``content`` as a string, stripped of terminal control sequences."""
    if content is None:
        return ""
    text = str(content)
    return strip_terminal_control(text)


#: Parser-channel prefixes that must NEVER reach an operator-facing
#: surface. The four documented kinds in their short form, with the
#: trailing colon and a single separating space. Agents that emit
#: structured output sometimes leak these prefixes into the body
#: (e.g. ``"text: internal prefix payload"``); the registry strips
#: them at the canonical event-content normalization seam so the
#: severity word, tool name, and outcome carry the information
#: instead (DA-001 / AC-02).
#:
#: wt-028-display S-5: the stripper and prefix lists now live in a
#: single leaf module (:mod:`ralph.display._channel_prefix_stripper`)
#: so the live log and the rendered record cannot drift. This
#: module re-exports the public helper for backwards compatibility
#: with the existing call sites; the canonical definition lives
#: in the leaf module.
_INTERNAL_CHANNEL_PREFIXES = _PARSER_CHANNEL_PREFIXES
_INTERNAL_CHANNEL_PREFIXES_SPACELESS = _PARSER_CHANNEL_PREFIXES_SPACELESS


def _strip_internal_channel_prefix(content: str) -> str:
    """Remove a leading parser-channel prefix from ``content``.

    Thin compatibility wrapper over
    :func:`ralph.display._channel_prefix_stripper.strip_parser_channel_prefix`.
    wt-028-display S-5 consolidated the stripper into a single
    leaf module so the live log and the rendered record cannot
    drift; this alias exists only so existing call sites in this
    module still typecheck.
    """
    return strip_parser_channel_prefix(content)


def _normalized_event_content(event: AgentActivityEvent) -> str:
    """Return ``event.content`` ready for body rendering.

    DA-001 (wt-028-display S-3 / AC-02): applies
    :func:`_safe_str` (control-character sanitization) followed by
    :func:`_strip_internal_channel_prefix` (parser-channel prefix
    stripping). This is the SINGLE canonical seam where agent-body
    content is normalized before reaching any surface -- the live
    log, the rendered record, and the plain-text shim all inherit
    the normalization here so a parser that leaks
    ``"text: ..."`` / ``"thinking: ..."`` / ``"tool_use: ..."`` /
    ``"tool_result: ..."`` into the body cannot leak those tokens
    to the operator.
    """
    return _strip_internal_channel_prefix(_safe_str(event.content))


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


#: Regex matching the first markdown fenced code block in a text-event
#: body. Group 1 is the fence opener line, group 2 the fenced code, group
#: 3 the closing fence line. Used only when the parser annotated the event
#: with ``syntax_highlight``/``language`` metadata; the regex itself does
#: no language detection (the parser owns that contract).
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
    """Apply lexer-derived spans to the fenced code region of ``text``.

    Transport-neutral AC-02 seam: a parser (AGY today, any future agent
    whose parser sets the same annotation) marks a text event with
    ``syntax_highlight: True`` and a canonical Pygments ``language``;
    this helper re-locates the fenced code inside the ALREADY-APPENDED
    body and overlays themed token styles on that region only. The plain
    content is never altered (``.plain`` stays byte-identical) and any
    resolution failure leaves the flat body style untouched.
    """
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
    """Prefix ``body`` with the unit identity when ``unit_id`` is set.

    Used by every per-kind renderer so the per-unit identity threads
    into the visible body, matching the legacy plain-text path's
    ``agent_name`` prefix contract (which existing tests rely on).
    """
    if not unit_id:
        return body
    return f"{unit_id} {body}"


def _identity_style_for(
    unit_id: str | None,
    *,
    active: Iterable[str] | None = None,
    ctx: DisplayContext | None = None,
) -> str:
    """Return the Rich style string for a unit identity prefix.

    Resolves the deterministic, accessible identity color from
    :func:`ralph.display.theme.identity_color` and returns a hex
    style the renderer can pass to ``Text.append``. The name label
    is always preserved (color only assists recognition), so a
    grayscale / colorblind operator still reads the bare name.

    When ``active`` is supplied (any iterable of identity names
    currently rendered on the same surface), the collision-aware
    palette slot is picked so two simultaneously-rendered identities
    can never share a confusable color under any of the three
    documented CVD simulations. AC-15 (wt-028-display P3).

    Returns ``""`` (no override) when ``unit_id`` is empty so the
    caller's default body style wins.
    """
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
    """Split ``body`` into ``(unit_prefix, body_without_prefix)``.

    Returns ``("", body)`` when no ``unit_id`` is set or when the
    body does not start with the canonical ``"{unit_id} "`` prefix
    (so the caller can avoid colouring a body that was already
    prefixed upstream -- see
    :func:`_has_explicit_unit_prefix`).
    """
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
    """Append ``body`` to ``text`` with the unit prefix colored distinctly.

    Splits the body into its unit-prefix portion (colored with the
    identity color from :func:`_identity_style_for`) and the
    remainder (colored with ``body_style``). The name label is
    always preserved in the prefix; color only assists recognition
    so grayscale / colourblind operators keep the bare name.
    """
    prefix, rest = _split_body_with_unit(body, unit_id)
    if prefix:
        prefix_rendered = escape(prefix) if escape_body else prefix
        text.append(prefix_rendered, style=_identity_style_for(unit_id, ctx=ctx))
    if rest:
        rest_rendered = escape(rest) if escape_body else rest
        text.append(rest_rendered, style=body_style)


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
    """Return the friendly tool name attached to a TOOL_RESULT, if known."""
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

    DA-001 (wt-028-display P1 / AC-21): the timestamp is intentionally
    NOT rendered here. The chrome prefix the caller assembles in
    :class:`ParallelDisplay.emit_activity_line` already stamps every
    emitted line with ``hh:mm:ss``; rendering the timestamp in the
    body too would duplicate it on both the live log and the
    rendered record (the defect the analysis feedback flagged).
    The icon + label redundant prefix is preserved so the meaning
    survives when color is disabled (AC-10), and the body carries
    the unit identity so the plain-text path's
    ``"{unit_id} {content}"`` grep contract is held.
    """
    style_name = "info"
    if event.kind is ActivityEventKind.THINKING:
        style_name = "running"
    style, icon, label = _state_payload_for_context(
        style_name, ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    body_start = len(text.plain)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
    # AC-02 (plan S-3): a parser-annotated fenced-code text event renders
    # its code region with REAL lexer-derived token styles, not just
    # parser metadata. Transport-neutral: any agent whose parser sets the
    # ``syntax_highlight``/``language`` annotation gets identical styling.
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
    """Render a status / progress / heartbeat event.

    DA-001 (wt-028-display P1 / AC-21): the timestamp is intentionally
    NOT rendered here -- the chrome prefix the caller assembles in
    :class:`ParallelDisplay.emit_activity_line` already stamps every
    emitted line with ``hh:mm:ss``; rendering it in the body too
    duplicates the timestamp on the live log and the rendered record.
    Status, progress, subagent_progress, and heartbeat all render
    identically: an icon + label prefix (state-driven) and the
    message. Heartbeat uses the ``info`` carrier; progress uses
    ``running``; subagent_progress uses ``info``.
    """
    style, icon, label = _state_payload_for_context(
        "info", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
    )
    body = _format_body_with_unit(_normalized_event_content(event), unit_id)
    text = Text()
    text.append(f"{icon} {label} ", style=style)
    _append_body_with_unit(text, body, unit_id, style, ctx=ctx, escape_body=escape_body)
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

#: Correlation prefix for the TOOL_RESULT body so the result visually nests
#: under its TOOL_USE call. The state-label carrier already supplies the
#: separator, keeping the plain-text form stable as ``✓ PASS ↳`` for log
#: consumers and grep-based pair recovery.
_TOOL_RESULT_INDENT: str = _TOOL_PAIR_MARKER + " "


def _render_tool_use_event(
    event: AgentActivityEvent,
    ctx: DisplayContext | None = None,
    *,
    unit_id: str | None = None,
    escape_body: bool = True,
) -> Text:
    """Render a tool call.

    DA-001 (wt-028-display P1 / AC-21): the timestamp is intentionally
    NOT rendered here -- the chrome prefix the caller assembles in
    :class:`ParallelDisplay.emit_activity_line` already stamps every
    emitted line with ``hh:mm:ss``; rendering it in the body too
    duplicates the timestamp on the live log and the rendered record
    (the defect the analysis feedback flagged). Identity is carried
    by the caller's ``[call][unit]`` chrome, never repeated in the body.

    Layout: ``<icon><label> <friendly-tool-name> (<args>)``.

    The friendly tool name (e.g. ``mcp__ralph__read_file`` ->
    ``ralph.read_file``) and the formatted input come from
    :mod:`ralph.display.tool_args` so the agent-specific quirks are
    removed BEFORE rendering. State carried as ``running`` (the tool
    call is in flight). The line ends with the stable
    :data:`_TOOL_PAIR_MARKER` so the operator can pair this line
    with its follow-up TOOL_RESULT in grep / scrollback.

    When ``unit_id`` is set the caller's badge carries it once; the
    body remains tool name plus arguments so all consumers share one shape.
    """
    style, icon, label = _state_payload_for_context(
        "running", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    """Append a result body, deduplicating a leading ``tool_name`` echo (B1)."""
    body = strip_duplicate_tool_prefix(body, tool_name)
    _append_body_with_unit(text, body, unit_id, body_style, ctx=ctx, escape_body=escape_body)


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
    is_error = outcome_is_failure(event.metadata)
    state = "error" if is_error else "success"
    style, icon, label = _state_payload_for_context(
        state, ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
    )
    raw_body = _normalized_event_content(event)
    if not raw_body:
        result_meta = event.metadata.get("result")
        if isinstance(result_meta, dict):
            # Structured tool results (codex MCP tool_result, JSON Responses)
            # carry the dict in metadata rather than as a string body. Surface
            # the parsed dict so the operator can see why the call failed and
            # so downstream failure diagnostics (e.g. commit_plumbing's
            # "Agent output:" lines) preserve the structured payload.
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
    # DA-001 (wt-028-display P1 / AC-21): the timestamp is intentionally
    # NOT rendered here -- the chrome prefix the caller assembles in
    # :class:`ParallelDisplay.emit_activity_line` already stamps every
    # emitted line with ``hh:mm:ss``; rendering it in the body too
    # duplicates the timestamp on the live log and the rendered record
    # (the defect the analysis feedback flagged).
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
    """Render an error event.

    The ``error`` carrier (error style + ✗ + FAIL) is paired with the
    body so the meaning persists with color disabled.
    """
    style, icon, label = _state_payload_for_context(
        "error", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    """Render a lifecycle event (phase transitions, run start / end).

    Per C3 / DoD 14: when the lifecycle event's content is the agent's
    own transcript-claimed outcome (e.g. ``agy result SUCCESS (...turns)``),
    qualify the rendered line with a ``[transcript]`` prefix so the
    operator knows the assertion is the model's own and not Ralph's
    graded verdict. The graded verdict is rendered separately by the
    phase report path.
    """
    style, icon, label = _state_payload_for_context(
        "info", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    """Render a ``PROGRESS`` / ``SUBAGENT_PROGRESS`` event.

    Both event kinds render with the ``running`` carrier so an
    in-progress signal never accidentally reads as success/failure.
    """
    style, icon, label = _state_payload_for_context(
        "running", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    """Render a heartbeat event (idle-waitdog liveness ping)."""
    style, icon, label = _state_payload_for_context(
        "info", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    """Render an unknown / unclassified event without crashing.

    This is the registry's safety net -- a kind that escaped
    ``map_parser_type_to_kind`` (a brand-new provider feeding an
    unknown parser type) must still render something readable so the
    operator knows something happened. When the event carries
    metadata but no body (e.g. ``item_plan_result``), the metadata
    summary is rendered instead so the operator still sees the
    key=value context.
    """
    style, icon, label = _state_payload_for_context(
        "info", ctx.terminal_background_is_light if ctx is not None else None, surface_hex=ctx.terminal_background_hex if ctx is not None else None
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
    active_identities: Iterable[str] | None = None,
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
