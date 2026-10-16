"""Supervised inspection when retained integration lacks a completion receipt.

The diagnostic path is the single authority that decides whether the
retained-integration evidence is still trustworthy. The architecture
fixes the linked-worktree reproduction by:

* reading ONLY operation-scoped Git state through the
  :class:`OperationEvidenceCollector` (no ``git for-each-ref``, no
  full ``git ls-files --stage``, no branch reflog, no
  ``logs/HEAD``);
* bounding the diagnostic-repair attempt loop at
  :data:`DIAGNOSTIC_REPAIR_ATTEMPT_CAP` so an over-cap handoff
  emits the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition and stops
  calling agents;
* routing every ``write_record(... model_copy(...))`` call site
  through the closed :class:`Transition` table on
  :class:`IntegrationRecord.with_transition`;
* routing every clear of ``diagnostic_evidence`` /
  ``diagnostic_ownership`` through the
  ``RELEASE_DIAGNOSTIC_EVIDENCE`` transition.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline._integration_action import (
    IntegrationAction,
    last_outcome_reason,
    next_integration_action,
)
from ralph.pipeline.auto_integrate_record import (
    BUMP_DIAGNOSTIC_ATTEMPT,
    HOLD_DIAGNOSTIC_EVIDENCE,
    RAISE_DIAGNOSTIC_INTERVENTION,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordUnreadableError,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    OperationEvidenceCollector,
    OperationFact,
    SubprocessOperationEvidenceCollector,
    ownership_fingerprint,
)
from ralph.pipeline.operation_fact import (
    _KIND_MERGE_OBJECT_RESOLVABLE,
    _KIND_REBASE_OBJECT_RESOLVABLE,
    _UNREADABLE,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline.run_loop import _LoopContext


# Default collector used by the diagnostic path. Tests inject
# ``InMemoryOperationEvidenceCollector`` (or a custom subclass) to
# exercise the diagnostic without depending on the host's ``git``
# binary or filesystem layout.
_OPERATION_EVIDENCE_COLLECTOR: list[OperationEvidenceCollector] = [
    SubprocessOperationEvidenceCollector()
]

# Default repair-attempt cap. The diagnostic path rotates inspection
# agents on every handoff, but the rotation must terminate: an
# over-cap handoff emits the ``RAISE_DIAGNOSTIC_INTERVENTION``
# transition and stops calling agents. Operators can raise the cap
# via ``conflict_resolution.diagnostic_repair_attempt_cap`` in
# policy configuration; the default is intentionally low so the
# production loop is broken on the very first incident.
DIAGNOSTIC_REPAIR_ATTEMPT_CAP: int = 5


def set_operation_evidence_collector(
    collector: OperationEvidenceCollector | None,
) -> None:
    """Inject (or clear) the diagnostic path's evidence collector.

    Used by tests that need an in-memory fake. ``None`` resets the
    default subprocess collector.
    """
    _OPERATION_EVIDENCE_COLLECTOR[0] = (
        collector if collector is not None else SubprocessOperationEvidenceCollector()
    )


def operation_evidence_collector() -> OperationEvidenceCollector:
    """Return the diagnostic path's currently-configured collector."""
    return _OPERATION_EVIDENCE_COLLECTOR[0]


def _raise_intervention(record: IntegrationRecord, reason: str) -> IntegrationRecord:
    """Emit the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition for ``record``.

    Single authority for the operator-intervention emission.
    """
    return record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason=reason,
    )


def _bump_attempt(
    record: IntegrationRecord,
    exc: str,
    *,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    cap_reason: str = "diagnostic repair exhausted; operator intervention required",
) -> IntegrationRecord:
    """Emit ``BUMP_DIAGNOSTIC_ATTEMPT`` and enforce ``DIAGNOSTIC_REPAIR_ATTEMPT_CAP``.

    Single authority for the attempt-counter emission: when the bumped
    count reaches the cap, the returned record additionally carries the
    ``RAISE_DIAGNOSTIC_INTERVENTION`` transition with ``cap_reason``.
    """
    bumped = record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=record.merge_commit_repair_attempts + 1,
        repair_last_error=exc,
    )
    if bumped.merge_commit_repair_attempts >= repair_attempt_cap:
        return _raise_intervention(bumped, cap_reason)
    return bumped


