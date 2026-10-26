"""Diagnostic-cap exhaustion routing: the typed "diagnostic budget spent" outcome.

When the diagnostic-repair attempt budget
(:data:`~ralph.pipeline._integration_diagnostic_state.DIAGNOSTIC_REPAIR_ATTEMPT_CAP`)
is spent, the durable record is stamped with the typed
:data:`~ralph.pipeline._integration_diagnostic_state.DIAGNOSTIC_BUDGET_SPENT_REASON`
marker (through the closed ``BUMP_DIAGNOSTIC_ATTEMPT`` transition) and the
state is routed into the **autonomous fallback ladder**
(:mod:`ralph.pipeline.integration_ladder`): ledgered rungs 1-6, ending in
the rung 6 hard reset — never a person.

The module is the single authority for that routing. It consumes the
owner's typed fail-closed vocabulary
(:class:`~ralph.pipeline.integration_owner.RecordFactOutcome`) read-only
and owns:

* :func:`budget_spent_record` — the typed durable-record stamp that
  replaces the retired ``RAISE_DIAGNOSTIC_INTERVENTION`` emission;
* :func:`escalate_record_fact_outcome` — the one-rung-per-call ladder
  dispatch for a typed record-fact outcome: it records the
  resolver-agent salvage attempt the last-resort precondition waits
  for when the budget is spent, executes the next unfinished rung
  through :func:`ralph.pipeline.integration_ladder.run_ladder_rung`
  (selected by the durable ledger's own resume rule), and names the
  preserved copy the rung produced;
* :func:`escalate_diagnostic_budget_to_ladder` — the
  ``DIAGNOSTIC_BUDGET_SPENT`` specialisation the diagnostic seam calls.

Every rung is idempotent and records intent before acting and outcome
after, so a restart mid-ladder resumes at the recorded rung without
repeating finished rungs or losing preserved copies. The owner's
vocabulary maps both typed outcomes onto ``NORMAL_CLEANUP`` — the
ladder ENTRY the startup boundary consumes — while this seam advances
the climb with the durable ledger's resume rule (the first rung without
a finished recorded outcome). Rung 1 (``NORMAL_CLEANUP``) is owned by
the startup boundary, not this seam: dispatching it through the ladder
records the typed ``failed:not-owned-here`` outcome, which counts as
"shown impossible" for the last-resort precondition and hands the climb
to the next rung.

The diagnostic handoff itself lives in
:mod:`ralph.pipeline._integration_diagnostic`; this module is the
routing seam it calls once the budget is spent.

Observed facts, not invocation reports
--------------------------------------

:func:`_invoke_and_judge` reports every attempt to the single owner
(:class:`ralph.recovery.agent_failure_ledger.AgentFailureLedger`
through the recovery controller) with the facts Ralph OBSERVES itself:
the operation-completion proof (the artifact on disk, checked against
live Git) and whether the workspace changed while the attempt ran (see
:func:`observe_workspace_change`). The invocation's own report is not a
fact -- it can announce failure after the operation completed and
success after nothing was produced -- so it never decides an agent's
standing: a verified completion clears the agent's history, and every
other way out is an unproductive attempt whose consequences (the
cooldown, the demotion, the next candidate) are the owner's decision.

The diagnostic-repair budget is a REPOSITORY-state bound, not an
agent-health bound: it counts the failures Ralph observes in the
repository and the durable record WITHOUT an agent attempt, and it is
those failures that escalate into the autonomous ladder. An agent
attempt never advances it, so no invocation count can decide the
integration's fate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_diagnostic_state import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
)
from ralph.pipeline.auto_integrate_record import BUMP_DIAGNOSTIC_ATTEMPT
from ralph.pipeline.conflict_resolution.resolver_health import (
    bench_resolver,
    choose_resolver,
    clear_resolver,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_ladder import (
    LadderStepResult,
    record_salvage_attempt,
    resume_rung,
    run_ladder_rung,
)
from ralph.pipeline.integration_owner import RecordFactOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.pipeline._integration_ladder_seams import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.operation_evidence import OperationEvidenceCollector
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "budget_spent_record",
    "escalate_diagnostic_budget_to_ladder",
    "escalate_record_fact_outcome",
    "observe_workspace_change",
]

#: The probe key the integration seam uses for its workspace baseline.
#: The conflict-resolution driver keys the SAME probe by
#: ``PHASE_RESOLUTION``; the integration seam needs a baseline of its
#: own so the two never overwrite (or inherit) each other's measurement.
_INTEGRATION_PROBE_KEY = "integration_resolution"


def observe_workspace_change(
    controller: RecoveryController,
    root: Path,
    attempt: Callable[[], None],
) -> bool:
    """Run ``attempt`` and report whether the workspace changed while it ran.

    The change is OBSERVED through the owner's own workspace probe (the
    same public seam the phase executor uses): a baseline is taken
    immediately before the attempt and compared immediately after it.

    An attempt that raises still propagates; the caller owns that
    verdict. When no probe is configured, or when the probe cannot
    measure, the attempt reports "nothing observed changed" -- the
    ledger's conservative default -- instead of inheriting a previous
    attempt's fact.
    """
    probe = controller.workspace_probe
    if probe is None:
        attempt()
        return False
    from ralph.workspace.fs import FsWorkspace

    workspace = FsWorkspace(root)
    try:
        probe.observe_before(_INTEGRATION_PROBE_KEY, workspace)
    except Exception as exc:  # pragma: no cover - defensive: the attempt still runs
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


def _next_resolver(controller: RecoveryController, agents: Sequence[str]) -> str | None:
    """Return the next resolver to call, or ``None`` (after saying why) while all are benched."""
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
    """Invoke ``agent`` and report what Ralph OBSERVED about the attempt.

    The invoke boolean is not a fact. An invocation can report failure
    after the operation completed, and report success after nothing was
    produced, so it never decides an agent's standing. The facts Ralph
    observes itself are:

    * the operation-completion proof -- the artifact on disk, checked
      against live Git; and
    * whether the workspace changed while the attempt ran.

    A verified completion CLEARS the agent's history (the attempt
    produced what was asked of it); every other way out is an
    unproductive attempt the owner charges, and the owner's consecutive
    and total limits decide when the agent is benched and which agent
    runs next. An invocation that raises is benched before the error
    propagates, so a crashing resolver cannot be re-offered on the next
    pass.
    """
    from ralph.pipeline._integration_diagnostic_proof import _check_operation_completion_proof

    def _attempt() -> None:
        invoke(agent, prompt)

    try:
        workspace_changed = observe_workspace_change(controller, inspection_root, _attempt)
    except Exception as exc:
        bench_resolver(controller, agent, exc)
        raise
    verified, _reason = _check_operation_completion_proof(inspection_root, record, collector=col)
    if verified:
        clear_resolver(controller, agent)
    else:
        bench_resolver(
            controller,
            agent,
            _failure_text(agent, failure_detail),
            workspace_changed=workspace_changed,
        )
    return verified


def _failure_text(
    agent: str, failure_detail: Callable[[], BaseException | str | None] | None
) -> BaseException | str:
    """The real failure of the attempt, or a statement that it produced no proof."""
    detail = failure_detail() if failure_detail is not None else None
    return detail or f"resolver '{agent}' ended without a verified completion proof"


def budget_spent_record(
    record: IntegrationRecord,
    *,
    reason: str = DIAGNOSTIC_BUDGET_SPENT_REASON,
    repair_attempt_cap: int = DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
) -> IntegrationRecord:
    """Stamp the typed "diagnostic budget spent" outcome on ``record``.

    Single authority for the durable-record stamp that replaces the
    retired ``RAISE_DIAGNOSTIC_INTERVENTION`` emission: the attempt
    counter is clamped at the cap (it never increments past it) and
    ``repair_cap_reason`` carries the typed outcome naming the next
    autonomous action -- the fallback ladder. The retired
    ``diagnostic_intervention_required`` hand-off flag stays ``False``.

    The counter it clamps is a REPOSITORY-state bound: it is advanced
    by the failures that happen WITHOUT an agent attempt (the
    fail-closed pre-pass, an unreadable diagnostic, a workspace with no
    repair agent installed), and it is those failures that escalate
    into the ladder. An agent attempt never advances it, so an
    invocation count can neither bench an agent nor decide the
    integration's fate.
    """

    return record.with_transition(
        BUMP_DIAGNOSTIC_ATTEMPT,
        merge_commit_repair_attempts=min(
            record.merge_commit_repair_attempts + 1, repair_attempt_cap
        ),
        repair_last_error=reason,
        repair_cap_reason=reason,
    )


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
) -> LadderStepResult:
    """Route one typed fail-closed record-fact outcome through the fallback ladder.

    Exactly one rung executes per call, ledger-charged and idempotent:

    * the rung is the durable ledger's own resume answer — the first
      rung without a finished recorded outcome — so repeated
      escalations climb rungs 1-6 in order and a restart resumes where
      the climb stopped. The owner's vocabulary maps the typed outcome
      to ``NORMAL_CLEANUP`` (the ladder ENTRY the startup boundary
      consumes); this seam advances the climb from wherever the
      durable ledger stands. When every rung has finished (the ladder
      is spent) the climb restarts at the last resort, whose six
      preconditions are re-evaluated and fail closed when unmet;
    * for ``DIAGNOSTIC_BUDGET_SPENT`` the resolver-agent salvage attempt
      the last-resort precondition (b) waits for is recorded as
      ``failed`` — the diagnostic rotation IS the resolver-agent
      salvage, and its budget is spent;
    * the single rate-limited log line names the rung, its outcome and
      the preserved copy (ref or sidecar) when the rung produced one.

    Unspecified seams resolve to the production adapters, so the
    diagnostic boundary runs any rung with one call; tests inject fakes.
    The caller re-observes before deciding again — acting twice on one
    observation is what the durable ledger's escalation budget exists
    to prevent.
    """
    ladder = ledger if ledger is not None else AttemptLedger(root)
    if outcome is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT:
        record_salvage_attempt(ladder, "resolver_agent", f"failed:{reason}")
    # The durable ledger's resume rule names the first rung without a
    # finished recorded outcome, so repeated escalations climb rungs
    # 1-6 in order and a restart resumes where the climb stopped. The
    # owner's vocabulary maps the typed outcome onto NORMAL_CLEANUP
    # (the ladder ENTRY the startup boundary consumes); this seam
    # advances the climb from wherever the durable ledger stands. When
    # every rung has finished (the ladder is spent) the climb restarts
    # at the last resort, whose six preconditions are re-evaluated and
    # fail closed when unmet.
    rung = resume_rung(ladder.read())
    if rung is None:
        rung = CleanupLadderRung.LAST_RESORT
    step = run_ladder_rung(
        rung,
        root=root,
        ledger=ladder,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target=record.target if target is None else target,
        branch=branch,
        feature_tip=feature_tip,
    )
    preserved = step.preserved_ref or step.preserved_sidecar or "no preserved copy yet"
    logger.warning(
        "Integration diagnostic budget spent; escalated autonomously to fallback "
        "ladder rung {} ({}): {}; preserved copy: {}; the run continues without "
        "waiting on a person",
        rung.value,
        step.outcome,
        step.reason,
        preserved,
    )
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
    """Route a spent diagnostic-repair budget into the autonomous fallback ladder.

    The ``DIAGNOSTIC_BUDGET_SPENT`` specialisation of
    :func:`escalate_record_fact_outcome`: the caller stamps the typed
    outcome on the durable record first (:func:`budget_spent_record`),
    then this function escalates it — the ladder preserves the work to
    backup refs first and ends in rung 6 (hard reset) as the last
    resort; every step is ledgered and never waits on a person.
    """
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
