"""Media artifact I/O: cache writing, registry persistence, and byte loading."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.tools._cache_retention import prune_cache_files
from ralph.prompts.debug_dump import (
    media_cache_artifact_path,
    media_registry_path,
    media_session_path,
)

if TYPE_CHECKING:
    from ralph.workspace import Workspace

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

#: Module-level counter for the periodic prune gate. Incremented on every
#: ``_persist_media_session_entry`` and ``_persist_media_registry_entry``
#: call; when it crosses the interval, the next call runs the stat sweep.
_media_add_counter: int = 0


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


def _write_durable_media_cache(
    workspace: Workspace,
    artifact_id: str,
    raw_bytes: bytes,
) -> str:
    """Write raw bytes to the durable media cache and return the workspace-relative path."""
    if len(raw_bytes) > MEDIA_CACHE_MAX_TOTAL_BYTES:
        return ""
    cache_path = media_cache_artifact_path(artifact_id)
    try:
        abs_path = Path(workspace.absolute_path(cache_path))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(raw_bytes)
        prune_cache_files(
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
        return Path(workspace.absolute_path(cache_path)).is_file()
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
    global _media_add_counter  # noqa: PLW0603
    path = media_registry_path()
    artifact_id = entry["artifact_id"]
    _media_add_counter += 1
    run_prune = _media_add_counter % _MEDIA_PRUNE_INTERVAL == 0
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
            return Path(workspace.absolute_path(cache_path)).read_bytes()
        except Exception:
            pass
    if source_path:
        try:
            return Path(workspace.absolute_path(source_path)).read_bytes()
        except Exception:
            pass
    return None


def _persist_media_session_entry(
    session: object,
    workspace: Workspace,
    meta: dict[str, str],
) -> None:
    """Upsert a resource-reference artifact into the persistent session media index."""
    global _media_add_counter  # noqa: PLW0603
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
    _media_add_counter += 1
    run_prune = _media_add_counter % _MEDIA_PRUNE_INTERVAL == 0
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
