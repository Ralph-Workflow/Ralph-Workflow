from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph._build_identity import build_identity
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pipeline import _terminal_emitter as terminal_emitter
from ralph.pipeline._integration_diagnostic_state import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
)
from ralph.pipeline._integration_ladder_evidence import _record_evidence
from ralph.pipeline.auto_integrate_record import BUMP_DIAGNOSTIC_ATTEMPT
from ralph.pipeline.auto_integrate_recovery_terminal import (
    RESUME_COMMAND,
    ResolutionTerminalMarker,
    terminal_marker_blocks_attempt,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    CleanupLadderRung,
    cleanup_action_for,
)
from ralph.pipeline.integration_ladder import (
    LADDER_ATTEMPT_BUDGET,
    LAST_RESORT_DISABLED_NO_SIGNATURE,
    LAST_RESORT_TERMINAL_NO_SIGNATURE,
    LAST_RESORT_WAIT_SECONDS,
    LadderFacts,
    LadderStepResult,
    LandedVerdict,
    decide_ladder_transition,
    default_git_plumbing,
    ensure_preserved_copy,
    evaluate_last_resort_preconditions,
    ladder_facts_fingerprint,
    last_resort_wait_proof,
    preserved_copy_identity,
    record_salvage_attempt,
    resume_rung,
    run_ladder_rung,
    terminal_operator_message,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.integration_preservation_bundle import (
    IntegrationPreservationBundle,
    preserve_integration_state,
)
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState
    from ralph.pipeline.last_resort_agent_pool import LastResortAgentPool
    from ralph.pipeline.last_resort_wait_proof import LastResortWaitProof
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "COMPLETION_IN_PLACE_HANDOFF_KEY",
    "PLANNING_HANDOFF_FILENAME",
    "budget_spent_record",
    "check_all_last_resort_conditions",
    "check_last_resort_agent_eligibility",
    "check_last_resort_preconditions",
    "check_target_worktree_context",
    "completion_in_place_handoff_record",
    "create_planning_handoff_record",
    "ensure_last_resort_preservation",
    "escalate_diagnostic_budget_to_ladder",
    "escalate_record_fact_outcome",
    "is_disabled_signature_step",
    "is_last_resort_disabled_for_caller",
    "ladder_terminal_state",
    "next_resolver",
    "observe_workspace_change",
    "read_planning_handoff_record",
]

PLANNING_HANDOFF_FILENAME: Final[str] = "planning-handoff.json"
COMPLETION_IN_PLACE_HANDOFF_KEY: Final[str] = "handoff:completion_in_place"

_INTEGRATION_PROBE_KEY = "integration_resolution"

_LAST_RESORT_EVIDENCE_KEY: Final[str] = (
    f"rung:{cleanup_action_for(CleanupLadderRung.LAST_RESORT).value}"
)

_TERMINAL_EVIDENCE_PREFIX: Final[str] = "terminal:"

_TERMINAL_FACTS_EVIDENCE_KEY: Final[str] = "terminal:ladder_facts"


def observe_workspace_change(
    controller: RecoveryController,
    root: Path,
    attempt: Callable[[], None],
) -> bool:
    probe = controller.workspace_probe
    if probe is None:
        attempt()
        return False
    from ralph.workspace.fs import FsWorkspace

    workspace = FsWorkspace(root)
    try:
        probe.observe_before(_INTEGRATION_PROBE_KEY, workspace)
    except Exception as exc:
        logger.warning(
            "Integration: workspace baseline could not be observed before the attempt: {}", exc
        )
    attempt()
    try:
        return bool(probe.has_changed(_INTEGRATION_PROBE_KEY, workspace))
    except Exception as exc:
        logger.warning(
            "Integration: workspace change could not be observed after the attempt: {}", exc
        )
        return False


def check_target_worktree_context(target_root: Path) -> str | None:
    from ralph.workspace.context import WorkspaceContextError, workspace_context

    try:
        with workspace_context(target_root):
            pass
    except WorkspaceContextError as exc:
        return exc.fix
    except Exception as exc:
        return f"target worktree context error: {exc}"
    return None


