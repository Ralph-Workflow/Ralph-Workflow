from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InteractiveTranscriptEvent:
    kind: str
    text: str
    metadata: dict[str, object] = field(default_factory=dict)
