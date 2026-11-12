from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.operations import create_commit
from ralph.git.scoped_auto_commit import (
    ScopedCommitStatus,
    capture_pre_write_contents,
)
from ralph.language_detector import get_project_stack
from ralph.project_policy import (
    agents_md,
    cache,
    evidence,
    markers,
    starters,
    validators,
)
from ralph.project_policy._auto_commit import commit_policy_writes
from ralph.project_policy.models import PolicyFinding, ReadinessResult, ReadinessStatus

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

REMEDIATION_PROMPT_REL_PATH: str = ".agent/tmp/policy_remediation_prompt.md"

EmitFn = Callable[[str], None]


def _noop_emit(message: str) -> None:
    pass


def run_policy_readiness_preflight(
    workspace: Workspace,
    stack: ProjectStack,
    *,
    emit: EmitFn = _noop_emit,
) -> ReadinessResult:
    if agents_md.is_opted_out(workspace):
        return ReadinessResult(
            status=ReadinessStatus.SKIPPED,
            report_lines=["project explicitly opted out"],
        )

    if cache.read_cached_ready(workspace, stack):
        cached_assessment, cached_outcomes = cache.read_cached_assessment(workspace, stack)
        return ReadinessResult(
            status=ReadinessStatus.READY,
            command_outcomes=cached_outcomes,
            assessment=cached_assessment,
            report_lines=["project-policy-readiness: ready (cached)"],
        )

    candidate_paths = _candidate_policy_paths()
    pre_contents = _capture_pre_write_policy_paths(workspace, candidate_paths)
    changed_files = seed_project_policy_starters(workspace, stack)
    _commit_preflight_writes(workspace, pre_contents, changed_files)

    findings: list[PolicyFinding] = validators.validate_readiness(workspace, stack)
    migrated_sources = _collect_migrated_sources(workspace, findings)

    blocking = [f for f in findings if f.blocking]
    if not blocking:
        return ReadinessResult(
            status=ReadinessStatus.READY,
            findings=findings,
            changed_files=changed_files,
            migrated_sources=migrated_sources,
            report_lines=[
                "project-policy-readiness: ready",
                f"changed_files: {changed_files}",
            ],
        )

    emit(f"project-policy-readiness: remediation-required ({len(blocking)} findings)")
    return ReadinessResult(
        status=ReadinessStatus.REMEDIATION_REQUIRED,
        findings=findings,
        changed_files=changed_files,
        migrated_sources=migrated_sources,
        report_lines=_render_findings_report(findings),
    )


def _candidate_policy_paths() -> list[str]:
    return [
        markers.AGENTS_MD,
        markers.CLAUDE_MD,
        *(
            f"{markers.CANONICAL_DIR}{name}"
            for name in (
                *markers.CORE_POLICY_FILES,
                *markers.CONDITIONAL_POLICY_FILES.values(),
                markers.PORTFOLIO_PATH.split("/")[-1],
            )
        ),
    ]


def _capture_pre_write_policy_paths(
    workspace: Workspace,
    candidate_paths: list[str],
) -> dict[str, str | None]:
    repo_root = _workspace_root(workspace)
    if repo_root is None:
        empty: dict[str, str | None] = dict.fromkeys(candidate_paths, None)
        return empty
    return capture_pre_write_contents(repo_root, candidate_paths)


def _workspace_root(workspace: Workspace) -> Path | None:
    root: object = getattr(workspace, "root", None)
    if root is not None and isinstance(root, Path):
        return root
    return None


def _commit_preflight_writes(
    workspace: Workspace,
    pre_contents: dict[str, str | None],
    written_paths: list[str],
) -> None:
    repo_root = _workspace_root(workspace)
    if repo_root is None:
        return
    if not written_paths:
        return
    covered = [path for path in written_paths if path in pre_contents]
    if not covered:
        return
    try:
        result = commit_policy_writes(
            repo_root,
            written_paths=covered,
            pre_contents={path: pre_contents[path] for path in covered},
            create_commit_fn=create_commit,
        )
        if result.status is ScopedCommitStatus.CREATED and result.sha:
            logger.debug("project-policy preflight auto-commit created: {}", result.sha)
        elif result.status is ScopedCommitStatus.SKIPPED and result.skipped_paths:
            logger.debug(
                "project-policy preflight auto-commit skipped {} path(s) "
                "already dirty at HEAD; left for the agent flow",
                len(result.skipped_paths),
            )
        elif result.status is ScopedCommitStatus.FAILED:
            logger.warning(
                "project-policy preflight auto-commit failed (non-fatal): {}",
                str(result.error) if result.error is not None else "<no error detail>",
            )
    except Exception as exc:
        logger.warning("project-policy preflight auto-commit failed (non-fatal): {}", exc)


def _seed_missing_starters(workspace: Workspace, stack: ProjectStack) -> list[str]:
    frozen = any(
        workspace.exists(path) and "<!-- ralph-policy-schema: freeze " in workspace.read(path)
        for path in (
            f"{markers.CANONICAL_DIR}{name}"
            for name in (*markers.CORE_POLICY_FILES, *markers.CONDITIONAL_POLICY_FILES.values())
        )
    )
    if frozen:
        return []
    seeded: list[str] = []
    if starters.seed_starter_into(workspace, starters.PORTFOLIO_STARTER_NAME):
        seeded.append(markers.PORTFOLIO_PATH)
    seeded.extend(
        f"{markers.CANONICAL_DIR}{name}"
        for name in markers.CORE_POLICY_FILES
        if starters.seed_starter_into(workspace, name)
    )
    requirements = evidence.conditional_domain_requirements(workspace, stack)
    for domain, name in markers.CONDITIONAL_POLICY_FILES.items():
        required, _ = requirements[domain]
        if required and starters.seed_starter_into(workspace, name):
            seeded.append(f"{markers.CANONICAL_DIR}{name}")
    return seeded


def _collect_migrated_sources(workspace: Workspace, findings: list[PolicyFinding]) -> list[str]:
    migration_id_prefix = markers.ID_MIGRATE
    return [
        finding.path
        for finding in findings
        if finding.requirement_id.startswith(migration_id_prefix)
    ]


def _render_findings_report(findings: list[PolicyFinding]) -> list[str]:
    return [
        (
            f"  - {finding.requirement_id}  path={finding.path}\n"
            f"      missing: {finding.missing_evidence}\n"
            f"      fix:     {finding.required_outcome}"
        )
        for finding in findings
    ]


def seed_project_policy_starters(
    workspace: Workspace,
    stack: ProjectStack | None = None,
) -> list[str]:
    if agents_md.is_opted_out(workspace):
        return []
    if stack is None:
        stack = get_project_stack(workspace)
    changed = agents_md.bootstrap(workspace)
    seeded = _seed_missing_starters(workspace, stack)
    return [*changed, *seeded]


__all__ = [
    "REMEDIATION_PROMPT_REL_PATH",
    "run_policy_readiness_preflight",
    "seed_project_policy_starters",
]
