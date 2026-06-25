"""URI builder/parser and session-scoped manifest for ralph://media resources.

The manifest tracks every resource_reference artifact emitted during a session
so they can be listed via resources/list and retrieved via resources/read.
"""

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

# wt-024 M2: default cap on retained manifest entries.  Matches the
# shipped-in-production knob documented in the wt-024 plan.  256 is
# generous for any realistic session; pathological unbounded growth
# is the only thing this cap prevents.
_DEFAULT_MAX_ENTRIES = 256


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

    # wt-024 M2: bounded retention.  ``_entries`` is an ``OrderedDict``
    # so we can evict the oldest entry (FIFO) when a NEW identity
    # pushes the manifest past ``max_entries``.  Re-adding an
    # EXISTING identity dedups in place and PRESERVES its original
    # insertion position so ``list_entries()`` (and the downstream
    # ``resources/list`` response) keep stable ordering across
    # duplicate adds.  Typed as ``OrderedDict`` (not ``dict``) so
    # mypy --strict can verify ``popitem(last=False)`` is a valid
    # call.
    _entries: OrderedDict[str, ManifestEntry] = field(
        default_factory=OrderedDict,
    )
    _identity_index: dict[str, str] = field(default_factory=dict)
    # Maximum number of distinct artifact_ids retained in ``_entries``.
    # When a NEW identity pushes ``len(_entries) > max_entries`` we
    # evict the oldest artifact and clear its mapping from
    # ``_identity_index``.  Default keeps existing small-fixture
    # behaviour unchanged.
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
        """Add or replace an artifact and return its manifest entry.

        Re-adding an EXISTING identity dedups in place and PRESERVES
        the original insertion order so the ``resources/list`` output
        order stays stable across duplicate adds (wt-024 analysis
        feedback: reordering on re-add was an externally visible
        behavior change that the memory cap did not require).
        """
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
        # wt-024 M2 follow-up (AC-06): when a durable replay source is
        # wired at add-time (a byte_loader is supplied), the manifest
        # must NOT retain the raw payload in memory. The byte_loader
        # is the canonical source for rehydrating bytes later, so
        # storing raw_bytes on top of it doubles peak memory per
        # entry (up to 256 entries x multi-MB each) for no observable
        # benefit. Raw bytes are only retained when the caller has
        # no durable replay source — i.e. when neither byte_loader nor
        # cache_path is supplied — so the in-memory-only contract for
        # legacy callers is preserved verbatim.
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
        """Pop the oldest ``_entries`` key and drop its identity mappings.

        Returns the evicted ``artifact_id`` so callers can log or
        observe the eviction.  No-op when ``_entries`` is empty.
        """
        if not self._entries:
            return None
        evicted_id, _ = self._entries.popitem(last=False)
        # Remove every ``_identity_index`` entry whose value is the
        # evicted artifact_id so we never serve a stale dedup to a
        # future ``add()`` of the same identity.
        stale_identities = [
            identity
            for identity, mapped_id in self._identity_index.items()
            if mapped_id == evicted_id
        ]
        for identity in stale_identities:
            del self._identity_index[identity]
        return evicted_id

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
