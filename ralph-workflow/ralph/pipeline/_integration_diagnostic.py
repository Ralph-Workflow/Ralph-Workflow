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
from ralph.pipeline.operation_fact import _UNREADABLE

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
