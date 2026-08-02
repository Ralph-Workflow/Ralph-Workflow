"""Media artifact I/O: cache writing, registry persistence, and byte loading."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_bytes_if_changed
from ralph.mcp.tools._cache_retention import prune_cache_files
from ralph.prompts.debug_dump import (
    media_cache_artifact_path,
    media_registry_path,
    media_session_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    from ralph.mcp.tools._cache_retention import CachePruneResult
    from ralph.workspace import Workspace

    class CachePruner(Protocol):
        def __call__(
            self,
            files: Iterable[Path],
            *,
            max_total_bytes: int,
            keep_paths: Iterable[Path] = (),
        ) -> CachePruneResult: ...

_MEDIA_SESSION_SCHEMA_VERSION = "2"
MEDIA_CACHE_MAX_TOTAL_BYTES = 256 * 1024 * 1024

#: Number of ``_persist_media_session_entry`` / ``_persist_media_registry_entry``
#: adds between full ``_drop_evicted_cache_entries`` stat sweeps. The naive
#: implementation stat'd EVERY cached artifact on EVERY add, which is O(N*M)
#: in the worst case (N entries, M adds). Gating the stat pass behind this
#: counter drops the amortized cost to O(N*M/K) ≈ O(M). Eviction semantics
#: are preserved exactly: the next prune tick still drops entries whose
#: cache files were evicted. The dedup-by-artifact_id list comprehension
#: still runs every add, so same-id replacement is immediate (AC-10).
_MEDIA_PRUNE_INTERVAL: int = 32


class _MediaPruneCounter:
    """Own the bounded periodic-prune counter without mutable module globals."""

    def __init__(self, counter: Iterator[int] | int | None = None) -> None:
        self._counter = count(1) if counter is None else counter
        self._workspace_key: str | None = None

    def advance(self, workspace_key: str) -> bool:
        """Advance one workspace's counter and report whether this is a prune tick."""
        if workspace_key != self._workspace_key:
            self._workspace_key = workspace_key
            self._counter = count(1)
        counter = self._counter
        if isinstance(counter, int):
            next_count = counter + 1
            self._counter = next_count
        else:
            next_count = next(counter)
        return next_count % _MEDIA_PRUNE_INTERVAL == 0

    def reset(self, counter: Iterator[int] | int | None = None) -> None:
        """Reset the test-injectable counter to a deterministic starting point."""
        self._counter = count(1) if counter is None else counter
        self._workspace_key = None


_media_prune_counter = _MediaPruneCounter()


def _advance_media_prune_counter(workspace: Workspace) -> bool:
    """Advance the bounded workspace-local prune counter and report whether this is a tick."""
    return _media_prune_counter.advance(workspace.absolute_path(media_registry_path()))


def _reset_media_prune_counter(counter: Iterator[int] | int | None = None) -> None:
    """Reset the injectable prune counter for deterministic tests."""
    _media_prune_counter.reset(counter)


def _media_session_identity(entry: dict[str, str]) -> str:
    """Return the dedupe identity for a persisted media-session entry."""
    identity_key = entry.get("identity_key", "")
    if identity_key:
        return identity_key
    source_uri = entry.get("source_uri", "")
    source_path = entry.get("source_path", "")
    modality = entry.get("modality", "")
    artifact_id = entry.get("artifact_id", "")
    uri = entry.get("uri", "")
    if source_uri:
        return f"source-uri:{modality}:{source_uri}"
    if source_path:
        return f"source-path:{modality}:{source_path}"
    return f"artifact-id:{artifact_id or uri}"


def write_durable_media_cache(
    workspace: Workspace,
    artifact_id: str,
    raw_bytes: bytes,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    cache_pruner: CachePruner = prune_cache_files,
) -> str:
    """Write raw bytes to the durable media cache and return the workspace-relative path."""
    if len(raw_bytes) > MEDIA_CACHE_MAX_TOTAL_BYTES:
        return ""
    cache_path = media_cache_artifact_path(artifact_id)
    try:
        abs_path = Path(workspace.absolute_path(cache_path))
        write_bytes_if_changed(
            backend,
            abs_path,
            raw_bytes,
            prepare_write=lambda: backend.mkdir(abs_path.parent, parents=True, exist_ok=True),
        )
        # filesystem-read-ok: bounded cache retention enumerates only the media-cache directory after publication
        cache_pruner(
            # filesystem-read-ok: bounded cache retention enumerates only the media-cache directory after publication
            abs_path.parent.glob("*"),
            max_total_bytes=MEDIA_CACHE_MAX_TOTAL_BYTES,
            keep_paths=(abs_path,),
        )
    except Exception:
        return ""
    return cache_path


