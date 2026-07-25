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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.display.activity_event_kind import ActivityEventKind

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
    """
    identity = unit_id or ""
    metadata: dict[str, object] = {}
    body = ""
    if event.content is not None:
        body = str(event.content)
    if event.metadata:
        metadata.update(event.metadata)
    severity = "info"
    if event.kind in (
        ActivityEventKind.TOOL_RESULT,
        ActivityEventKind.ERROR,
        ActivityEventKind.UNKNOWN,
    ):
        severity = "warn"
    if event.kind == ActivityEventKind.ERROR:
        severity = "error"
    return PresentedEntry(
        kind=event.kind.value if hasattr(event.kind, "value") else str(event.kind),
        severity=severity,
        identity=identity,
        body=body,
        timestamp=timestamp,
        phase=phase,
        cycle=cycle,
        iter=iter_,
        metadata=metadata,
    )


__all__ = ["PresentedEntry", "build_presented_entry"]