def next_resolver(
    controller: RecoveryController,
    agents: Sequence[str],
    target_root: Path | None = None,
) -> str | None:
    if target_root is not None:
        failure_reason = check_target_worktree_context(target_root)
        if failure_reason is not None:
            return failure_reason

    from ralph.pipeline.conflict_resolution.resolver_health import choose_resolver

    choice = choose_resolver(controller, agents)
    if choice.agent is None:
        logger.warning(
            "Integration diagnostic: every resolver is cooling down; next one is "
            "selectable in {:.0f}s",
            choice.wait_ms / 1000.0,
        )
    return choice.agent


def _invoke_and_judge(
    agent: str,
    prompt: Path,
    invoke: Callable[[str, Path], bool],
    controller: RecoveryController,
    inspection_root: Path,
    record: IntegrationRecord,
    col: OperationEvidenceCollector,
    failure_detail: Callable[[], BaseException | str | None] | None,
) -> bool:
    from ralph.pipeline._integration_diagnostic_proof import _check_operation_completion_proof
    from ralph.pipeline.conflict_resolution.resolver_health import bench_resolver, clear_resolver
    from ralph.workspace.context import WorkspaceContextError

    def _attempt() -> None:
        invoke(agent, prompt)

    try:
        workspace_changed = observe_workspace_change(controller, inspection_root, _attempt)
    except WorkspaceContextError:
        raise
    except Exception as exc:
        bench_resolver(controller, agent, exc)
        raise
    verified, _reason = _check_operation_completion_proof(inspection_root, record, collector=col)
    if verified:
        clear_resolver(controller, agent)
    else:
        detail = failure_detail() if failure_detail is not None else None
        if detail is not None:
            bench_resolver(
                controller,
                agent,
                detail,
                workspace_changed=workspace_changed,
            )
        else:
            bench_resolver(
                controller,
                agent,
                None,
                workspace_changed=workspace_changed,
                outcome=ConflictAttemptOutcome.ATTEMPT_FAILED,
            )
    return verified


def budget_spent_record(
    record: IntegrationRecord,
    *,
    reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
) -> IntegrationRecord:

    return record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=min(
            record.merge_commit_repair_attempts + 1, repair_attempt_cap
        ),
        repair_last_error=reason,
        repair_cap_reason=reason,
    )


def _last_charged_signature(state: AttemptLedgerState) -> str:
    for signature, _action in reversed(list(state.repeat_counts)):
        return signature
    return ""


def _observed_signature(root: Path) -> str:
    from ralph.pipeline.integration_decision import AttemptState
    from ralph.pipeline.integration_director import observe

    try:
        return AttemptState().signature(observe(root))
    except Exception as exc:
        logger.warning("Integration diagnostic: blocking signature unreadable: {}", exc)
        return ""


def _resolve_blocking_signature(
    root: Path,
    state: AttemptLedgerState,
    rung: CleanupLadderRung,
    decided_signature: str | None,
    fresh_signature: str | None,
) -> tuple[str, str]:
    if decided_signature is not None:
        return decided_signature, fresh_signature or ""
    if rung is not CleanupLadderRung.LAST_RESORT:
        return "", ""
    charged = _last_charged_signature(state)
    if not charged:
        return "", ""
    observed = _observed_signature(root)
    if observed and observed == charged:
        return charged, observed
    return "", ""


def _preserved_copy_clause(
    root: Path,
    *,
    step: LadderStepResult,
    ladder: AttemptLedger,
    plumbing: GitPlumbing,
    fs: FileQuarantine | None,
    state_dir: Path | None,
    observed_head: str | None,
) -> str:
    identity = preserved_copy_identity(step, ladder.read())
    if identity:
        return identity
    if not plumbing.changed_paths(root):
        return f"nothing to preserve at HEAD {observed_head or 'unreadable'}"
    preserved = ensure_preserved_copy(
        root,
        ledger=ladder,
        git=plumbing,
        fs=fs,
        state_dir=state_dir,
    )
    if preserved is not None:
        identity = preserved_copy_identity(preserved, ladder.read())
        if identity:
            return identity
    return "the uncommitted changes are not preserved in any durable copy"


