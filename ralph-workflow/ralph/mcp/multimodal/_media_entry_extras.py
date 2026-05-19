"""MediaEntryExtras — optional extras when adding a media artifact to the manifest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.multimodal._manifest_entry import ByteLoader


@dataclass(frozen=True)
class MediaEntryExtras:
    """Optional extras when adding a media artifact to the manifest."""

    cache_path: str = ""
    source_path: str = ""
    source_uri: str = ""
    identity_key: str = ""
    byte_loader: ByteLoader | None = None


__all__ = ["MediaEntryExtras"]
