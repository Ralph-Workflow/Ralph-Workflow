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

wt-012: the preflight now commits its OWN writes (steps 3 + 4)
deterministically via the producer-level
:func:`ralph.project_policy._auto_commit.commit_policy_writes` helper
BEFORE the validator runs. Each pre-write content hash is recorded
BEFORE the bootstrap / seed step runs so a path that was already dirty
at HEAD (an agent edit in flight) is SKIPPED with a warning and never
enters the deterministic chore commit.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
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

    # wt-012: commit the preflight's own writes immediately, before the
    # validator runs. The pre-write content hash for every candidate
    # path is recorded BEFORE the bootstrap / seed step runs so a path
    # that was already dirty at HEAD (an agent edit in flight) is
    # SKIPPED with a warning and never enters the deterministic chore
    # commit. The commit step is BEST-EFFORT: a git error or missing
    # workspace cannot block the preflight, but its outcome is logged
    # so the operator can see the FAILED / SKIPPED status.
    candidate_paths = _candidate_policy_paths()
    pre_contents = _capture_pre_write_policy_paths(workspace, candidate_paths)
    changed_files = agents_md.bootstrap(workspace)
    seeded_files = _seed_missing_starters(workspace, stack)
    changed_files.extend(seeded_files)
    _commit_preflight_writes(workspace, pre_contents, changed_files + seeded_files)

    findings: list[PolicyFinding] = validators.validate_readiness(workspace, stack)
    migrated_sources = _collect_migrated_sources(workspace, findings)

    if not findings:
        # Cache write is owned by the OUTER boundary in
        # ``_finalize_ready_state`` (cli_integration.py) so the cached
        # signature is taken over the tree the run actually leaves
        # behind -- after ``agents_md.condense_placeholder_block`` and
        # the auto-commit. Writing here would race those post-READY
        # mutations and produce a stale cache signature that flunks
        # the very next preflight into a full re-validation.
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
    emit(f"project-policy-readiness: remediation-required ({len(findings)} findings)")
    return ReadinessResult(
        status=ReadinessStatus.REMEDIATION_REQUIRED,
        findings=findings,
        changed_files=changed_files,
        migrated_sources=migrated_sources,
        report_lines=_render_findings_report(findings),
    )


def _candidate_policy_paths() -> list[str]:
    """Return the policy surface paths the preflight could write.

    The pre-flight commit at the producer boundary commits ONLY paths
    in this set. The set is conservative (it covers every surface
    ``agents_md.bootstrap`` + ``_seed_missing_starters`` could
    touch); a post-write diff against the recorded pre-write
    hashes narrows it to the paths actually modified.
    """
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
    """Snapshot the pre-write git blob hash for every preflight candidate path.

    Uses :func:`ralph.git.scoped_auto_commit.capture_pre_write_contents`
    to record git blob hashes that are directly comparable to HEAD.
    A non-git workspace (the preflight must work in a fresh project
    that has not been ``git init``-ed yet) returns ``None`` for every
    path; the downstream commit helper will treat that as NOT_REPO and
    return a NOOP without raising.
    """
    from ralph.git.scoped_auto_commit import (  # noqa: PLC0415 -- lazy import avoids preflight<->scoped_auto_commit cycle
        capture_pre_write_contents,
    )

    repo_root = _workspace_root(workspace)
    if repo_root is None:
        return {path: None for path in candidate_paths}  # noqa: C420  # ruff prefers dict.fromkeys; mypy loses the literal type on it
    return capture_pre_write_contents(repo_root, candidate_paths)


def _workspace_root(workspace: Workspace) -> Path | None:
    """Return the on-disk workspace root, or ``None`` for a synthetic test seam.

    The preflight must work with the production :class:`FsWorkspace`
    (which carries a real ``root``) and the in-memory ``MemoryWorkspace``
    test seam (which does not). The :func:`capture_pre_write_contents`
    helper wants a ``Path``; fall back to a non-git result for the
    synthetic case so the preflight stays testable without touching
    real on-disk git state.
    """
    root: object = getattr(workspace, "root", None)
    if root is not None and isinstance(root, Path):
        return root
    return None


def _commit_preflight_writes(
    workspace: Workspace,
    pre_contents: dict[str, str | None],
    written_paths: list[str],
) -> None:
    """Commit the preflight's own writes via the producer-level helper.

    A no-op for any preflight run that did not actually write
    anything (e.g. a fully-cached READY that bypassed the bootstrap
    step). Failures (FAILED / NOT_REPO) are logged at DEBUG so they
    are visible without breaking the run.
    """
    from ralph.git.operations import (  # noqa: PLC0415 -- lazy import avoids preflight<->operations import cycle
        create_commit,
    )
    from ralph.git.scoped_auto_commit import ScopedCommitStatus  # noqa: PLC0415
    from ralph.project_policy._auto_commit import commit_policy_writes  # noqa: PLC0415

    repo_root = _workspace_root(workspace)
    if repo_root is None:
        return
    if not written_paths:
        return
    # Restrict the commit to the byte-exact intersection of
    # ``written_paths`` (the preflight's reported diff) and the
    # candidate set the pre-write hashes cover. A preflight
    # change to an unrelated path (defensive: should not happen
    # but the helper is safe either way) stays in the agent flow.
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
            from loguru import logger as _logger  # noqa: PLC0415

            _logger.debug("project-policy preflight auto-commit created: {}", result.sha)
        elif result.status is ScopedCommitStatus.SKIPPED and result.skipped_paths:
            from loguru import logger as _logger  # noqa: PLC0415

            _logger.debug(
                "project-policy preflight auto-commit skipped {} path(s) "
                "already dirty at HEAD; left for the agent flow",
                len(result.skipped_paths),
            )
        elif result.status is ScopedCommitStatus.FAILED:
            from loguru import logger as _logger  # noqa: PLC0415

            _logger.warning(
                "project-policy preflight auto-commit failed (non-fatal): {}",
                str(result.error) if result.error is not None else "<no error detail>",
            )
    except Exception as exc:  # pragma: no cover - defensive
        from loguru import logger as _logger  # noqa: PLC0415

        _logger.warning("project-policy preflight auto-commit failed (non-fatal): {}", exc)


def _seed_missing_starters(workspace: Workspace, stack: ProjectStack) -> list[str]:
    """Seed every missing core starter and every required conditional starter.

    Seeding does NOT make a file complete — the starter ships with the
    template banner, ``REPLACE-ME`` section comments, and unresolved
    ``RALPH-FACT`` placeholders, so the validator blocks readiness until
    the remediation agent resolves every one of them.

    Returns the list of newly-created starter paths.
    """
    seeded: list[str] = []
    frozen = any(
        workspace.exists(path) and "<!-- ralph-policy-schema: freeze " in workspace.read(path)
        for path in (
            f"{markers.CANONICAL_DIR}{name}"
            for name in (*markers.CORE_POLICY_FILES, *markers.CONDITIONAL_POLICY_FILES.values())
        )
    )
    if not frozen and starters.seed_starter_into(workspace, starters.PORTFOLIO_STARTER_NAME):
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
