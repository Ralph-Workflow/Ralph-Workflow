"""MigrationCandidate dataclass.

A doc the migration detector flagged as containing policy-like content
based on the exact heading recognizer. ``resolved`` is True when the doc
either carries the byte-exact migrated marker (pointing at a canonical
file that exists) or no longer contains any recognized heading.
Unresolved candidates generate RWP-MIGRATE-UNRECONCILED findings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MigrationCandidate:
    """A doc flagged by the migration detector.

    Attributes:
        path: Workspace-relative path of the candidate file.
        recognized_heading: The lowercased heading phrase that caused the
            file to be classified as a migration candidate.
        resolved: True when the file carries the exact migrated marker
            (pointing at a canonical file that exists) or no longer
            contains any recognized heading. Unresolved candidates generate
            RWP-MIGRATE-UNRECONCILED findings.
    """

    path: str
    recognized_heading: str
    resolved: bool


__all__ = ["MigrationCandidate"]