def _record_ladder_facts(
    ledger: AttemptLedger,
    record: IntegrationRecord,
    observed_head: str | None,
    target_tip: str | None,
) -> None:
    facts = ladder_facts_fingerprint(
        record,
        observed_head=observed_head,
        target_tip=target_tip,
    )
    state = ledger.read()
    if state.ladder_evidence.get(_TERMINAL_FACTS_EVIDENCE_KEY) == facts:
        return
    ledger.persist(
        replace(
            state,
            ladder_evidence={**state.ladder_evidence, _TERMINAL_FACTS_EVIDENCE_KEY: facts},
        )
    )


def ladder_terminal_state(
    root: Path,
    record: IntegrationRecord,
    *,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
) -> LadderStepResult | None:
    ladder = ledger if ledger is not None else AttemptLedger(root)
    state = ladder.read()
    value = state.ladder_evidence.get(_LAST_RESORT_EVIDENCE_KEY)
    if value is None or not value.startswith(_TERMINAL_EVIDENCE_PREFIX):
        return None
    recorded = state.ladder_evidence.get(_TERMINAL_FACTS_EVIDENCE_KEY)
    if recorded is not None:
        plumbing = git if git is not None else default_git_plumbing()
        if recorded != ladder_facts_fingerprint(
            record,
            observed_head=plumbing.head_sha(root),
            target_tip=plumbing.target_sha(root, record.target),
        ):
            return None
    return LadderStepResult(
        CleanupLadderRung.LAST_RESORT,
        "terminal",
        value,
        evidence={_LAST_RESORT_EVIDENCE_KEY: value},
    )


def _write_terminal_marker(root: Path, record: IntegrationRecord) -> None:
    from ralph.pipeline.auto_integrate_record import read_record, write_record

    current = read_record(root) or record
    marker = ResolutionTerminalMarker(
        current.resolution_gate_key,
        "NextStrategy",
        RESUME_COMMAND,
    )
    write_record(root, current.with_resolution_terminal_marker(marker.as_record_value()))


