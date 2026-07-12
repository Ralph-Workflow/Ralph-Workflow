"""Deterministic project-policy-readiness preflight orchestrator.

The orchestrator sequences the five deterministic steps:

1. Opt-out check (byte-exact marker in AGENTS.md).
2. Fast-path cache lookup (change-aware READY cache).
3. AGENTS.md / CLAUDE.md bootstrap (idempotent).
4. Bundle-starter seeding for every missing core file (and for any
   required conditional file).
5. Deterministic validator (returns ``[]`` on READY, list of findings
   otherwise).

The readiness decision is purely deterministic — no AI is consulted. The
remediation driver (see :mod:`ralph.project_policy.remediation`) is invoked
by the run-loop ONLY when this orchestrator returns REMEDIATION_REQUIRED.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ralph.project_policy import (
    agents_md,
    cache,
    evidence,
    markers,
    starters,
    validators,
)
from ralph.project_policy.models import PolicyFinding, ReadinessResult, ReadinessStatus

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

#: Filename of the materialized remediation prompt. The orchestrator does
#: NOT write this file; :func:`ralph.project_policy.remediation.remediate`
#: owns the prompt materialization.
REMEDIATION_PROMPT_REL_PATH: str = ".agent/tmp/policy_remediation_prompt.md"

EmitFn = Callable[[str], None]


def _noop_emit(message: str) -> None:
    """Default emit callback used when no display is injected."""


def run_policy_readiness_preflight(
    workspace: Workspace,
    stack: ProjectStack,
    *,
    emit: EmitFn = _noop_emit,
) -> ReadinessResult:
    """Run the deterministic preflight and return a :class:`ReadinessResult`.

    Args:
        workspace: Injected workspace seam.
        stack: Detected project stack (from
            :func:`ralph.language_detector.get_project_stack`).
        emit: Display callback. The orchestrator emits short, one-line
            status messages here. Defaults to a no-op so the orchestrator
            is usable without a display.

    Returns:
        A :class:`ReadinessResult` whose ``status`` is one of:

        * :attr:`ReadinessStatus.READY` — every check passed.
        * :attr:`ReadinessStatus.SKIPPED` — the byte-exact opt-out marker
          is present; no policy writes occurred.
        * :attr:`ReadinessStatus.REMEDIATION_REQUIRED` — one or more
          findings; an agent must reconcile them. The findings,
          changed_files, migrated_sources, commands_run, and report_lines
          carry the actionable detail.
    """
    if agents_md.is_opted_out(workspace):
        # SKIPPED: do NOT emit here. The run-loop owns the single brief status
        # line for ready/skipped states so the user sees exactly one message
        # per preflight outcome (per the AC-14 reporting contract).
        return ReadinessResult(
            status=ReadinessStatus.SKIPPED,
            report_lines=["project explicitly opted out"],
        )

    if cache.read_cached_ready(workspace, stack):
        # READY (cached): no emit here either; run-loop owns the brief line.
        return ReadinessResult(
            status=ReadinessStatus.READY,
            report_lines=["project-policy-readiness: ready (cached)"],
        )

    changed_files = agents_md.bootstrap(workspace)
    seeded_files = _seed_missing_starters(workspace, stack)
    changed_files.extend(seeded_files)

    findings: list[PolicyFinding] = validators.validate_readiness(workspace, stack)
    migrated_sources = _collect_migrated_sources(workspace, findings)

    if not findings:
        cache.write_cache(workspace, stack, ReadinessStatus.READY)
        # READY (after work): run-loop owns the single brief line.
        return ReadinessResult(
            status=ReadinessStatus.READY,
            changed_files=changed_files,
            migrated_sources=migrated_sources,
            report_lines=[
                "project-policy-readiness: ready",
                f"changed_files: {changed_files}",
            ],
        )

    # REMEDIATION_REQUIRED: emit here because remediation follows and the
    # run-loop needs to know the count.
    emit(
        f"project-policy-readiness: remediation-required ({len(findings)} findings)"
    )
    return ReadinessResult(
        status=ReadinessStatus.REMEDIATION_REQUIRED,
        findings=findings,
        changed_files=changed_files,
        migrated_sources=migrated_sources,
        report_lines=_render_findings_report(findings),
    )


def _seed_missing_starters(workspace: Workspace, stack: ProjectStack) -> list[str]:
    """Seed every missing core starter and every required conditional starter.

    Seeding does NOT make a file complete — the starter ships without the
    completion marker, contains ``RALPH-FACT`` placeholders, and lacks
    project-specific commands. The remediation agent is the only owner of
    the completion marker.

    Returns the list of newly-created starter paths.
    """
    seeded: list[str] = [
        f"{markers.CANONICAL_DIR}{name}"
        for name in markers.CORE_POLICY_FILES
        if starters.seed_starter_into(workspace, name)
    ]
    ds_required, _ = evidence.design_system_required(workspace, stack)
    ux_required, _ = evidence.ux_required(workspace, stack)
    perf_required, _ = evidence.performance_required(workspace, stack)
    mem_required, _ = evidence.memory_required(workspace, stack)
    if ds_required:
        name = markers.CONDITIONAL_POLICY_FILES["design-system"]
        if starters.seed_starter_into(workspace, name):
            seeded.append(f"{markers.CANONICAL_DIR}{name}")
    if ux_required:
        name = markers.CONDITIONAL_POLICY_FILES["ux"]
        if starters.seed_starter_into(workspace, name):
            seeded.append(f"{markers.CANONICAL_DIR}{name}")
    if perf_required:
        name = markers.CONDITIONAL_POLICY_FILES["performance"]
        if starters.seed_starter_into(workspace, name):
            seeded.append(f"{markers.CANONICAL_DIR}{name}")
    if mem_required:
        name = markers.CONDITIONAL_POLICY_FILES["memory-usage"]
        if starters.seed_starter_into(workspace, name):
            seeded.append(f"{markers.CANONICAL_DIR}{name}")
    return seeded


def _collect_migrated_sources(
    workspace: Workspace, findings: list[PolicyFinding]
) -> list[str]:
    """Return the list of files referenced by migration findings."""
    migration_id_prefix = markers.ID_MIGRATE
    return [
        finding.path
        for finding in findings
        if finding.requirement_id.startswith(migration_id_prefix)
    ]


def _render_findings_report(findings: list[PolicyFinding]) -> list[str]:
    """Render the per-finding lines used in the BLOCKED report."""
    return [
        (
            f"  - {finding.requirement_id}  path={finding.path}\n"
            f"      missing: {finding.missing_evidence}\n"
            f"      fix:     {finding.required_outcome}"
        )
        for finding in findings
    ]


__all__ = ["REMEDIATION_PROMPT_REL_PATH", "run_policy_readiness_preflight"]
