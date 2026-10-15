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
    Transition,
    write_record,
)
from ralph.pipeline.operation_evidence import (
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


def _ownership_fingerprint(record: IntegrationRecord) -> str:
    """Backward-compatible private alias for :func:`ownership_fingerprint`."""
    return ownership_fingerprint(record)


def _validate_transition(record: IntegrationRecord, transition: Transition) -> None:
    """Backward-compat shim that delegates to :meth:`Transition.is_legal_from`.

    The validator is now a property of the transition itself, not a
    free function in this module. This shim remains so older call
    sites keep working while we migrate them to ``with_transition``.
    """
    if not transition.is_legal_from(record):
        raise ValueError(f"Transition {transition.name!r} is not legal from phase={record.phase!r}")


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
        if record.rebase_continue_pending:
            from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches

            return not pending_rebase_identity_matches(operation_root, record)
        if record.merge_commit_pending:
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


def diagnose_retained_integration(ctx: _LoopContext, failure: str) -> bool:
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
        if not leases.enter_context(integration_transaction(ctx.workspace_scope.root)):
            return False
        record = read_record(ctx.workspace_scope.root)
        if record is None:
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

        def invoke(agent: str, prompt: Path) -> bool:
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

        try:
            collector = operation_evidence_collector()
            before = record.diagnostic_evidence or tuple(
                fact.to_evidence_tuple() for fact in collector.snapshot(operation_root, record)
            )
            if not record.diagnostic_evidence:
                record = record.with_transition(
                    HOLD_DIAGNOSTIC_EVIDENCE,
                    diagnostic_evidence=before,
                    diagnostic_ownership=ownership_fingerprint(record),
                )
            handoff_integration_diagnostic(
                root=ctx.workspace_scope.root,
                record=record,
                failure=failure,
                agents=tuple(
                    agent
                    for agent in resolution_chain_agents(ctx.policy_bundle)
                    if ctx.registry.get(agent) is not None
                ),
                invoke=invoke,
            )
            after = tuple(
                fact.to_evidence_tuple() for fact in collector.snapshot(operation_root, record)
            )
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
            return False
        except Exception as exc:
            logger.critical("Integration diagnostic retained ownership after failure: {}", exc)
            return False


def diagnostic_evidence_unchanged(root: Path, record: IntegrationRecord) -> bool:
    if not record.diagnostic_evidence:
        return True
    if record.diagnostic_ownership != ownership_fingerprint(record):
        return False
    observed_facts = _evidence_to_facts(record.diagnostic_evidence)
    if any(fact.value == _UNREADABLE for fact in observed_facts):
        return False
    operation_root = (
        Path(record.owning_worktree)
        if (
            record.operation_kind == "target_reconcile"
            and record.owning_worktree
            and DEFAULT_FILE_BACKEND.exists(Path(record.owning_worktree))
        )
        else root
    )
    return _verify_diagnostic_evidence(operation_root, record, observed_facts)


def _verify_diagnostic_evidence(
    operation_root: Path,
    record: IntegrationRecord,
    observed_facts: tuple[OperationFact, ...],
) -> bool:
    if _diagnostic_snapshot is not _default_diagnostic_snapshot:
        current = _evidence_to_facts(_diagnostic_snapshot(operation_root))
        if any(fact.value == _UNREADABLE for fact in current):
            return False
        current_map = {(fact.kind, fact.scope): fact.value for fact in current}
        kind_map = {fact.kind: fact.value for fact in current if fact.scope is None}
        return all(
            _matches_fact(
                fact,
                current_map.get((fact.kind, fact.scope)) or kind_map.get(fact.kind),
            )
            for fact in observed_facts
        )
    collector = operation_evidence_collector()
    current = collector.snapshot(operation_root, record)
    if any(fact.value == _UNREADABLE for fact in current):
        return False
    return collector.verify(operation_root, record, observed_facts)


def _matches_fact(fact: OperationFact, expected: str | None) -> bool:
    return expected is not None and expected != _UNREADABLE and expected == fact.value


def release_verified_diagnostic(root: Path, record: IntegrationRecord) -> str | None:
    try:
        if not diagnostic_evidence_unchanged(root, record):
            return "diagnostic evidence changed; original evidence must be restored before recovery"
        cleared = record.with_transition(
            RELEASE_DIAGNOSTIC_EVIDENCE,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
        )
        write_record(root, cleared)
        return None
    except Exception as exc:
        return f"diagnostic evidence unreadable; recovery retained: {exc}"


def _diagnostic_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    """Adapter from :class:`OperationEvidenceCollector` to the legacy tuple shape."""
    from ralph.pipeline.auto_integrate_record import read_record

    record = read_record(root)
    if record is None:
        return ()
    facts = operation_evidence_collector().snapshot(root, record)
    return tuple(fact.to_evidence_tuple() for fact in facts)


_default_diagnostic_snapshot = _diagnostic_snapshot


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

    is_rebase = bool(record.resolving_rebase or record.rebase_continue_pending)
    if is_rebase:
        if not record.pre_target_sha or record.pre_target_sha not in parents:
            return False, f"parent of HEAD does not match pre_target_sha {record.pre_target_sha}"
    else:
        expected_parents = {record.merge_commit_parent, record.pre_target_sha} - {None, ""}
        if not any(p in parents for p in expected_parents):
            return (
                False,
                f"parent of HEAD does not match merge_commit_parent {record.merge_commit_parent} or pre_target_sha {record.pre_target_sha}",
            )
    return True, "parents verified"


def _check_ancestry_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    ok, reason = _check_head_match(root, record)
    if not ok:
        return False, reason
    return _check_parent_match(root, record)


def _check_ownership_proof(root: Path, record: IntegrationRecord) -> tuple[bool, str]:
    if record.owning_worktree is not None:
        try:
            if Path(record.owning_worktree).resolve() != root.resolve():
                return False, f"owning_worktree {record.owning_worktree} does not match {root}"
        except Exception:
            return False, f"owning_worktree {record.owning_worktree} is invalid"

    if (
        record.diagnostic_ownership is not None
        and record.diagnostic_ownership != ownership_fingerprint(record)
    ):
        return False, "diagnostic_ownership does not match ownership_fingerprint"
    return True, "ownership verified"


def _check_index_tree_proof(root: Path) -> tuple[bool, str]:
    from ralph.git.merge import unmerged_paths
    from ralph.git.subprocess_runner import run_git

    try:
        merge_head_res = run_git(
            ("rev-parse", "--verify", "MERGE_HEAD"),
            cwd=root,
            label="integration_diagnostic:check_merge_head",
        )
        if merge_head_res.returncode == 0:
            return False, "MERGE_HEAD is present"
    except (OSError, RuntimeError):
        pass

    try:
        rebase_head_res = run_git(
            ("rev-parse", "--verify", "REBASE_HEAD"),
            cwd=root,
            label="integration_diagnostic:check_rebase_head",
        )
        if rebase_head_res.returncode == 0:
            return False, "REBASE_HEAD is present"
    except (OSError, RuntimeError):
        pass

    unmerged = unmerged_paths(root)
    if unmerged or "<unmerged-path-query-failed>" in unmerged:
        return False, "unmerged paths in index or unmerged query failed"

    try:
        diff_res = run_git(
            ("diff", "--quiet", "HEAD"),
            cwd=root,
            label="integration_diagnostic:diff_head",
        )
        if diff_res.returncode != 0:
            return False, "git diff against HEAD is non-empty"
    except (OSError, RuntimeError) as exc:
        return False, f"git diff failed: {exc}"
    return True, "index and tree verified"


def _check_atomic_transition_proof(record: IntegrationRecord) -> tuple[bool, str]:
    has_valid_phase = record.phase in ("integrated",) or (
        record.phase in ("integrating",) and bool(record.integrated_feature_sha)
    )
    if not has_valid_phase:
        return False, f"record.phase is '{record.phase}', expected 'integrated'"
    if bool(record.diagnostic_evidence):
        return False, "diagnostic_evidence is not empty"
    return True, "atomic transition verified"


def _check_operation_completion_proof(
    root: Path,
    record: IntegrationRecord,
) -> tuple[bool, str]:
    checks = (
        _check_ancestry_proof(root, record),
        _check_ownership_proof(root, record),
        _check_index_tree_proof(root),
        _check_atomic_transition_proof(record),
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
    intervention = record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason=reason,
    )
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

    if next_action == IntegrationAction.CONTINUE_GIT_OPERATION:
        proof_ok, proof_reason = _check_operation_completion_proof(inspection_root, record)
        if proof_ok:
            cleared = record.with_transition(
                RELEASE_DIAGNOSTIC_EVIDENCE,
                diagnostic_evidence=(),
                diagnostic_ownership=None,
            )
            write_record(root, cleared, backend=backend)
            logger.info(
                "Positive operation-completion proof verified; released diagnostic evidence"
            )
            return True
        return _pre_pass_intervention(
            root, record, backend, f"operation completion proof failed: {proof_reason}"
        )

    if next_action == IntegrationAction.REQUIRE_INTERVENTION:
        reason = last_outcome_reason(record) or "operator intervention required"
        return _pre_pass_intervention(root, record, backend, reason)

    return None


def handoff_integration_diagnostic(
    *,
    root: Path,
    record: IntegrationRecord,
    failure: str,
    agents: Sequence[str],
    invoke: Callable[[str, Path], bool],
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
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
    collector = operation_evidence_collector()
    snapshot = collector.snapshot(inspection_root, record)
    next_action = next_integration_action(record)

    pre_pass_result = _run_completion_pre_pass(
        root=root,
        inspection_root=inspection_root,
        record=record,
        snapshot=snapshot,
        next_action=next_action,
        backend=backend,
    )
    if pre_pass_result is not None:
        return pre_pass_result

    if record.merge_commit_repair_attempts >= repair_attempt_cap:
        intervention = record.with_transition(
            RAISE_DIAGNOSTIC_INTERVENTION,
            diagnostic_intervention_required=True,
            repair_cap_reason=("diagnostic repair exhausted; operator intervention required"),
        )
        write_record(root, intervention, backend=backend)
        logger.critical(
            "Integration diagnostic repair exhausted after {} attempts; "
            "operator intervention required",
            record.merge_commit_repair_attempts,
        )
        return False
    if not agents:
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
    bumped = record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=record.merge_commit_repair_attempts + 1,
        repair_last_error=failure[:8192],
    )
    write_record(root, bumped, backend=backend)
    return invoke(agent, prompt)
