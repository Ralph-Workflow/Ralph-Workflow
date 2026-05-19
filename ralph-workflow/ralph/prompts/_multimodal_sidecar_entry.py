from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MultimodalSidecarEntry:
    """A single multimodal artifact entry in the prompt-to-invoke handoff sidecar."""

    artifact_id: str
    uri: str
    mime_type: str
    title: str
    modality: str
    delivery: str
    reason: str = ""
    source_path: str = ""
    cache_path: str = ""
    source_uri: str = ""
    block_type: str = ""
    failure_kind: str = ""
    identity_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "uri": self.uri,
            "mime_type": self.mime_type,
            "title": self.title,
            "modality": self.modality,
            "delivery": self.delivery,
            "reason": self.reason,
            "source_path": self.source_path,
            "cache_path": self.cache_path,
            "source_uri": self.source_uri,
            "block_type": self.block_type,
            "failure_kind": self.failure_kind,
            "identity_key": self.identity_key,
        }