def _evidence_to_facts(
    raw: tuple[tuple[str, str], ...],
) -> tuple[OperationFact, ...]:
    """Reverse :func:`_record_to_evidence` for the verify path.

    Best-effort: an unknown key is preserved as a
    :class:`OperationFact` with ``kind="unknown"`` so the verify
    path can still detect a non-operation-scoped drift without
    raising on parse.
    """
    facts: list[OperationFact] = []
    for key, value in raw:
        if ":" in key:
            kind, scope = key.split(":", 1)
            facts.append(OperationFact(kind=kind, value=value, scope=scope))
        else:
            facts.append(OperationFact(kind=key, value=value))
    return tuple(facts)


def diagnostic_needed(root: Path, record: IntegrationRecord) -> bool:
    from ralph.git.merge import MERGE_STATE_NONE, MERGE_STATE_UNKNOWN, merge_state, unmerged_paths
    from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
    from ralph.pipeline._retained_resolution_scope import original_conflict_paths

    if record.diagnostic_evidence:
        return True
    operation_root = Path(record.owning_worktree) if record.owning_worktree else root
    try:
        if classify_git_operation(record) is GitOperationKind.AWAITING_CONTINUE:
            from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches

            return not pending_rebase_identity_matches(operation_root, record)
        if classify_git_operation(record) is GitOperationKind.AWAITING_MERGE_COMMIT:
            from ralph.pipeline._pending_merge_commit import pending_merge_identity_matches

            return not pending_merge_identity_matches(operation_root, record)
        unmerged = unmerged_paths(operation_root)
        unreadable = "<unmerged-path-query-failed>" in unmerged
        missing_scope = (record.resolving_merge or record.resolving_rebase) and not (
            record.resolving_paths or unmerged or original_conflict_paths(operation_root)
        )
        if unreadable or missing_scope:
            return True
        merge = merge_state(operation_root)
        return merge == MERGE_STATE_UNKNOWN or (
            merge == MERGE_STATE_NONE and not rebase_in_progress_at(operation_root)
        )
    except Exception:
        return True


