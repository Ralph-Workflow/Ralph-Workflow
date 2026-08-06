"""Bounded transport-neutral recorder for display-capability occurrences.

The capability declaration in :mod:`ralph.agents.display_capabilities`
promises that an agent whose stance for
:class:`DisplayCapability.SYNTAX_HIGHLIGHTING` is ``SUPPORTED`` will
actually render a syntax-highlighted block when the agent emits a
write-style tool call. The promise is the only thing that makes a
``SUPPORTED`` declaration different from an empty one; without
verification the original OpenCode defect can return (parser drops
the structured tool metadata, the display layer silently renders
nothing, and no one says so).

This module exposes the recorder used at the existing preview
production point. The shared preview builder
(:func:`ralph.display.edit_preview.build_edit_preview`) is the single
choke point through which every supported preview surface flows;
attaching the recorder there means a SUPPORTED capability is
counted only when the preview actually materializes -- not when a
parser merely emitted a tool name. The recording call site stays
in :mod:`ralph.display.parallel_display` and the recorder's
storage stays bounded so an unattended long-running smoke run
cannot leak observations across runs.

The recorder's value object is :class:`CapabilityObservation` in
:mod:`ralph.display.capability_observation` (one public class per
file -- the repo-structure audit's ``multiple top-level classes``
rule). The surface-to-capability mapping helpers
(:func:`capability_for_render`, :func:`infer_surface_for_preview`)
also live here so the recorder and its helpers share one module.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import RenderableType

    from ralph.agents.display_capabilities import DisplayCapability
    from ralph.display.capability_observation import CapabilityObservation


#: Bounded-capacity deque used to store the per-smoke-run observations.
#: Sized to hold a few hundred tool calls without unbounded growth; the
#: recorder exposes ``observed_capabilities`` (a frozenset snapshot of
#: which capabilities produced a render) rather than the raw sequence
#: so an unbounded call rate does not cause OOM. Sized for the upper
#: bound of tool calls a single ``ralph smoke-interactive-*`` run can
#: legitimately emit; larger runs are not the supported path.
_OBSERVATION_LOG_CAPACITY: int = 512


class CapabilityObservationRecorder:
    """Bounded recorder for capability occurrences in one smoke run.

    The recorder is the single source of truth for "what the display
    layer actually rendered". An empty ``observed_capabilities``
    means the shared preview builder never produced a renderable
    during the run -- the smoking gun for the original OpenCode
    defect.

    Bounded: a fresh ``deque(maxlen=_OBSERVATION_LOG_CAPACITY)``
    holds the per-event log so a runaway tool-call rate cannot
    accumulate without limit. ``observed_capabilities`` is a
    :class:`frozenset` derived from the log's contents, so the
    "which capabilities fired" question has a stable answer
    regardless of the cap.

    Thread-safety: the recorder is per-``ParallelDisplay`` (which is
    itself single-threaded); no internal lock is needed. Concurrent
    callers must serialize externally.
    """

    def __init__(self) -> None:
        self._observations: deque[CapabilityObservation] = deque(
            maxlen=_OBSERVATION_LOG_CAPACITY
        )  # bounded-accumulator-ok: deque(maxlen=...) caps the per-event log at _OBSERVATION_LOG_CAPACITY entries
        self._observed_capabilities: set[DisplayCapability] = set()  # bounded-accumulator-ok: the per-capability set is bounded by the catalog-derived vocabulary size, which is small (3 entries today)

    def record(self, observation: CapabilityObservation) -> None:
        """Append an observation. Idempotent on the per-capability set."""
        self._observations.append(observation)
        self._observed_capabilities.add(observation.capability)

    def observed_capabilities(self) -> frozenset[DisplayCapability]:
        """Return the set of capabilities that have fired so far."""
        return frozenset(self._observed_capabilities)

    def observations_for_capability(
        self, capability: DisplayCapability
    ) -> tuple[CapabilityObservation, ...]:
        """Return the recorded observations for ``capability`` in arrival order."""
        return tuple(o for o in self._observations if o.capability is capability)

    def observed_count(self) -> int:
        """Return the total number of recorded observations."""
        return len(self._observations)

    def clear(self) -> None:
        """Drop all observations. Used between smoke runs on the same recorder."""
        self._observations.clear()
        self._observed_capabilities.clear()

    def __iter__(self) -> Iterator[CapabilityObservation]:
        return iter(tuple(self._observations))


def capability_for_render(
    *,
    surface_name: str,
    tool_name: str | None,
) -> DisplayCapability | None:
    """Map a preview surface and tool name to the capability it exercises.

    Returns ``None`` when the surface does not correspond to any
    operator-facing capability (e.g. the ``welcome`` frame, the
    ``elision`` marker, or any surface whose name is not in the
    catalog-derived vocabulary).

    The tool name is informational only -- the surface itself is
    the source of truth for which capability fires. The tool name
    is included in the returned mapping so the recorder can keep a
    per-tool diagnostic trail in addition to the per-capability set.
    """
    from ralph.agents.display_capabilities import (
        surface_to_capability,  # reason: lazy import keeps this module's import surface small
    )

    return surface_to_capability(surface_name)


def infer_surface_for_preview(
    renderable: RenderableType | None,
    canonical_operation: str,
) -> str:
    """Infer the catalog surface name that ``renderable`` belongs to.

    The shared :func:`build_edit_preview` is transport-neutral and
    returns one of three catalog surface shapes for file activity:

      * ``"syntax_preview"`` -- a write / append / NotebookEdit-style
        preview that emits a syntax-highlighted content block.
      * ``"file_preview"`` -- a read-style preview that emits the
        syntax-highlighted file content for display.
      * ``"diff_preview"`` -- an edit-style preview that emits
        ``- old`` / ``+ new`` polarity rows.

    ``canonical_operation`` is the operation recorded in
    :class:`ralph.display.preview_payload.PreviewPayload.operation`
    and selects between the three shapes: ``"write"`` /
    ``"append"`` map to ``syntax_preview``; ``"read"`` maps to
    ``file_preview``; ``"replace"`` and ``"patch"`` map to
    ``diff_preview``. A ``None`` renderable returns ``"syntax_preview"``
    defensively so a caller that records ``None`` (a degenerate
    preview) still gets a stable identifier.
    """
    del renderable  # The catalog surface is inferred from operation, not the renderable
    if canonical_operation == "read":
        return "file_preview"
    if canonical_operation in {"write", "append", "NotebookEdit"}:
        return "syntax_preview"
    if canonical_operation in {"replace", "patch"}:
        return "diff_preview"
    return "syntax_preview"


__all__ = [
    "CapabilityObservationRecorder",
    "capability_for_render",
    "infer_surface_for_preview",
]
