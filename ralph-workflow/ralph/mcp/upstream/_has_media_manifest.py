
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.mcp.multimodal.resources import MediaManifest


class HasMediaManifest(Protocol):

    @property
    def media_manifest(self) -> MediaManifest: ...


__all__ = ["HasMediaManifest"]
