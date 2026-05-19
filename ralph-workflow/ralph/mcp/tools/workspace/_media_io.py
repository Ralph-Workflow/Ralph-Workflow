"""Media artifact I/O: cache writing, registry persistence, and byte loading."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.prompts.debug_dump import (
    media_cache_artifact_path,
    media_registry_path,
    media_session_path,
)

if TYPE_CHECKING:
    from ralph.workspace import Workspace

_MEDIA_SESSION_SCHEMA_VERSION = "2"


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
    cache_path = media_cache_artifact_path(artifact_id)
    try:
        abs_path = Path(workspace.absolute_path(cache_path))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(raw_bytes)
    except Exception:
        return ""
    return cache_path


def _persist_media_registry_entry(
    workspace: Workspace,
    entry: dict[str, str],
) -> None:
    """Write entry to the centralized media registry for cross-session lookup."""
    path = media_registry_path()
    artifact_id = entry["artifact_id"]
    try:
        artifacts: list[dict[str, str]] = []
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts = list(raw_artifacts) if isinstance(raw_artifacts, list) else []
        except Exception:
            artifacts = []
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
    try:
        try:
            data: dict[str, object] = json.loads(workspace.read(path))
            raw_artifacts = data.get("artifacts", [])
            artifacts: list[dict[str, str]] = (
                list(raw_artifacts) if isinstance(raw_artifacts, list) else []
            )
        except Exception:
            artifacts = []

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