def diagnose_retained_integration(
    ctx: _LoopContext,
    failure: str,
    *,
    invoke: Callable[[str, Path], bool] | None = None,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    """Inspect missing evidence under both worktree leases; never accept agent success as proof."""
    from contextlib import ExitStack

    from ralph.pipeline.auto_integrate_record import read_record
    from ralph.pipeline.auto_integrate_transaction import integration_transaction
    from ralph.pipeline.conflict_resolution.session import (
        invoke_resolution_agent,
        resolution_chain_agents,
    )
    from ralph.workspace.context import workspace_context
    from ralph.workspace.scope import WorkspaceScope

    pipeline_deps = ctx.pipeline_deps
    if pipeline_deps is None:
        logger.critical("Integration evidence inspection requires agent dependencies: {}", failure)
        return False
    with ExitStack() as leases:
        if (
            not leases.enter_context(integration_transaction(ctx.workspace_scope.root))
            or (record := read_record(ctx.workspace_scope.root)) is None
        ):
            return False
        if (
            record.diagnostic_intervention_required
            or record.merge_commit_repair_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
        ):
            if not record.diagnostic_intervention_required:
                write_record(
                    ctx.workspace_scope.root,
                    _raise_intervention(
                        record,
                        "diagnostic repair exhausted; operator intervention required",
                    ),
                )
            return False

        operation_root = (
            Path(record.owning_worktree)
            if (
                record.operation_kind == "target_reconcile"
                and record.owning_worktree
                and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
            )
            else ctx.workspace_scope.root
        )
        if operation_root != ctx.workspace_scope.root and not leases.enter_context(
            integration_transaction(operation_root)
        ):
            return False

        effective_invoke: Callable[[str, Path], bool]
        if invoke is None:

            def _default_invoke(agent: str, prompt: Path) -> bool:
                with workspace_context(operation_root):
                    return invoke_resolution_agent(
                        agent_name=agent,
                        prompt_path=prompt,
                        config=ctx.config,
                        pipeline_deps=pipeline_deps,
                        workspace_scope=WorkspaceScope(operation_root),
                        policy_bundle=ctx.policy_bundle,
                        display=ctx.active_display,
                        display_context=ctx.display_context,
                        operator_cap_seconds=ctx.config.conflict_resolution.total_resolution_cap_seconds,
                        require_completion_evidence=True,
                    )

            effective_invoke = _default_invoke
        else:
            effective_invoke = invoke

        try:
            col = collector if collector is not None else operation_evidence_collector()
            before = record.diagnostic_evidence or tuple(
                fact.to_evidence_tuple() for fact in col.snapshot(operation_root, record)
            )
            if not record.diagnostic_evidence or not record.diagnostic_ownership:
                record = record.with_transition(
                    HOLD_DIAGNOSTIC_EVIDENCE,
                    diagnostic_evidence=before,
                    diagnostic_ownership=ownership_fingerprint(record),
                )
                write_record(ctx.workspace_scope.root, record)
            released = handoff_integration_diagnostic(
                root=ctx.workspace_scope.root,
                record=record,
                failure=failure,
                agents=tuple(
                    agent
                    for agent in resolution_chain_agents(ctx.policy_bundle)
                    if ctx.registry.get(agent) is not None
                ),
                invoke=effective_invoke,
                collector=col,
            )
            after = tuple(fact.to_evidence_tuple() for fact in col.snapshot(operation_root, record))
            if after != before:
                logger.critical(
                    "Integration diagnostic changed protected Git evidence; landing withheld"
                )
            retained = read_record(ctx.workspace_scope.root)
            if retained is None or ownership_fingerprint(retained) != record.diagnostic_ownership:
                write_record(ctx.workspace_scope.root, record)
                logger.critical(
                    "Integration diagnostic changed ownership; original record restored"
                )
            return bool(
                retained is not None
                and ownership_fingerprint(retained) == record.diagnostic_ownership
                and released
                and after == before
                and not retained.diagnostic_evidence
                and retained.diagnostic_ownership is None
            )
        except Exception as exc:
            logger.critical("Integration diagnostic retained ownership after failure: {}", exc)
            current = read_record(ctx.workspace_scope.root) or record
            target = _bump_attempt(current, str(exc), cap_reason=f"diagnostic failure: {exc}")
            write_record(ctx.workspace_scope.root, target)
            return False


def diagnostic_evidence_verdict(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    """Evaluate whether recorded diagnostic evidence is still trustworthy.

    Returns:
        EvidenceVerdict.UNCHANGED: the evidence still matches live operation state.
        EvidenceVerdict.CHANGED: the evidence or ownership has drifted.
        EvidenceVerdict.UNKNOWN: an observation is unreadable or indeterminate.
    """
    if not record.diagnostic_evidence:
        return EvidenceVerdict.UNCHANGED
    if record.diagnostic_ownership != ownership_fingerprint(record):
        return EvidenceVerdict.CHANGED
    observed_facts = _evidence_to_facts(record.diagnostic_evidence)
    if any(fact.value == _UNREADABLE for fact in observed_facts):
        return EvidenceVerdict.UNKNOWN
    operation_root = (
        Path(record.owning_worktree)
        if (
            record.operation_kind == "target_reconcile"
            and record.owning_worktree
            and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
        )
        else root
    )
    col = collector if collector is not None else operation_evidence_collector()
    if hasattr(col, "evaluate"):
        return col.evaluate(operation_root, record, observed_facts)
    current = col.snapshot(operation_root, record)
    if any(fact.value == _UNREADABLE for fact in current):
        return EvidenceVerdict.UNKNOWN
    return (
        EvidenceVerdict.UNCHANGED
        if col.verify(operation_root, record, observed_facts)
        else EvidenceVerdict.CHANGED
    )


def diagnostic_evidence_unchanged(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    """Return True iff diagnostic evidence evaluates to UNCHANGED."""
    return (
        diagnostic_evidence_verdict(root, record, collector=collector) == EvidenceVerdict.UNCHANGED
    )


def release_verified_diagnostic(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> EvidenceVerdict:
    """Evaluate diagnostic evidence without mutating durable record state.

    Returns an :class:`EvidenceVerdict`. The caller owns the transition
    and durable record write when the verdict is ``UNCHANGED``.
    """
    try:
        return diagnostic_evidence_verdict(root, record, collector=collector)
    except Exception:
        return EvidenceVerdict.UNKNOWN


def _check_head_match(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        head_res = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="integration_diagnostic:verify_head",
        )
    except (OSError, RuntimeError) as exc:
        return False, f"HEAD rev-parse failed: {exc}"
    if head_res.returncode != 0:
        return False, "HEAD is unreadable"
    head = head_res.stdout.strip()
    allowed_heads = {record.integrated_feature_sha, record.pre_target_sha} - {None, ""}
    if head not in allowed_heads:
        return False, f"HEAD {head} does not match integrated_feature_sha or pre_target_sha"
    return True, "HEAD matches"


def _check_parent_match(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        parents_res = run_git(
            ("show", "-s", "--format=%P", "HEAD"),
            cwd=root,
            label="integration_diagnostic:head_parents",
        )
    except (OSError, RuntimeError) as exc:
        return False, f"parents rev-parse failed: {exc}"
    if parents_res.returncode != 0:
        return False, "parents of HEAD are unreadable"
    parents = parents_res.stdout.strip().split()

    if record.merge_commit_parent:
        if record.merge_commit_parent not in parents:
            return (
                False,
                f"parent of HEAD does not match merge_commit_parent {record.merge_commit_parent}",
            )
    elif record.pre_target_sha and record.pre_target_sha not in parents:
        try:
            ancestor_res = run_git(
                ("merge-base", "--is-ancestor", record.pre_target_sha, "HEAD"),
                cwd=root,
                label="integration_diagnostic:check_ancestor",
            )
            if ancestor_res.returncode != 0:
                return (
                    False,
                    f"pre_target_sha {record.pre_target_sha} is not an ancestor of HEAD",
                )
        except (OSError, RuntimeError) as exc:
            return False, f"ancestry check failed: {exc}"
    return True, "parents verified"


def _check_ancestry_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    ok, reason = _check_head_match(root, record)
    if not ok:
        return False, reason
    return _check_parent_match(root, record)


def _check_ownership_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    if not record.owning_worktree or not record.owning_worktree.strip():
        return False, "record.owning_worktree is missing or empty"
    try:
        if Path(record.owning_worktree).resolve() != root.resolve():
            return False, f"owning_worktree {record.owning_worktree} does not match {root}"
    except Exception:
        return False, f"owning_worktree {record.owning_worktree} is invalid"

    expected_fingerprint = ownership_fingerprint(record)
    if not record.diagnostic_ownership:
        return False, "diagnostic_ownership is missing or empty"
    if record.diagnostic_ownership != expected_fingerprint:
        return False, "diagnostic_ownership does not match ownership_fingerprint"
    return True, "ownership verified"


def _check_marker_ref_proof(root: Path, ref_name: str) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        res = run_git(
            ("rev-parse", "--verify", "--quiet", ref_name),
            cwd=root,
            label=f"integration_diagnostic:check_{ref_name.lower()}",
        )
        if res.returncode == 0:
            return False, f"{ref_name} is present"
        if res.returncode != 1:
            return False, f"{ref_name} status is UNKNOWN (unexpected exit code {res.returncode})"
    except (OSError, RuntimeError) as exc:
        return False, f"{ref_name} status is UNKNOWN ({exc})"
    return True, f"{ref_name} absent"


def _check_gitdir_markers_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        gitdir_res = run_git(
            ("rev-parse", "--absolute-git-dir"),
            cwd=root,
            label="integration_diagnostic:gitdir",
        )
        if gitdir_res.returncode != 0:
            return (
                False,
                f"gitdir query returned unexpected exit code {gitdir_res.returncode}: UNKNOWN",
            )
        gitdir = Path(gitdir_res.stdout.strip())
        if (gitdir / "MERGE_HEAD").exists():
            return False, "MERGE_HEAD is present"
        if (gitdir / "REBASE_HEAD").exists():
            return False, "REBASE_HEAD is present"
        if (gitdir / "rebase-merge").exists() or (gitdir / "rebase-apply").exists():
            return False, "rebase state directory is present"
    except (OSError, RuntimeError) as exc:
        return False, f"gitdir check failed: {exc} (UNKNOWN)"
    return True, "gitdir markers absent"


def _check_clean_diff_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.subprocess_runner import run_git

    try:
        diff_res = run_git(
            ("diff", "--quiet", "HEAD"),
            cwd=root,
            label="integration_diagnostic:diff_head",
        )
        if diff_res.returncode == 1:
            return False, "git diff against HEAD is non-empty"
        if diff_res.returncode != 0:
            return False, f"git diff failed with exit code {diff_res.returncode}: UNKNOWN"
    except (OSError, RuntimeError) as exc:
        return False, f"git diff failed: {exc} (UNKNOWN)"
    return True, "diff against HEAD clean"


def _check_index_tree_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.merge import unmerged_paths

    for ref in ("MERGE_HEAD", "REBASE_HEAD"):
        ok, reason = _check_marker_ref_proof(root, ref)
        if not ok:
            return False, reason

    ok, reason = _check_gitdir_markers_proof(root)
    if not ok:
        return False, reason

    unmerged = unmerged_paths(root)
    if unmerged or "<unmerged-path-query-failed>" in unmerged:
        return False, "unmerged paths in index or unmerged query failed"

    ok, reason = _check_clean_diff_proof(root)
    if not ok:
        return False, reason

    return True, "index and tree verified"


def _check_atomic_transition_proof(
    record: IntegrationRecord,
    root: Path | None = None,
    collector: OperationEvidenceCollector | None = None,
) -> tuple[bool, str]:
    has_valid_phase = record.phase in ("integrated",) or (
        record.phase in ("integrating",) and bool(record.integrated_feature_sha)
    )
    if not has_valid_phase:
        return False, f"record.phase is '{record.phase}', expected 'integrated'"
    effective_root = (
        root
        if root is not None
        else (Path(record.owning_worktree) if record.owning_worktree else Path.cwd())
    )
    verdict = diagnostic_evidence_verdict(effective_root, record, collector=collector)
    if verdict == EvidenceVerdict.UNKNOWN:
        return False, "diagnostic evidence status is UNKNOWN (observations unreadable)"
    if verdict != EvidenceVerdict.UNCHANGED:
        return False, "diagnostic evidence does not match release conditions"
    return True, "atomic transition verified"


def _check_operation_completion_proof(
    root: Path,
    record: IntegrationRecord,
    collector: OperationEvidenceCollector | None = None,
) -> tuple[bool, str]:
    checks = (
        _check_ancestry_proof(root, record),
        _check_ownership_proof(root, record),
        _check_index_tree_proof(root),
        _check_atomic_transition_proof(record, root=root, collector=collector),
    )
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, "positive operation-completion proof satisfied"


def _pre_pass_intervention(
    root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    reason: str,
) -> bool:
    intervention = _raise_intervention(record, reason)
    write_record(root, intervention, backend=backend)
    logger.critical(
        "Operation completion proof failed ({}); operator intervention required",
        reason,
    )
    return False


def _run_completion_pre_pass(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    snapshot: tuple[OperationFact, ...],
    next_action: IntegrationAction,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None = None,
) -> bool | None:
    for fact in snapshot:
        if fact.kind in (
            _KIND_MERGE_OBJECT_RESOLVABLE,
            _KIND_REBASE_OBJECT_RESOLVABLE,
        ) and fact.value in ("False", "false"):
            return _pre_pass_intervention(
                root,
                record,
                backend,
                "unreachable marker object; operator intervention required",
            )

    if next_action in (
        IntegrationAction.CONTINUE_GIT_OPERATION,
        IntegrationAction.LAND_TARGET,
    ):
        return _complete_or_intervene(
            root=root,
            inspection_root=inspection_root,
            record=record,
            backend=backend,
            collector=collector,
        )

    if next_action == IntegrationAction.REQUIRE_INTERVENTION:
        reason = last_outcome_reason(record) or "operator intervention required"
        return _pre_pass_intervention(root, record, backend, reason)

    return None


def _complete_or_intervene(
    *,
    root: Path,
    inspection_root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
    collector: OperationEvidenceCollector | None,
) -> bool:
    """Run the positive-completion pre-pass through the typed record store."""
    proof_ok, proof_reason = _check_operation_completion_proof(
        inspection_root, record, collector=collector
    )
    if not proof_ok:
        return _pre_pass_intervention(
            root, record, backend, f"operation completion proof failed: {proof_reason}"
        )
    try:
        apply_record_transition(
            root,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            backend=backend,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
    except RecordBusyError:
        logger.warning(
            "Release busy; record bytes preserved for bounded retry"
        )
        return False
    except RecordUnreadableError:
        logger.critical(
            "Release refused: durable record unreadable; intervention required"
        )
        return _pre_pass_intervention(
            root,
            record,
            backend,
            "release refusal: durable record unreadable; intervention required",
        )
    logger.info(
        "Positive operation-completion proof verified; released diagnostic evidence"
    )
    return True


def handoff_integration_diagnostic(
    *,
    root: Path,
    record: IntegrationRecord,
    failure: str,
    agents: Sequence[str],
    invoke: Callable[[str, Path], bool],
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    collector: OperationEvidenceCollector | None = None,
) -> bool:
    """Rotate inspection candidates without authorizing publication or record cleanup.

    The diagnostic-repair attempt counter is bounded at
    ``repair_attempt_cap``. When the cap is reached, the function
    emits the ``RAISE_DIAGNOSTIC_INTERVENTION`` transition and
    returns ``False`` without calling any agent -- the next run
    loop iteration will see ``diagnostic_intervention_required``
    and surface ``REPORT_INTERVENTION_REQUIRED`` to the operator.
    """
    inspection_root = (
        Path(record.owning_worktree)
        if (record.owning_worktree and backend.exists(Path(record.owning_worktree)))
        else root
    )
    col = collector if collector is not None else operation_evidence_collector()
    snapshot = col.snapshot(inspection_root, record)
    next_action = next_integration_action(record)

    pre_pass_result = _run_completion_pre_pass(
        root=root,
        inspection_root=inspection_root,
        record=record,
        snapshot=snapshot,
        next_action=next_action,
        backend=backend,
        collector=col,
    )
    if pre_pass_result is not None:
        return pre_pass_result

    if record.merge_commit_repair_attempts >= repair_attempt_cap:
        write_record(
            root,
            _raise_intervention(
                record,
                "diagnostic repair exhausted; operator intervention required",
            ),
            backend=backend,
        )
        logger.critical(
            "Integration diagnostic repair exhausted after {} attempts; "
            "operator intervention required",
            record.merge_commit_repair_attempts,
        )
        return False
    if not agents:
        target_record = _bump_attempt(
            record,
            "no eligible diagnostic agent available",
            repair_attempt_cap=repair_attempt_cap,
        )
        if (
            target_record.diagnostic_intervention_required
            and not record.diagnostic_intervention_required
        ):
            logger.critical(
                "Integration diagnostic repair exhausted after {} attempts; "
                "operator intervention required",
                target_record.merge_commit_repair_attempts,
            )
        write_record(root, target_record, backend=backend)
        return False
    agent = agents[record.merge_commit_repair_attempts % len(agents)]
    prompt = root / ".agent" / "tmp" / "integration_diagnostic_prompt.md"
    content = (
        "Inspect the retained integration blocker and repair only its environment.\n"
        f"Diagnostic data: {failure[:8192]}\n"
        f"Recorded target: {record.target}\n"
        f"Original feature: {record.pre_feature_sha}\n"
        f"Original target: {record.pre_target_sha}\n"
        f"Operation worktree: {record.owning_worktree or root}\n"
        "Completion evidence is missing or unreadable. Examine Git history, reflogs, "
        "operation metadata, executable availability, and the command diagnostic. "
        "Restore access to existing evidence only when independently justified. "
        "Do not create, alter, or delete recovery records, receipts, refs, indexes, "
        "operation metadata, hooks, signing configuration, or source files. "
        "Do not reset, abort, stash, commit, rebase, merge, stage, or bypass checks. "
        "Preserve all unfinished work. Declare_complete with observed cause, "
        "evidence locations, and any environment repair. Agent success does not "
        "authorize landing; Ralph independently retries and verifies Git evidence.\n"
    )
    backend.mkdir(prompt.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, prompt, content)
    write_record(root, _bump_attempt(record, failure[:8192]), backend=backend)
    return invoke(agent, prompt)
