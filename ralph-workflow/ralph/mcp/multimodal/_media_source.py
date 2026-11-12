from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaSource:
    source_path: str = ""
    source_uri: str = ""
    raw_bytes: bytes | None = None


__all__ = ["MediaSource"]
