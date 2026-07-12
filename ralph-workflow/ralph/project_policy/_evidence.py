"""EvidenceEntry dataclass.

A single ``(path, exists, sha256)`` atom in the shared readiness-evidence
inventory. Captured once per preflight and consumed by BOTH the validator
(to know which files to read) and the cache (to know when a cached READY is
stale). ``content_sha256`` is ``None`` when the file does not exist so
DELETIONS change the signature too — a cached READY becomes stale the
instant any of its evidence files is removed.

The shared inventory (see :mod:`ralph.project_policy.evidence`) is the
single source of cache + validator hashing; this dataclass is its atom.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceEntry:
    """A single ``(path, exists, sha256)`` atom in the readiness-evidence inventory.

    Attributes:
        rel_path: Workspace-relative path.
        exists: True when the file exists at scan time.
        content_sha256: Lowercase hex SHA-256 of the file content when
            ``exists`` is True; ``None`` when ``exists`` is False.
    """

    rel_path: str
    exists: bool
    content_sha256: str | None = None

    def to_dict(self) -> dict[str, str | bool | None]:
        """Return a JSON-serializable dict representation."""
        return {
            "rel_path": self.rel_path,
            "exists": self.exists,
            "content_sha256": self.content_sha256,
        }


__all__ = ["EvidenceEntry"]
