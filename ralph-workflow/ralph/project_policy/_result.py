"""ReadinessResult dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.project_policy._status import ReadinessStatus

if TYPE_CHECKING:
    from ralph.project_policy._command_outcome import CommandOutcome
    from ralph.project_policy._findings import PolicyFinding
    from ralph.project_policy._readiness_assessment import ReadinessAssessment


@dataclass
class ReadinessResult:
    """The orchestrator's return value."""

    status: ReadinessStatus
    findings: list[PolicyFinding] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    migrated_sources: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    report_lines: list[str] = field(default_factory=list)
    # Optional: the strict execution-evidence ledger ingested from the
    # analysis-phase artifact. Empty tuple when no ledger is supplied
    # (the default) so existing call sites that never emit the section
    # keep working unchanged.
    command_outcomes: tuple[CommandOutcome, ...] = field(default_factory=tuple)
    # Optional: the three-dimension readiness assessment. ``None`` means
    # no assessment was computed for this run; an explicit all-false
    # :class:`ReadinessAssessment` is what a run that DID compute one
    # reports when every dimension is unverified.
    assessment: ReadinessAssessment | None = None

    def is_ready(self) -> bool:
        return self.status is ReadinessStatus.READY

    def is_skipped(self) -> bool:
        return self.status is ReadinessStatus.SKIPPED

    def requires_remediation(self) -> bool:
        return self.status is ReadinessStatus.REMEDIATION_REQUIRED

    def is_blocked(self) -> bool:
        return self.status is ReadinessStatus.BLOCKED

    def to_report(self) -> list[str]:
        if self.report_lines:
            return list(self.report_lines)
        lines: list[str] = [f"project-policy-readiness: {self.status.value}"]
        if self.assessment is not None:
            lines.append(
                "readiness-assessment: "
                + ", ".join(
                    f"{name}={'true' if value else 'false'}"
                    for name, value in self.assessment.as_dict().items()
                )
            )
        if self.command_outcomes:
            lines.append(f"command_outcomes: {len(self.command_outcomes)}")
        if self.findings:
            lines.append(f"findings: {len(self.findings)}")
            for finding in self.findings:
                lines.append(f"  - {finding.requirement_id}  path={finding.path}")
                lines.append(f"      missing: {finding.missing_evidence}")
                lines.append(f"      fix:     {finding.required_outcome}")
        if self.changed_files:
            lines.append(f"changed_files: {self.changed_files}")
        if self.migrated_sources:
            lines.append(f"migrated_sources: {self.migrated_sources}")
        if self.commands_run:
            lines.append(f"commands_run: {self.commands_run}")
        return lines


__all__ = ["ReadinessResult"]
