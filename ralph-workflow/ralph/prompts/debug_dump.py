"""Helpers for persisting rendered prompts for debugging."""

from __future__ import annotations

import json
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.prompts._multimodal_sidecar_entry import MultimodalSidecarEntry

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace


def _normalized_phase(phase: str) -> str:
    return phase.replace("/", "_").replace(" ", "_")


def prompt_dump_path(phase: str) -> str:
    """Return the workspace-relative path for a phase's debug prompt dump."""
    return f".agent/tmp/{_normalized_phase(phase)}_prompt.md"


def worker_prompt_dump_path(worker_namespace: Path, phase: str) -> Path:
    """Return the worker-local prompt dump path for a phase."""
    return worker_namespace / "tmp" / f"{_normalized_phase(phase)}_prompt.md"


def multimodal_sidecar_path(phase: str) -> str:
    """Return the workspace-relative path for a phase's multimodal handoff sidecar."""
    return f".agent/tmp/{_normalized_phase(phase)}_multimodal_handoff.json"


def worker_multimodal_sidecar_path(worker_namespace: Path, phase: str) -> Path:
    """Return the worker-local multimodal handoff sidecar path for a phase."""
    return worker_namespace / "tmp" / f"{_normalized_phase(phase)}_multimodal_handoff.json"


def media_session_path(phase: str) -> str:
    """Path for the persistent media session index written by the MCP server.

    This file accumulates artifact metadata for each media file loaded during
    a session via read_media. The runner reads it at the next prompt
    materialization to carry media context forward across sessions.
    """
    return f".agent/tmp/{_normalized_phase(phase)}_media_session.json"


def media_registry_path() -> str:
    """Path for the centralized media artifact registry.

    Maps artifact_id to full v2 metadata for cross-session replay lookup.
    """
    return ".agent/tmp/media_registry.json"


def media_cache_artifact_path(artifact_id: str) -> str:
    """Path for the durable byte cache of a media artifact.

    Bytes written here survive the session and enable cross-session replay.
    """
    return f".agent/tmp/media/{artifact_id}"


_SIDECAR_SCHEMA_VERSION = "2"


def _sidecar_entry_identity(entry: MultimodalSidecarEntry) -> str:
    if entry.identity_key:
        return entry.identity_key
    if entry.source_uri:
        return f"source-uri:{entry.modality}:{entry.source_uri}"
    if entry.source_path:
        return f"source-path:{entry.modality}:{entry.source_path}"
    return f"artifact-id:{entry.artifact_id or entry.uri}"


def write_multimodal_sidecar(
    workspace: Workspace,
    phase: str,
    entries: list[MultimodalSidecarEntry],
    *,
    worker_namespace: Path | None = None,
) -> None:
    """Persist the phase multimodal handoff sidecar for shared or worker-local prompts."""
    path = (
        str(worker_multimodal_sidecar_path(worker_namespace, phase))
        if worker_namespace is not None
        else multimodal_sidecar_path(phase)
    )
    payload = {
        "schema_version": _SIDECAR_SCHEMA_VERSION,
        "phase": phase,
        "artifacts": [entry.to_dict() for entry in entries],
    }
    workspace.write(path, json.dumps(payload, indent=2))


def clear_multimodal_sidecar(
    workspace: Workspace,
    phase: str,
    *,
    worker_namespace: Path | None = None,
) -> None:
    """Remove the multimodal handoff sidecar for a shared or worker-local prompt."""
    path = (
        str(worker_multimodal_sidecar_path(worker_namespace, phase))
        if worker_namespace is not None
        else multimodal_sidecar_path(phase)
    )
    with suppress(Exception):
        workspace.remove(path)


def collect_media_entries_for_phase(
    workspace: Workspace,
    phase: str,
) -> list[MultimodalSidecarEntry]:
    """Read media entries from the persistent session index for a phase."""
    path = media_session_path(phase)
    try:
        raw = workspace.read(path)
    except Exception:
        return []
    try:
        data: dict[str, object] = json.loads(raw)
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            return []
        entries: OrderedDict[str, MultimodalSidecarEntry] = OrderedDict()
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            try:
                entry = MultimodalSidecarEntry(
                    artifact_id=str(item.get("artifact_id", "")),
                    uri=str(item.get("uri", "")),
                    mime_type=str(item.get("mime_type", "")),
                    title=str(item.get("title", "")),
                    modality=str(item.get("modality", "")),
                    delivery=str(item.get("delivery", "resource_reference_replay")),
                    reason=str(item.get("reason", "")),
                    source_path=str(item.get("source_path", "")),
                    cache_path=str(item.get("cache_path", "")),
                    source_uri=str(item.get("source_uri", "")),
                    block_type=str(item.get("block_type", "")),
                    failure_kind=str(item.get("failure_kind", "")),
                    identity_key=str(item.get("identity_key", "")),
                )
            except Exception:
                continue
            entries[_sidecar_entry_identity(entry)] = entry
        return list(entries.values())
    except Exception:
        return []


def dump_rendered_prompt(
    workspace: Workspace,
    phase: str,
    prompt: str,
    *,
    worker_namespace: Path | None = None,
) -> str:
    """Write the rendered prompt to the debug dump path and return the path."""
    path = (
        worker_prompt_dump_path(worker_namespace, phase)
        if worker_namespace is not None
        else Path(prompt_dump_path(phase))
    )
    workspace.write(str(path), prompt)
    return str(path)