def _entry_has_available_cache(workspace: Workspace, entry: dict[str, str]) -> bool:
    """Return False when an entry points at a cache file already evicted from disk."""
    cache_path = entry.get("cache_path", "")
    if not cache_path:
        return True
    try:
        return workspace.is_file(cache_path)
    except Exception:
        return False


def _drop_evicted_cache_entries(
    workspace: Workspace,
    artifacts: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Drop metadata entries whose durable cache files no longer exist."""
    return [artifact for artifact in artifacts if _entry_has_available_cache(workspace, artifact)]


def _persist_media_registry_entry(
    workspace: Workspace,
    entry: dict[str, str],
) -> None:
    """Write entry to the centralized media registry for cross-session lookup."""
    path = media_registry_path()
    artifact_id = entry["artifact_id"]
    run_prune = _advance_media_prune_counter(workspace)
    try:
        artifacts: list[dict[str, str]] = []
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts = list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        except Exception:
            artifacts = []
        # Periodic prune: only run the O(N) stat pass every K adds.
        # The dedup-by-artifact_id list comprehension below still runs
        # every add so same-id replacement is immediate.
        if run_prune:
            artifacts = _drop_evicted_cache_entries(workspace, artifacts)
        artifacts = [a for a in artifacts if a.get("artifact_id") != artifact_id]
        artifacts.append(entry)
        payload: dict[str, object] = {
            "schema_version": _MEDIA_SESSION_SCHEMA_VERSION,
            "artifacts": artifacts,
        }
        workspace.write(path, json.dumps(payload, indent=2))
    except Exception:
        pass


def _load_persisted_registry_entry(
    workspace: Workspace,
    artifact_id: str,
) -> dict[str, str] | None:
    """Look up a persisted media artifact entry from the centralized registry."""
    path = media_registry_path()
    try:
        data: dict[str, object] = json.loads(workspace.read(path))
        raw_artifacts = data.get("artifacts", [])
        artifacts: list[dict[str, str]] = (
            list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        )
        for entry in artifacts:
            if entry.get("artifact_id") == artifact_id:
                return entry
    except Exception:
        pass
    return None


def _load_artifact_bytes(
    workspace: Workspace,
    cache_path: str,
    source_path: str,
) -> bytes | None:
    """Load artifact bytes from cache_path (durable cache) or source_path (original file)."""
    if cache_path:
        try:
            return DEFAULT_FILE_BACKEND.read_bytes(Path(workspace.absolute_path(cache_path)))
        except Exception:
            pass
    if source_path:
        try:
            return DEFAULT_FILE_BACKEND.read_bytes(Path(workspace.absolute_path(source_path)))
        except Exception:
            pass
    return None


def _persist_media_session_entry(
    session: object,
    workspace: Workspace,
    meta: dict[str, str],
) -> None:
    """Upsert a resource-reference artifact into the persistent session media index."""
    drain: object = getattr(session, "drain", None)
    phase = str(drain) if drain else "standalone"
    path = media_session_path(phase)
    uri = meta["uri"]
    artifact_id = uri.rsplit("/", maxsplit=1)[-1]
    new_entry: dict[str, str] = {
        "artifact_id": artifact_id,
        "uri": uri,
        "mime_type": meta["mime_type"],
        "title": meta["title"],
        "modality": meta["modality"],
        "delivery": meta.get("delivery", "resource_reference_replay"),
        "reason": meta["reason"],
        "source_path": meta.get("source_path", ""),
        "cache_path": meta.get("cache_path", ""),
        "source_uri": meta.get("source_uri", ""),
        "block_type": meta.get("block_type", ""),
        "failure_kind": meta.get("failure_kind", ""),
        "identity_key": meta.get("identity_key", ""),
    }
    run_prune = _advance_media_prune_counter(workspace)
    try:
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts: list[dict[str, str]] = (
                list(raw_artifacts) if isinstance(raw_artifacts, list) else []
            )
        except Exception:
            artifacts = []
        # Periodic prune: only run the O(N) stat pass every K adds.
        # The OrderedDict rebuild below stays per-add because it is O(N)
        # and is needed for correct append-order semantics.
        if run_prune:
            artifacts = _drop_evicted_cache_entries(workspace, artifacts)

        new_identity = _media_session_identity(new_entry)
        ordered: OrderedDict[str, dict[str, str]] = OrderedDict()
        for artifact in artifacts:
            normalized = {str(k): str(v) for k, v in artifact.items()}
            ordered[_media_session_identity(normalized)] = normalized
        ordered[new_identity] = new_entry
        payload: dict[str, object] = {
            "schema_version": _MEDIA_SESSION_SCHEMA_VERSION,
            "phase": phase,
            "artifacts": list(ordered.values()),
        }
        workspace.write(path, json.dumps(payload, indent=2))
    except Exception:
        pass
    _persist_media_registry_entry(workspace, new_entry)
