"""Canonical :class:`PresentedEntry` dataclass and its builder.

Split from :mod:`ralph.display.agent_event_renderer` so the
renderer registry file stays under the 1000-line audit cap. The
:class:`PresentedEntry` is the structured intermediate that both
consumers (live display and text-first record writer) feed off
of; separating it from the rendering helpers keeps the contract
surface small and reviewable.

P1 (wt-028-display): one event, one entry, one identity, one
timestamp -- the canonical seam that makes "one presentation,
one vocabulary" structurally enforced rather than remembered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.display._channel_prefix_stripper import (
    strip_parser_channel_prefix,
)
from ralph.display._tool_correlation import tool_call_id
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.tool_args import friendly_tool_name

if TYPE_CHECKING:
    from ralph.display.agent_activity_event import AgentActivityEvent


@dataclass(frozen=True)
class PresentedEntry:
    """Canonical structured output of the agent-event renderer registry.

    S-10 (wt-028-display P1): every event the renderer registry
    produces yields exactly one :class:`PresentedEntry`. Two
    consumers feed off the same stream:

    * the live display (Rich color + glyphs), via ``render_to_text``;
    * the text-first rendered record (plain, greppable, stable
      field order), via :class:`ralph.display.record_writer.RenderedRecordWriter`.

    Adding a new event kind or a new agent means adding one
    renderer; both consumers inherit the change automatically.
    The unknown / malformed-input fallback also produces a valid
    :class:`PresentedEntry` (kind="unknown", severity="info") so a
    raw dump can never reach either consumer.

    Attributes:
        kind: ActivityEventKind for the event (e.g. ``TEXT``,
            ``TOOL_CALL``, ``TOOL_RESULT``, ``THINKING``,
            ``PROGRESS``, ``SUBAGENT_PROGRESS``, ``UNKNOWN``).
            ``UNKNOWN`` is the first-class fallback for unparsed /
            malformed input.
        severity: One of ``info`` / ``warn`` / ``error``. The
            renderer registry picks the severity; consumers do not
            compute it.
        identity: Agent / unit identity for this entry. Used by the
            renderer to apply the deterministic identity color and
            by the record writer to fill the ``agent=`` field.
        timestamp: Optional ISO-8601 timestamp (UTC). ``None`` when
            the event has no timestamp (the record writer renders
            the placeholder ``[??:??:??]`` slot in that case).
        phase: Optional phase label (e.g. ``development``,
            ``planning``). Surfaced in the live log section header
            and in the record writer's field-order contract.
        cycle: Optional outer-cycle count (1-indexed). Surfaced in
            the record writer as ``cycle=N``.
        iter: Optional inner-iteration label (e.g. ``2/4``).
            Surfaced in the record writer as ``iter=2/4``.
        body: Plain-text body. Renderers may split this across
            multiple live-display lines, but the record writer
            flattens it back to a single greppable line.
        metadata: Optional metadata bag (tool name, exit code,
            duration, ...). Carried for downstream consumers; the
            record writer ignores it.
    """

    kind: str
    severity: str
    identity: str
    body: str
    timestamp: str | None = None
    phase: str | None = None
    cycle: int | None = None
    iter: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    # P1 (wt-028-display S-12 / AC-07): hierarchy is data, not glyphs.
    # ``indent_level`` is a 0-indexed depth; ``grouping_role`` is the
    # semantic role used by the record writer to choose indentation
    # and by the live log to choose hanging-indent continuation
    # columns. Adding a new event kind means picking a role here
    # once; every consumer (record writer, live log, accessibility
    # matrix) inherits the structural position automatically.
    indent_level: int = 0
    grouping_role: str = "agent_text"

    @property
    def agent(self) -> str:
        """Alias for :attr:`identity` (the record writer's contract).

        S-10 (wt-028-display P1) keeps ``identity`` as the
        canonical field name on the structured entry, but the
        text-first record writer's stable field order uses
        ``agent=`` so the same line is greppable for either
        concept. Exposing ``agent`` as a property pins the
        compatibility without forking the dataclass shape.
        """
        return self.identity


def build_presented_entry(
    event: AgentActivityEvent,
    *,
    unit_id: str | None = None,
    timestamp: str | None = None,
    phase: str | None = None,
    cycle: int | None = None,
    iter_: str | None = None,
) -> PresentedEntry:
    """Build the canonical :class:`PresentedEntry` for ``event``.

    S-10 (wt-028-display P1): every renderer in
    :data:`EVENT_RENDERERS` is expected to expose its rendered
    output as a :class:`PresentedEntry`. This helper centralises
    the extraction of the public-ish attributes (kind / severity /
    identity / body / metadata) so the live display path can keep
    its ``Text`` rendering while the text-first record writer
    consumes the same structured intermediate.

    S-12 (wt-028-display P1): the canonical entry also carries
    ``indent_level`` and ``grouping_role`` so hierarchy is data
    rather than embedded glyphs. The mapping is fixed in
    :data:`_KIND_TO_GROUPING`; adding a new event kind means
    picking a role there once.

    S-14 (wt-028-display P1 / AC-04): severity is derived from
    *outcome* metadata for ``TOOL_RESULT`` (nonzero exit code or
    explicit ``is_error=True`` \u2192 ``error``; otherwise
    ``info``). ``ERROR`` is the only kind that maps to
    ``severity=error`` unconditionally. The
    ``UNKNOWN``-as-``info`` fallback (the AC-04 missing-data
    graceful-degrade contract) is preserved: when a ``TOOL_RESULT``
    omits its exit code and error flag, the renderer does not
    invent a failure; it stays ``info``.
    """
    identity = unit_id or ""
    metadata: dict[str, object] = {}
    body = ""
    if event.content is not None:
        body = str(event.content)
    # DA-001 (wt-028-display S-3 / AC-02): strip parser-channel
    # prefixes (``text: ``, ``thinking: ``, ``tool_use: ``,
    # ``tool_result: ``) at the canonical PresentedEntry seam so
    # the text-first record writer carries the same normalized
    # body the renderer registry produces. The renderer strips
    # the prefix at its own seam (see
    # :func:`ralph.display.agent_event_renderer._normalized_event_content`);
    # the record writer reads the body off the event directly, so
    # the same normalization has to run here. wt-028-display S-5
    # consolidated the stripper into a single leaf module
    # (:mod:`ralph.display._channel_prefix_stripper`) so the live
    # log and the rendered record cannot drift.
    body = _strip_live_chrome(
        strip_parser_channel_prefix(body),
        identity,
        preserve_before_pair=event.kind is ActivityEventKind.TOOL_USE,
    )
    if event.kind in {ActivityEventKind.TEXT, ActivityEventKind.THINKING}:
        body = _strip_markdown_emphasis(body)
    if event.metadata:
        metadata.update(event.metadata)
    severity = _derive_severity(event.kind, metadata)
    if event.kind is ActivityEventKind.TOOL_USE:
        body = _tool_call_record_body(body, metadata)
    if event.kind is ActivityEventKind.TOOL_RESULT:
        body = _tool_result_record_body(body, metadata, severity)
    if identity and event.kind in {ActivityEventKind.STATUS, ActivityEventKind.UNKNOWN}:
        body = body.removeprefix(f"{identity} ")
    grouping_role, indent_level = _KIND_TO_GROUPING.get(event.kind, ("agent_text", 0))
    # S-2 (wt-028-display P1 / AC-01 / DA-002): the canonical entry
    # carries the source event's real timestamp unless the caller
    # supplies an explicit authoritative override. The pre-fix
    # contract ignored ``event.timestamp`` entirely, so an event
    # whose ``timestamp`` was the only authoritative source still
    # surfaced as ``[??:??:??]``. The display clock (``clock()``)
    # is the normal authoritative source in production (see
    # ``_append_recorded_entry`` in ``parallel_display.py``); the
    # event's own timestamp is the fallback that lets fixtures and
    # replay tests preserve the source's stamp end-to-end.
    effective_timestamp = timestamp
    if not effective_timestamp:
        # ``AgentActivityEvent.timestamp`` is ``str | None``; the
        # attribute read keeps that type narrow rather than the
        # ``Any`` ``getattr(..., default)`` returns, which mypy
        # refuses to narrow with ``isinstance``.
        event_timestamp = event.timestamp
        if isinstance(event_timestamp, str) and event_timestamp:
            effective_timestamp = event_timestamp
    return PresentedEntry(
        kind=event.kind.value if hasattr(event.kind, "value") else str(event.kind),
        severity=severity,
        identity=identity,
        body=body,
        timestamp=effective_timestamp,
        phase=phase,
        cycle=cycle,
        iter=iter_,
        metadata=metadata,
        indent_level=indent_level,
        grouping_role=grouping_role,
    )


def _strip_markdown_emphasis(body: str) -> str:
    """Remove prose-only Markdown chrome from canonical event bodies."""
    if not body:
        return body

    def unwrapped(match: re.Match[str]) -> str:
        return match.group(2) or match.group(4)

    return re.sub(
        r"(?<!\w)(\*\*|__)(.+?)\1(?!\w)|(?<!\w)([*_])(.+?)\3(?!\w)",
        unwrapped,
        re.sub(r"^#{1,6}\s+", "", body),
    ).lstrip()


def _tool_result_record_body(body: str, metadata: dict[str, object], severity: str) -> str:
    """Render a result as its terminal outcome plus content, not a repeated call header."""
    raw_tool = next(
        (
            value
            for key in ("tool_name", "name", "tool")
            if isinstance(value := metadata.get(key), str) and value
        ),
        "tool",
    )
    tool = friendly_tool_name(raw_tool)
    outcome = "failed" if severity == "error" else "ok"
    body = _strip_leading_tokens(body, tool, outcome)
    # A correlated result hangs below its already-rendered call header; an
    # orphan result names its tool so flood entries remain distinguishable.
    if tool_call_id(metadata):
        return " ".join(part for part in (outcome, body) if part)
    return " ".join(part for part in (tool, outcome, body) if part)


def _tool_call_record_body(body: str, metadata: dict[str, object]) -> str:
    """Preserve a tool name and any continuation glyph on every record call."""
    body = body.rstrip()
    if "↳" in body and not body.endswith("↳"):
        body = body.split("↳", 1)[1].lstrip()
    raw_tool = next(
        (
            value
            for key in ("tool_name", "name", "tool")
            if isinstance(value := metadata.get(key), str) and value
        ),
        "",
    )
    tool = friendly_tool_name(raw_tool) if raw_tool else ""
    call_id = tool_call_id(metadata)
    target = metadata.get("target")
    suffixes = [f"call_id={call_id}" if call_id else ""]
    if isinstance(target, str) and target:
        suffixes.append(target)
    suffix = " ".join(part for part in suffixes if part)
    # Preview headers already name the operator-facing tool; never prepend
    # the parser identifier or a synthetic ``tool`` fallback.
    for marker in ("▸", ">"):
        if marker in body:
            return f"{body[body.index(marker) :]} {suffix}".rstrip()
    if not tool:
        return f"{body} {suffix}".rstrip()
    if body.startswith("(") or not body.casefold().startswith(tool.casefold()):
        return f"{tool} {body} {suffix}".rstrip()
    return body if not suffix or suffix in body else f"{body} {suffix}"


def _strip_leading_tokens(body: str, tool: str, outcome: str) -> str:
    """Remove live-rendered duplicate tool/outcome prefixes from a result body."""
    while body:
        for token in (tool, outcome):
            prefix = f"{token} "
            if body.casefold().startswith(prefix.casefold()):
                body = body[len(prefix) :]
                break
            if body.casefold() == token.casefold():
                return ""
        else:
            return body
    return body


_LIVE_BADGE_PREFIX = re.compile(r"^[✓✗⚠\u2139◐]\s+(?:PASS|FAIL|WARN|INFO|RUN)(?:\s+|$)")
_LIVE_BADGE_ONLY = re.compile(r"^(?:\d{2}:\d{2}:\d{2}\s+)?\S+$")
_LIVE_BADGE_IDENTITY_ONLY = re.compile(r"^\d{2}:\d{2}:\d{2}\s+[^\s]+$")


def _strip_live_chrome(body: str, identity: str, *, preserve_before_pair: bool = False) -> str:
    """Remove a live-rendered badge/identity prefix from a record body.

    The canonical entry is text-first; status, time, and identity belong to
    its structured fields, not copied from a previously-rendered live line.
    """
    match = _LIVE_BADGE_PREFIX.match(body)
    identity_prefix = f"{identity} "
    if match is None:
        return body
    remainder = body[match.end() :].strip()
    if "↳" in remainder and not preserve_before_pair:
        return remainder.split("↳", 1)[1].strip()
    if identity and remainder == identity:
        return ""
    if _LIVE_BADGE_IDENTITY_ONLY.fullmatch(remainder):
        return ""
    return remainder.removeprefix(identity_prefix) if identity else remainder


def _derive_severity(kind: ActivityEventKind, metadata: dict[str, object]) -> str:
    """Return ``info`` / ``warn`` / ``error`` for ``kind`` + outcome metadata.

    S-14 (wt-028-display P1 / AC-04): the bug was that every
    ``TOOL_RESULT`` (and ``UNKNOWN``) became ``severity=warn``
    regardless of outcome, and ``ERROR`` was also ``warn`` not
    ``error``. Severity now reflects outcome:

    * ``ERROR`` \u2192 ``error`` (the only kind that is unconditional).
    * ``TOOL_RESULT``: outcome metadata drives the verdict. A
      truthy ``is_error``, a nonzero ``exit_code`` / ``status``
      / ``error_code`` (or any numeric metadata value whose
      ``int(...)`` is nonzero), or a present ``error`` /
      ``stderr`` payload \u2192 ``error``. A missing or zero outcome
      \u2192 ``info`` (missing-data graceful-degrade).
    * ``UNKNOWN`` \u2192 ``info`` (the designed fallback).
    * Everything else \u2192 ``info``.

    The function never invents a failure when outcome metadata is
    absent; it preserves the operator's principle "the file
    surface and the terminal surface carry the same vocabulary",
    so a successful tool result renders ``info`` on both surfaces
    and a failed one renders ``error`` on both.
    """
    if kind is ActivityEventKind.ERROR:
        return "error"
    if kind is ActivityEventKind.TOOL_RESULT:
        if outcome_is_failure(metadata):
            return "error"
        return "info"
    if kind is ActivityEventKind.UNKNOWN:
        return "info"
    return "info"


def outcome_is_failure(metadata: dict[str, object]) -> bool:
    """True when the tool-result outcome metadata flags a failure.

    Inspects the conventional parser metadata keys
    (``is_error``, ``exit_code``, ``status``, ``error_code``,
    ``error``, ``stderr``). The first three are explicit signals;
    a present ``error`` or ``stderr`` payload is treated as a
    failure even without an explicit code, because the parser
    only emits those when something went wrong.
    """
    if not metadata:
        return False
    flag = metadata.get("is_error")
    if isinstance(flag, bool) and flag:
        return True
    for key in ("exit_code", "status", "error_code"):
        if _code_value_is_failure(metadata.get(key)):
            return True
    return bool(metadata.get("error") or metadata.get("stderr"))


def _code_value_is_failure(value: object) -> bool:
    """True for explicit outcome codes that mean failure."""
    if isinstance(value, bool):
        # ``status=True`` is success; ``status=False`` is failure.
        return value is False
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped) and stripped != "0"
    return False


#: Parser-channel prefixes that must NEVER reach an operator-facing
#: surface (DA-001 / AC-02).
#:
#: wt-028-display S-5: the prefix lists and the stripper now live
#: in a single leaf module
#: (:mod:`ralph.display._channel_prefix_stripper`). Both the live
#: log and the rendered record import the canonical stripper from
#: there so a parser that changes one set of prefixes cannot leave
#: the other behind. The historical local copies were deleted --
#: this comment block documents why no per-module redefinition
#: exists.
#:
#: AC-07 (wt-028-display S-6): the SPACE-LESS form
#: (``text:hello``) is also a known accumulator key shape used by
#: pi/claude when the first content fragment lacks a separating
#: space. The space-less stripper only fires when the remainder is
#: non-empty AND does not begin with whitespace.


#: Map ``ActivityEventKind`` to ``(grouping_role, indent_level)`` for
#: the structured-hierarchy contract. The roles are part of the
#: P1 (wt-028-display S-12) vocabulary: a tool result hangs under
#: its call (deeper indent), reasoning reads as one subordinated
#: passage, and condensation markers stay at body level so the
#: reader knows they're the same kind of chrome as the body line
#: they replace.
_KIND_TO_GROUPING: dict[ActivityEventKind, tuple[str, int]] = {
    ActivityEventKind.TEXT: ("agent_text", 0),
    ActivityEventKind.THINKING: ("reasoning", 1),
    ActivityEventKind.STATUS: ("status_line", 0),
    ActivityEventKind.TOOL_USE: ("tool_call", 0),
    ActivityEventKind.TOOL_RESULT: ("tool_result", 1),
    ActivityEventKind.ERROR: ("error", 0),
    ActivityEventKind.LIFECYCLE: ("phase_header", 0),
    ActivityEventKind.HEARTBEAT: ("heartbeat", 0),
    ActivityEventKind.PROGRESS: ("progress", 1),
    ActivityEventKind.SUBAGENT_PROGRESS: ("progress", 1),
    ActivityEventKind.UNKNOWN: ("unrecognized", 0),
}


__all__ = ["PresentedEntry", "build_presented_entry", "outcome_is_failure"]
