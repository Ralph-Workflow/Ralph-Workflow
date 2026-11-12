from __future__ import annotations

import hashlib
import re
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from ralph.mcp.multimodal._manifest_entry import ManifestEntry
from ralph.mcp.multimodal._media_entry_extras import MediaEntryExtras
from ralph.mcp.multimodal._media_source import MediaSource

_RALPH_MEDIA_PREFIX = "ralph://media/"
_URI_PATTERN = re.compile(
    r"^ralph://media/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

MEDIA_URI_TEMPLATE = "ralph://media/{artifact_id}"

_DEFAULT_MAX_ENTRIES = 256


def build_media_uri(artifact_id: str) -> str:
    return f"{_RALPH_MEDIA_PREFIX}{artifact_id}"


def parse_media_uri(uri: str) -> str | None:
    m = _URI_PATTERN.match(uri)
    if m is None:
        return None
    return m.group(1)


def new_artifact_id() -> str:
    return str(uuid.uuid4())


def build_media_identity(
    *,
    modality: str,
    mime_type: str,
    title: str,
    source: MediaSource | None = None,
) -> str:
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
    _entries: OrderedDict[str, ManifestEntry] = field(
        default_factory=OrderedDict,
    )
    _identity_index: dict[str, str] = field(default_factory=dict)
    max_entries: int = _DEFAULT_MAX_ENTRIES

    def add(
        self,
        *,
        title: str,
        mime_type: str,
        modality: str,
        raw_bytes: bytes,
        extras: MediaEntryExtras | None = None,
    ) -> ManifestEntry:
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
        artifact_id = self._identity_index.get(
            resolved_identity,
            xt.artifact_id or new_artifact_id(),
        )
        uri = build_media_uri(artifact_id)
        retain_raw_bytes = xt.byte_loader is None and not xt.cache_path
        stored_raw_bytes = raw_bytes if retain_raw_bytes else None
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
            _raw_bytes=stored_raw_bytes,
            _byte_loader=xt.byte_loader,
        )
        existing = self._entries.get(artifact_id)
        self._entries[artifact_id] = entry
        self._identity_index[resolved_identity] = artifact_id
        if existing is None and len(self._entries) > self.max_entries:
            self._evict_oldest()
        return entry

    def _evict_oldest(self) -> str | None:
        if not self._entries:
            return None
        evicted_id, _ = self._entries.popitem(last=False)
        stale_identities = [
            identity
            for identity, mapped_id in self._identity_index.items()
            if mapped_id == evicted_id
        ]
        for identity in stale_identities:
            del self._identity_index[identity]
        return evicted_id

    def get(self, artifact_id: str) -> ManifestEntry | None:
        return self._entries.get(artifact_id)

    def list_entries(self) -> list[ManifestEntry]:
        return list(self._entries.values())

    def is_empty(self) -> bool:
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