def escalate_record_fact_outcome(
    root: Path,
    record: IntegrationRecord,
    outcome: RecordFactOutcome,
    *,
    reason: str,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
    target: str | None = None,
    branch: str = "feature",
    feature_tip: str | None = None,
    decided_signature: str | None = None,
    fresh_signature: str | None = None,
) -> LadderStepResult:
    ladder = ledger if ledger is not None else AttemptLedger(root)
    plumbing = git if git is not None else default_git_plumbing()
    effective_target = record.target if target is None else target
    terminal = ladder_terminal_state(root, record, ledger=ladder, git=plumbing)
    if terminal is not None:
        return terminal
    if terminal_marker_blocks_attempt(record, record.resolution_gate_key):
        marker = ResolutionTerminalMarker(
            record.resolution_gate_key,
            "NextStrategy",
            RESUME_COMMAND,
        )
        return LadderStepResult(
            CleanupLadderRung.LAST_RESORT,
            "terminal",
            f"terminal:durable-marker:{marker.state}",
        )
    if outcome is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT:
        record_salvage_attempt(ladder, "resolver_agent", f"failed:{reason}")
    rung = resume_rung(ladder.read())
    if rung is None:
        rung = CleanupLadderRung.LAST_RESORT
    decided, fresh = _resolve_blocking_signature(
        root,
        ladder.read(),
        rung,
        decided_signature,
        fresh_signature,
    )
    step = run_ladder_rung(
        rung,
        root=root,
        ledger=ladder,
        git=plumbing,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=effective_target,
        branch=branch,
        feature_tip=feature_tip,
        decided_signature=decided,
        fresh_signature=fresh,
        terminal_when_disabled=True,
    )
    observed_head = plumbing.head_sha(root)
    target_tip = plumbing.target_sha(root, effective_target)
    landed = (
        LandedVerdict.LANDED
        if record.integrated_feature_sha is not None and target_tip == record.integrated_feature_sha
        else LandedVerdict.NOT_LANDED
    )
    state = ladder.read()
    enabled_rungs = frozenset(
        candidate
        for candidate in CLEANUP_LADDER_ORDER
        if state.ladder_evidence.get(f"rung:{cleanup_action_for(candidate).value}")
        in (None, "intent")
    )
    decision = decide_ladder_transition(
        LadderFacts(
            rung=rung,
            outcome=step.outcome,
            reason=step.reason,
            landed=landed,
            enabled_rungs=enabled_rungs,
            terminal_for_attempt=False,
            clock_now=clock.now() if clock is not None else 0.0,
        )
    )
    if decision.terminal:
        _record_ladder_facts(ladder, record, observed_head, target_tip)
    preserved = _preserved_copy_clause(
        root,
        step=step,
        ladder=ladder,
        plumbing=plumbing,
        fs=fs,
        state_dir=state_dir,
        observed_head=observed_head,
    )
    if decision.terminal:
        terminal_step = LadderStepResult(
            rung,
            "terminal",
            decision.reason,
            preserved_ref=step.preserved_ref,
            preserved_sidecar=step.preserved_sidecar,
            quarantined_paths=step.quarantined_paths,
            evidence=step.evidence,
        )
        if is_disabled_signature_step(step):
            cip_record = completion_in_place_handoff_record(record, reason=step.reason)
            from ralph.pipeline.auto_integrate_record import write_record

            write_record(root, cip_record)
            _record_evidence(ladder, "transition:autonomous", "completion_in_place")
            _record_evidence(ladder, COMPLETION_IN_PLACE_HANDOFF_KEY, "autonomous_transition")
            if decided_signature is not None:
                return LadderStepResult(
                    rung,
                    "failed",
                    "autonomous-transition:completion_in_place",
                    preserved_ref=step.preserved_ref,
                    preserved_sidecar=step.preserved_sidecar,
                    quarantined_paths=step.quarantined_paths,
                    evidence={**step.evidence, "next_strategy": "completion_in_place"},
                )
            terminal_step = LadderStepResult(
                rung,
                "terminal",
                decision.reason,
                preserved_ref=step.preserved_ref,
                preserved_sidecar=step.preserved_sidecar,
                quarantined_paths=step.quarantined_paths,
                evidence={**step.evidence, "next_strategy": "completion_in_place"},
            )
        message = terminal_operator_message(
            record,
            reason=decision.reason,
            observed_head=observed_head,
            preserved_copy=preserved,
            proof_failures=tuple(step.evidence.values()) or (step.reason,),
        )
        _write_terminal_marker(root, record)
        logger.warning(terminal_emitter.terminal_message(message), build_identity())
        return terminal_step
    else:
        next_rung = decision.next_rung or rung
        message = (
            "Integration diagnostic budget spent; escalated autonomously to fallback "
            f"ladder rung {next_rung.value} after {rung.value} ended "
            f"({step.outcome}): {step.reason}; "
            f"preserved copy: {preserved}; the run continues without waiting on a person"
        )
    logger.warning("{}", message)
    return step


def escalate_diagnostic_budget_to_ladder(
    root: Path,
    record: IntegrationRecord,
    *,
    reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
    ledger: AttemptLedger | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
    target: str | None = None,
    branch: str = "feature",
    feature_tip: str | None = None,
) -> LadderStepResult:
    return escalate_record_fact_outcome(
        root,
        record,
        RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
        reason=reason,
        ledger=ledger,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=target,
        branch=branch,
        feature_tip=feature_tip,
    )


def check_last_resort_preconditions(
    state: AttemptLedgerState,
    *,
    decided_signature: str,
    fresh_signature: str,
    budget: int = LADDER_ATTEMPT_BUDGET,
) -> dict[str, str]:
    return evaluate_last_resort_preconditions(
        state,
        decided_signature=decided_signature,
        fresh_signature=fresh_signature,
        budget=budget,
    )


def is_last_resort_disabled_for_caller(
    decided_signature: str | None,
    fresh_signature: str | None,
) -> bool:
    if not decided_signature:
        return True
    return fresh_signature is not None and fresh_signature != decided_signature


def is_disabled_signature_step(step: LadderStepResult) -> bool:
    return step.rung == CleanupLadderRung.LAST_RESORT and step.reason in (
        LAST_RESORT_TERMINAL_NO_SIGNATURE,
        LAST_RESORT_DISABLED_NO_SIGNATURE,
    )


