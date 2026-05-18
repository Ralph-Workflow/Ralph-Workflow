"""ManifestEntry — an entry in the session-scoped multimodal manifest."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

ByteLoader = Callable[[], bytes | None]


@dataclass
class ManifestEntry:
    """An entry in the session-scoped multimodal manifest."""

    artifact_id: str
    uri: str
    mime_type: str
    title: str
    modality: str
    identity_key: str = ""
    cache_path: str = ""
    source_path: str = ""
    source_uri: str = ""
    _raw_bytes: bytes | None = field(default=None, repr=False)
    _byte_loader: ByteLoader | None = field(default=None, repr=False, compare=False)

    @property
    def raw_bytes(self) -> bytes:
        """Return artifact bytes, rehydrating from the loader when needed."""
        return self.load_bytes() or b""

    def load_bytes(self) -> bytes | None:
        """Return artifact bytes from memory or a backing replay source."""
        if self._raw_bytes is not None:
            return self._raw_bytes
        if self._byte_loader is None:
            return None
        return self._byte_loader()

    def set_replay_source(
        self,
        *,
        cache_path: str = "",
        source_path: str = "",
        source_uri: str = "",
        byte_loader: ByteLoader | None = None,
        retain_raw_bytes: bool = False,
    ) -> None:
        """Attach a durable replay source and optionally release in-memory bytes."""
        if cache_path:
            self.cache_path = cache_path
        if source_path:
            self.source_path = source_path
        if source_uri:
            self.source_uri = source_uri
        if byte_loader is not None:
            self._byte_loader = byte_loader
        if not retain_raw_bytes:
            self._raw_bytes = None

    def resource_list_entry(self) -> dict[str, object]:
        """Return the entry shape for a resources/list response."""
        return {
            "uri": self.uri,
            "name": self.title,
            "description": f"{self.modality} artifact: {self.title}",
            "mimeType": self.mime_type,
        }


__all__ = ["ByteLoader", "ManifestEntry"]
