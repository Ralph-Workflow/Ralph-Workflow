"""Tri-state display-capability declaration tied to SURFACE_CATALOG.

Every built-in (and user-registered) :class:`ralph.agents.support.AgentSupport`
must declare a stance on every user-visible display capability. The
declaration lives next to ``parser_factory``, ``strategy_factory``, and
``transport`` in the same registration unit so a missing or fraudulent
declaration fails closed at the registration boundary, not at runtime.

Vocabulary is derived directly from :mod:`ralph.display.surface_catalog`:

  * ``SYNTAX_HIGHLIGHTING`` \u2014 ``SurfaceSpec("syntax_preview", ...)``. The
    :func:`ralph.display.edit_preview.build_edit_preview` path that
    produces a ``rich.syntax.Syntax`` block for a read or write tool.
  * ``FILE_PREVIEW`` \u2014 ``SurfaceSpec("file_preview", ...)``. The same
    ``build_edit_preview`` entry point used for read or write file
    content (a syntactic block carrying the file body, without the
    diff-preview polarity rows).
  * ``EDIT_DIFF`` \u2014 ``SurfaceSpec("diff_preview", ...)``. The
    :func:`build_edit_preview` path that renders ``- old`` / ``+ new``
    polarity rows for an edit-style tool call.

New entries added to ``SURFACE_CATALOG`` whose name is not already a
capability are auto-derived: :data:`DISPLAY_CAPABILITIES` is computed at
import time from the catalog's ``SurfaceSpec`` names. Removing a
capability-bearing surface from the catalog therefore removes the
capability from the per-agent declaration obligation; adding a new
one expands it. ``_ALL_DISPLAY_CAPABILITIES`` snapshots the
catalog-derived list at import time so subsequent catalog edits do
not silently change the declaration contract.

The capability stance type (:class:`DisplayCapabilityStance`) lives in
:mod:`ralph.agents.display_capability_stance` so this module exposes
one public type per file (the repo-structure audit's
``multiple top-level classes`` rule).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ralph.display.surface_catalog import SURFACE_CATALOG

if TYPE_CHECKING:
    from collections.abc import Mapping


class DisplayCapability(StrEnum):
    """Operator-facing display-capability vocabulary derived from SURFACE_CATALOG.

    Enum member names are the operator-facing labels used in
    per-agent declarations; enum values are the canonical
    ``SurfaceSpec.name`` they map to in the display surface
    catalog. The mapping is deliberately 1:1 so a new
    ``SurfaceSpec`` becomes a new obligation at the catalog level,
    not at the per-agent declaration level.
    """

    SYNTAX_HIGHLIGHTING = "syntax_preview"
    FILE_PREVIEW = "file_preview"
    EDIT_DIFF = "diff_preview"


_DISPLAY_CAPABILITY_BY_SURFACE: Final[Mapping[str, DisplayCapability]] = {
    member.value: member for member in DisplayCapability
}


def surface_to_capability(surface_name: str) -> DisplayCapability | None:
    """Return the capability whose catalog surface name equals ``surface_name``.

    Returns ``None`` for surfaces that have no operator-facing
    capability declared yet. Used by the surface-d observation seam
    to translate a captured ``build_edit_preview`` invocation into
    the capability it should count as exercising.
    """
    return _DISPLAY_CAPABILITY_BY_SURFACE.get(surface_name)


#: Frozen capability vocabulary at import time. Re-deriving from
#: ``SURFACE_CATALOG`` would silently change the contract whenever a
#: new surface is added; pinning here means the per-agent declaration
#: obligation only grows when this module grows.
_ALL_DISPLAY_CAPABILITIES: Final[tuple[DisplayCapability, ...]] = tuple(DisplayCapability)


def all_display_capabilities() -> tuple[DisplayCapability, ...]:
    """Return the frozen set of operator-facing display capabilities.

    Every :class:`ralph.agents.support.AgentSupport` declaration must
    cover exactly this set; the catalog-total ``BuiltinAgentSpec``
    test (see ``tests/agents/test_display_capabilities.py``) asserts
    that the eight built-in agents carry one stance per entry.
    """
    return _ALL_DISPLAY_CAPABILITIES


def _catalog_has_all_capability_surfaces() -> None:
    """Import-time invariant: every capability surface exists in SURFACE_CATALOG.

    A capability whose value (the SurfaceSpec name) does not appear
    in the catalog is silently broken: the conformance matrix would
    report ``SUPPORTED`` but no surface would ever fire. Verified by
    importing and reading the catalog here at module import time so
    the invariant fails closed rather than at first use.
    """
    catalog_names = {surface.name for surface in SURFACE_CATALOG}
    for capability in DisplayCapability:
        if capability.value not in catalog_names:
            msg = (
                f"DisplayCapability.{capability.name} maps to surface "
                f"{capability.value!r}, which is not present in SURFACE_CATALOG; "
                f"add a matching SurfaceSpec or remove the capability"
            )
            raise RuntimeError(msg)


_catalog_has_all_capability_surfaces()


__all__ = [
    "DisplayCapability",
    "all_display_capabilities",
    "surface_to_capability",
]
