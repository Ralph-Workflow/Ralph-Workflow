"""URI builder/parser and session-scoped manifest for ralph://media resources.

The manifest tracks every resource_reference artifact emitted during a session
so they can be listed via resources/list and retrieved via resources/read.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field

from ralph.mcp.multimodal._manifest_entry import ManifestEntry
from ralph.mcp.multimodal._media_entry_extras import MediaEntryExtras
from ralph.mcp.multimodal._media_source import MediaSource

_RALPH_MEDIA_PREFIX = "ralph://media/"
_URI_PATTERN = re.compile(
    r"^ralph://media/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

MEDIA_URI_TEMPLATE = "ralph://media/{artifact_id}"

def build_media_uri(artifact_id: str) -> str:
    """Build a ralph://media/{artifact_id} URI."""
    return f"{_RALPH_MEDIA_PREFIX}{artifact_id}"


def parse_media_uri(uri: str) -> str | None:
    """Parse a ralph://media/{artifact_id} URI and return artifact_id, or None."""
    m = _URI_PATTERN.match(uri)
    if m is None:
        return None
    return m.group(1)


def new_artifact_id() -> str:
    """Generate a new random artifact UUID."""
    return str(uuid.uuid4())


def build_media_identity(
    *,
    modality: str,
    mime_type: str,
    title: str,
    source: MediaSource | None = None,
) -> str:
    """Build a stable identity for deduping repeated live artifacts."""
    src = source or MediaSource()
    if src.source_uri:
        return f"source-uri:{modality}:{src.source_uri}"
    if src.source_path:
        return f"source-path:{modality}:{src.source_path}"
    if src.raw_bytes is not None:
        digest = hashlib.sha256(src.raw_bytes).hexdigest()
        return f"payload:{modality}:{mime_type}:{title}:{digest}"
    return f"artifact:{modality}:{mime_type}:{title}"


@dataclass
class MediaManifest:
    """Session-scoped manifest of all multimodal resource references."""

    _entries: dict[str, ManifestEntry] = field(default_factory=dict)
    _identity_index: dict[str, str] = field(default_factory=dict)

    def add(
        self,
        *,
        title: str,
        mime_type: str,
        modality: str,
        raw_bytes: bytes,
        extras: MediaEntryExtras | None = None,
    ) -> ManifestEntry:
        """Add or replace an artifact and return its manifest entry."""
        xt = extras or MediaEntryExtras()
        resolved_identity = xt.identity_key or build_media_identity(
            modality=modality,
            mime_type=mime_type,
            title=title,
            source=MediaSource(
                source_path=xt.source_path,
                source_uri=xt.source_uri,
                raw_bytes=raw_bytes,
            ),
        )
        artifact_id = self._identity_index.get(resolved_identity, new_artifact_id())
        uri = build_media_uri(artifact_id)
        entry = ManifestEntry(
            artifact_id=artifact_id,
            uri=uri,
            mime_type=mime_type,
            title=title,
            modality=modality,
            identity_key=resolved_identity,
            cache_path=xt.cache_path,
            source_path=xt.source_path,
            source_uri=xt.source_uri,
            _raw_bytes=raw_bytes,
            _byte_loader=xt.byte_loader,
        )
        self._entries[artifact_id] = entry
        self._identity_index[resolved_identity] = artifact_id
        return entry

    def get(self, artifact_id: str) -> ManifestEntry | None:
        """Retrieve a manifest entry by artifact_id, or None if not found."""
        return self._entries.get(artifact_id)

    def list_entries(self) -> list[ManifestEntry]:
        """Return all manifest entries in insertion order."""
        return list(self._entries.values())

    def is_empty(self) -> bool:
        """Return True if no artifacts have been stored."""
        return not self._entries


__all__ = [
    "MEDIA_URI_TEMPLATE",
    "ManifestEntry",
    "MediaEntryExtras",
    "MediaManifest",
    "MediaSource",
    "build_media_identity",
    "build_media_uri",
    "new_artifact_id",
    "parse_media_uri",
]
