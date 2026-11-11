
from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.multimodal._failure_kind import MultimodalFailureKind


@dataclass(frozen=True)
class MultimodalFailure:

    kind: MultimodalFailureKind
    message: str
    modality: str | None = None
    provider: str | None = None
    model_id: str | None = None

    def user_message(self) -> str:
        parts = [self.message]
        if self.modality:
            parts.append(f"modality: {self.modality}")
        if self.provider:
            parts.append(f"provider: {self.provider}")
        if self.model_id:
            parts.append(f"model: {self.model_id}")
        return " | ".join(parts)


__all__ = [
    "MultimodalFailure",
    "MultimodalFailureKind",
]
