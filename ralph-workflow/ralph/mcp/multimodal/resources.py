"""URI builder/parser and session-scoped manifest for ralph://media resources.

The manifest tracks every resource_reference artifact emitted during a session
so they can be listed via resources/list and retrieved via resources/read.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

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


@dataclass
class ManifestEntry:
    """An entry in the session-scoped multimodal manifest."""

    artifact_id: str
    uri: str
    mime_type: str
    title: str
    modality: str
    raw_bytes: bytes

    def resource_list_entry(self) -> dict[str, object]:
        """Return the entry shape for a resources/list response."""
        return {
            "uri": self.uri,
            "name": self.title,
            "description": f"{self.modality} artifact: {self.title}",
            "mimeType": self.mime_type,
        }


@dataclass
class MediaManifest:
    """Session-scoped manifest of all multimodal resource references."""

    _entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def add(
        self,
        *,
        title: str,
        mime_type: str,
        modality: str,
        raw_bytes: bytes,
    ) -> ManifestEntry:
        """Add a new artifact and return its manifest entry."""
        artifact_id = new_artifact_id()
        uri = build_media_uri(artifact_id)
        entry = ManifestEntry(
            artifact_id=artifact_id,
            uri=uri,
            mime_type=mime_type,
            title=title,
            modality=modality,
            raw_bytes=raw_bytes,
        )
        self._entries[artifact_id] = entry
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
    "MediaManifest",
    "build_media_uri",
    "new_artifact_id",
    "parse_media_uri",
]