def check_last_resort_agent_eligibility(
    ledger: AttemptLedger,
    eligibility: AgentFailureLedger,
    pools: tuple[LastResortAgentPool, ...],
    blocking_signature: str,
    *,
    wait_seconds: float = LAST_RESORT_WAIT_SECONDS,
    epoch_now: Callable[[], float] = time.time,
) -> LastResortWaitProof:
    return last_resort_wait_proof(
        ledger=ledger,
        eligibility=eligibility,
        pools=pools,
        blocking_signature=blocking_signature,
        wait_seconds=wait_seconds,
        epoch_now=epoch_now,
    )


def check_all_last_resort_conditions(
    ledger: AttemptLedger,
    *,
    decided_signature: str,
    fresh_signature: str,
    eligibility: AgentFailureLedger | None = None,
    pools: tuple[LastResortAgentPool, ...] = (),
    budget: int = LADDER_ATTEMPT_BUDGET,
    wait_seconds: float = LAST_RESORT_WAIT_SECONDS,
    epoch_now: Callable[[], float] = time.time,
) -> tuple[bool, dict[str, str], LastResortWaitProof | None]:
    if is_last_resort_disabled_for_caller(decided_signature, fresh_signature):
        preconditions = check_last_resort_preconditions(
            ledger.read(),
            decided_signature=decided_signature,
            fresh_signature=fresh_signature,
            budget=budget,
        )
        return False, preconditions, None

    preconditions = check_last_resort_preconditions(
        ledger.read(),
        decided_signature=decided_signature,
        fresh_signature=fresh_signature,
        budget=budget,
    )
    all_preconditions_met = all(v.startswith("met") for v in preconditions.values())

    wait_proof: LastResortWaitProof | None = None
    if eligibility is not None and pools:
        wait_proof = check_last_resort_agent_eligibility(
            ledger,
            eligibility,
            pools,
            fresh_signature,
            wait_seconds=wait_seconds,
            epoch_now=epoch_now,
        )
        if not wait_proof.authorized:
            all_preconditions_met = False

    return all_preconditions_met, preconditions, wait_proof


def ensure_last_resort_preservation(
    root: Path,
    *,
    state_dir: Path,
    target: str,
    ledger: AttemptLedger | None = None,
) -> IntegrationPreservationBundle | None:
    bundle = preserve_integration_state(root, state_dir=state_dir, target=target)
    if bundle is not None:
        logger.warning(
            "integration ladder last-resort preservation bundle created at {}",
            bundle.manifest_path,
        )
        if ledger is not None:
            _record_evidence(ledger, "preserved:bundle", bundle.manifest_path)
    return bundle


def create_planning_handoff_record(
    state_dir: Path,
    *,
    manifest_path: str,
    carried: tuple[str, ...],
    not_carried: tuple[str, ...],
    next_phase: str = "planning",
    ledger: AttemptLedger | None = None,
) -> Path:
    payload: dict[str, object] = {
        "bundle": manifest_path,
        "carried": list(carried),
        "not_carried": list(not_carried),
        "next_phase": next_phase,
    }
    carry_path = Path(manifest_path).with_name(PLANNING_HANDOFF_FILENAME)
    write_text_if_changed(DEFAULT_FILE_BACKEND, carry_path, json.dumps(payload, indent=2), encoding="utf-8")
    if ledger is not None:
        _record_evidence(ledger, "preserved:planning-handoff", str(carry_path))
    return carry_path


def read_planning_handoff_record(handoff_path: Path) -> dict[str, object] | None:
    if not handoff_path.is_file():
        return None
    try:
        content = handoff_path.read_text(encoding="utf-8")
        parsed: object = json.loads(content)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
        return None
    except Exception:
        return None


def completion_in_place_handoff_record(
    record: IntegrationRecord,
    *,
    reason: str,
) -> IntegrationRecord:
    return record.model_copy(
        update={
            "resolving_rebase": False,
            "resolving_merge": False,
            "diagnostic_intervention_required": False,
            "repair_cap_reason": reason,
            "resolution_terminal_marker": None,
        }
    )
