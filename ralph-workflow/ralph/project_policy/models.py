"""Project-policy readiness models.

Pure data layer for the project-policy-readiness preflight. This module
defines:

* :class:`ReadinessStatus` — the four canonical outcome states.
* :class:`PolicyFinding` — a single deterministic validation finding. Each
  finding carries a stable ``requirement_id`` (one of the ``ID_*`` prefixes
  defined in :mod:`ralph.project_policy.markers`), a path, the missing
  evidence, and the required remediation outcome.
* :class:`EvidenceEntry` — a single ``(path, exists, sha256)`` atom in the
  shared readiness-evidence inventory. ``content_sha256`` is ``None`` when
  the file does not exist so deletions change the signature too.
* :class:`MigrationCandidate` — a doc that the migration detector flagged as
  policy-like content; ``resolved`` is True when the doc carries the
  exact migrated marker or has had its recognized headings removed.
* :class:`ReadinessResult` — the orchestrator's return value. Holds the
  status, the findings (empty on READY), the changed files, the migrated
  sources, and the report lines for display.

No I/O. No AI. No network. All models are deterministic data carriers that
the validator, the cache, the orchestrator, the remediation driver, and the
display layer thread through a single shared contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ReadinessStatus(StrEnum):
    """Canonical outcome states of the readiness preflight.

    The string values are the public display labels (used by the CLI status
    line and by external reports). Keep the values stable — they are part of
    the wire contract for any downstream tooling that reads the cache or the
    preflight report.
    """

    READY = "ready"
    SKIPPED = "skipped"
    REMEDIATION_REQUIRED = "remediation-required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PolicyFinding:
    """A single deterministic validation finding.

    Attributes:
        requirement_id: Stable ``RWP-*`` identifier. The validator emits
            exactly one finding per ``(requirement_id, path)`` pair so the
            remediation agent can address findings deterministically.
        path: Workspace-relative path of the affected file or concern (e.g.
            ``docs/ralph-workflow-policy/testing-policy.md``).
        missing_evidence: Short, human-readable description of what is
            missing or invalid. Surfaced directly in the BLOCKED report and
            the remediation prompt.
        required_outcome: What an agent must do to clear the finding.
    """

    requirement_id: str
    path: str
    missing_evidence: str
    required_outcome: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable dict representation."""
        return {
            "requirement_id": self.requirement_id,
            "path": self.path,
            "missing_evidence": self.missing_evidence,
            "required_outcome": self.required_outcome,
        }


@dataclass(frozen=True)
class EvidenceEntry:
    """A single ``(path, exists, sha256)`` atom in the readiness-evidence inventory.

    Captured once per preflight and consumed by BOTH the validator (to know
    which files to read) and the cache (to know when a cached READY is
    stale). ``content_sha256`` is ``None`` when the file does not exist so
    DELETIONS change the signature too — a cached READY becomes stale the
    instant any of its evidence files is removed.

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


@dataclass
class ReadinessResult:
    """The orchestrator's return value.

    Attributes:
        status: The canonical outcome state. Drives the orchestrator's
            control flow and the CLI display.
        findings: Validation findings; empty when status is READY.
        changed_files: Files actually created or modified during the
            preflight (bootstrap, starter seeding). Surfaced in the report.
        migrated_sources: Candidate files reconciled during the preflight.
        commands_run: Verification commands the orchestrator executed (if
            any). Useful for the BLOCKED report.
        report_lines: Pre-rendered report lines for display.
    """

    status: ReadinessStatus
    findings: list[PolicyFinding] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    migrated_sources: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    report_lines: list[str] = field(default_factory=list)

    def is_ready(self) -> bool:
        """Return True when the project is fully policy-ready."""
        return self.status is ReadinessStatus.READY

    def is_skipped(self) -> bool:
        """Return True when the user explicitly opted out."""
        return self.status is ReadinessStatus.SKIPPED

    def requires_remediation(self) -> bool:
        """Return True when an agent run is required before planning can begin."""
        return self.status is ReadinessStatus.REMEDIATION_REQUIRED

    def is_blocked(self) -> bool:
        """Return True when remediation exhausted its budget without a READY."""
        return self.status is ReadinessStatus.BLOCKED

    def to_report(self) -> list[str]:
        """Build a concise, per-finding report.

        Each line carries the requirement id, path, missing evidence, and
        required outcome so the BLOCKED report is actionable. The list
        starts with the status line and ends with a change summary.
        """
        if self.report_lines:
            return list(self.report_lines)
        lines: list[str] = [
            f"project-policy-readiness: {self.status.value}",
        ]
        if self.findings:
            lines.append(f"findings: {len(self.findings)}")
            lines.extend(
                (
                    f"  - {finding.requirement_id}  path={finding.path}\n"
                    f"      missing: {finding.missing_evidence}\n"
                    f"      fix:     {finding.required_outcome}"
                )
                for finding in self.findings
            )
        if self.changed_files:
            lines.append(f"changed_files: {self.changed_files}")
        if self.migrated_sources:
            lines.append(f"migrated_sources: {self.migrated_sources}")
        if self.commands_run:
            lines.append(f"commands_run: {self.commands_run}")
        return lines


__all__ = [
    "EvidenceEntry",
    "MigrationCandidate",
    "PolicyFinding",
    "ReadinessResult",
    "ReadinessStatus",
]
