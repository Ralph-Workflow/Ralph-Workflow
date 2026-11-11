
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceEntry:

    rel_path: str
    exists: bool
    content_sha256: str | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "rel_path": self.rel_path,
            "exists": self.exists,
            "content_sha256": self.content_sha256,
        }


__all__ = ["EvidenceEntry"]
