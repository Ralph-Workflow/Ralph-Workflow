"""MediaSource — source data for a media artifact."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaSource:
    """Source data for a media artifact (at most one field is set)."""

    source_path: str = ""
    source_uri: str = ""
    raw_bytes: bytes | None = None


__all__ = ["MediaSource"]
