
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ralph.display.surface_catalog import SURFACE_CATALOG

if TYPE_CHECKING:
    from collections.abc import Mapping


class DisplayCapability(StrEnum):

    SYNTAX_HIGHLIGHTING = "syntax_preview"
    FILE_PREVIEW = "file_preview"
    EDIT_DIFF = "diff_preview"


_DISPLAY_CAPABILITY_BY_SURFACE: Final[Mapping[str, DisplayCapability]] = {
    member.value: member for member in DisplayCapability
}


def surface_to_capability(surface_name: str) -> DisplayCapability | None:
    return _DISPLAY_CAPABILITY_BY_SURFACE.get(surface_name)


_ALL_DISPLAY_CAPABILITIES: Final[tuple[DisplayCapability, ...]] = tuple(DisplayCapability)


def all_display_capabilities() -> tuple[DisplayCapability, ...]:
    return _ALL_DISPLAY_CAPABILITIES


def _catalog_has_all_capability_surfaces() -> None:
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
