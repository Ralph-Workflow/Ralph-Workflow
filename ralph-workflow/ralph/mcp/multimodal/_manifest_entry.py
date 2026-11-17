from __future__ import annotations

from collections.abc import Callable

ByteLoader = Callable[[], bytes | None]


class ManifestEntry:
    __slots__ = (
        "_byte_loader",
        "_raw_bytes",
        "artifact_id",
        "cache_path",
        "identity_key",
        "mime_type",
        "modality",
        "source_path",
        "source_uri",
        "title",
        "uri",
    )

    def __init__(
        self,
        artifact_id: str,
        uri: str,
        mime_type: str,
        title: str,
        modality: str,
        identity_key: str = "",
        cache_path: str = "",
        source_path: str = "",
        source_uri: str = "",
        *,
        _raw_bytes: bytes | None = None,
        _byte_loader: ByteLoader | None = None,
    ) -> None:
        self.artifact_id = artifact_id
        self.uri = uri
        self.mime_type = mime_type
        self.title = title
        self.modality = modality
        self.identity_key = identity_key
        self.cache_path = cache_path
        self.source_path = source_path
        self.source_uri = source_uri
        self._raw_bytes = _raw_bytes
        self._byte_loader = _byte_loader

    @property
    def raw_bytes(self) -> bytes:
        return self.load_bytes() or b""

    def load_bytes(self) -> bytes | None:
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
        return {
            "uri": self.uri,
            "name": self.title,
            "description": f"{self.modality} artifact: {self.title}",
            "mimeType": self.mime_type,
        }


__all__ = ["ByteLoader", "ManifestEntry"]
