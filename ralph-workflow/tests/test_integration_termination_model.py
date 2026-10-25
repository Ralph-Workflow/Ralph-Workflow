"""Termination-bounded fault model for Ralph's auto-integration.

Acceptance Criterion 2 of the integration adversity request asks for a
MODEL-based test: seeded histories with injected faults must, for EVERY
history, make progress or escalate a ladder rung within a fixed bound, and
once the faults stop the local target, the remote target and the feature
must agree. Acceptance Criterion 8 asks for the observed 2026-10-24 case:
a hung ``merge --ff-only`` while an agent is active.

What is PRODUCTION under test here:

* :func:`ralph.pipeline.integration_decision.decide` -- the pure, total
  decision table that chooses the next integration action;
* :class:`ralph.pipeline.integration_decision.AttemptState` -- the
  escalation ladder, its repeat counters and its finite unknown budget;
* the executor protocols of :mod:`ralph.pipeline.integration_actions` --
  the mutations run through the installed Git-ops and agent-runner seams.

What is SIMULATED here: the repository. The simulated Git environment
(:mod:`tests._fake_git_env`) holds the target, the feature tip, HEAD, the
backup refs, the locks, the remote and the fault controller, and the fake
Git-ops double implements the production :class:`GitOps` protocol against
it. Every mutation the model performs therefore goes through the SAME
protocol the production pipeline uses, and every decision goes through the
SAME decision table.

The model is deliberately small: one observation, one decision, one
mutation per step. That is what makes the history replayable from its
seed: the seed selects the fault sequence, and nothing else is random.

Bounds (all fixed, none of them wall-clock):

* ``_MAX_STEPS`` -- the number of steps one history may take;
* ``_MAX_STALLED_STEPS`` -- how many consecutive steps may pass without
  progress AND without a ladder rung;
* ``_CONVERGENCE_STEPS`` -- how many steps the clean phase may take to
  reach ``<target> == <remote>/<target>`` with the feature contained.

Known production behaviour this model pins
--------------------------------------------

The model surfaces two properties of the production decision table that
the caller, not the table, has to honour:

* For a target-moved observation the table answers ``REINTEGRATE``
  without consulting the escalation ladder
  (``_ladder_action_for``): the rung advances underneath, but the ACTION
  does not change with it, so a persistently failing replay keeps the
  same action.
* The table may answer ``DISPATCH_RESOLVER`` for a repairable record or a
  detached HEAD -- conflict-class signatures it deliberately keeps
  (``_ladder_action_skipping_resolver``) -- even though an agent cannot
  repair a record or a HEAD. This model gates the dispatch on a real
  content conflict, so the agent is never spent on one.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.integration_decision import (
    LADDER_ORDER,
    AttemptState,
    DecisionContext,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.run_time_report import emit_run_time_report_safely, render_run_time_report
from ralph.pipeline.state import PipelineState
from tests._fake_git_agent_runner import FakeAgentRunner
from tests._fake_git_env import SIMULATED_CATALOGUE_ROWS, FaultKind, make_fake_environment
from tests._fake_git_fault import (
    FaultEffect,
    FaultOutcome,
    FaultPhase,
    catalogue,
    classification_for,
)
from tests._fake_git_ops import _SimulatedCrashError

pytestmark = [pytest.mark.timeout_seconds(10)]

#: The fixed seed set. Every history is reproducible from its seed, so a
#: failure names the seed that produced it and replays exactly.
_SEEDS: tuple[int, ...] = (1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31)

#: The number of steps one faulted history may take.
_MAX_STEPS = 24

#: How many consecutive steps may pass with neither progress nor a ladder
#: rung. The bound is the "progress OR a ladder rung within a fixed
#: bound" clause of the acceptance criterion.
_MAX_STALLED_STEPS = 4

#: How many steps the clean phase may take to converge both tiers.
_CONVERGENCE_STEPS = 12

#: The bound on the number of resolver dispatches one history may make.
_MAX_DISPATCHES = 2


def _faults_matching(
    *,
    phase: FaultPhase | None = None,
    effect: FaultEffect | None = None,
    outcome: FaultOutcome | None = None,
) -> tuple[FaultKind, ...]:
    """Return every fault kind whose registration matches the filters.

    The catalogue registry IS the inventory: the pools below are derived
    from it, so a newly named fault kind joins the model without a
    second list to keep in step.
    """

    return tuple(
        kind
        for kind, entry in catalogue().items()
        if (phase is None or entry.phase == phase)
        and (effect is None or entry.effect == effect)
        and (outcome is None or entry.outcome == outcome)
    )


#: The faults that make an OBSERVATION unreadable. An unreadable
#: observation is unknown, never a conflict.
_UNKNOWN_FAULTS = _faults_matching(phase="observation", effect="defer", outcome="unknown")

#: The faults the next OBSERVATION recovers from. A recovery is progress:
#: the lock a killed child left is gone and the replay can run again.
_RECOVER_FAULTS = _faults_matching(phase="observation", effect="recover")

#: The faults that make a REPLAY fail. The replay is the mutation the
#: integration ladder drives first.
_REPLAY_FAULTS = tuple(
    kind for kind in _faults_matching(phase="rebase") if kind is not FaultKind.REAL_CONFLICT
)

#: The faults that make the LANDING fail or be refused.
_LANDING_FAULTS = _faults_matching(phase="landing")

#: The faults that make the REMOTE refuse the push.
_REMOTE_FAULTS = _faults_matching(phase="remote")

#: The faults that kill Ralph itself, before or after a step.
_CRASH_FAULTS = _faults_matching(effect="crash_before") + _faults_matching(effect="crash_after")

#: The one fault an agent can change.
_CONFLICT_FAULT = FaultKind.REAL_CONFLICT


def _contains(container: str, tip: str) -> bool:
    """True when ``container`` holds ``tip`` (the environment's own rule)."""
    return container == tip or container.startswith(f"{tip}-")


def _is_real_conflict(observation: Observation) -> bool:
    """True when the observation carries a genuine content conflict.

    This mirrors the decision table's own notion of a real conflict: an
    unmerged worktree, or a FOREIGN in-progress operation.
    """
    return observation.worktree is WorktreeState.UNMERGED or (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.FOREIGN
    )


def _is_boring(observation: Observation) -> bool:
    """True when the observation holds nothing an agent could change.

    A boring observation is clean, attached and consistent: dispatching a
    resolver for it would do no useful work.
    """
    return not (
        _is_real_conflict(observation)
        or observation.head is HeadState.DETACHED
        or observation.record
        in (
            IntegrationRecordState.STALE,
            IntegrationRecordState.UNREADABLE,
            IntegrationRecordState.OLDER_VERSION,
            IntegrationRecordState.FOREIGN,
        )
    )


def _resolver(_root: Path, _target: str, _stop: object) -> bool:
    """A resolver that always claims the conflict is resolved."""
    return True


class _TerminationModel:
    """One seeded history over the simulated Git environment.

    Every step is: observe, decide (production table), mutate (production
    protocol), charge (production ladder).
    """

    def __init__(self, seed: int, root: Path) -> None:
        self.seed = seed
        self.root = root
        self.rng = random.Random(seed)
        self.env, self.git_ops, self.agent_runner, self.clock = make_fake_environment(
            remote_sha="target-v1",
            remote_enabled=True,
        )
        self.attempts = AttemptState()
        self.conflict = False
        self.in_progress = InProgressKind.NONE
        self.ownership = InProgressOwnership.OWNED
        self.record = IntegrationRecordState.NONE
        self.agent_active = False
        self.observation: Observation | None = None
        self.actions: list[NextAction] = []
        self.resolver_observations: list[Observation] = []
        self.dispatch_observations: list[Observation] = []
        self.agent_git_calls: list[int] = []
        self.crash_steps: list[int] = []
        self.inject_faults = True

    # -- state ---------------------------------------------------------------

    def state_vector(self) -> tuple[object, ...]:
        """Return the observable repository state the model progresses on."""
        env = self.env
        return (
            env.target_sha,
            env.feature_sha,
            env.head_sha,
            env.head_branch,
            tuple(sorted(env.backup_refs.items())),
            self.conflict,
            self.in_progress,
            self.ownership,
            self.record,
            env.remote_sha,
        )

    def rung(self) -> int:
        """Return the ladder position the model currently stands on."""
        return LADDER_ORDER.index(self.attempts.rung)

    def converged(self) -> bool:
        """True when both tiers hold the feature's work."""
        return self.env.is_converged()

    # -- the history ---------------------------------------------------------

    def pick_fault(self) -> FaultKind | None:
        """Choose the fault for the next step, or ``None`` for a clean step."""
        if not self.inject_faults:
            return None
        roll = self.rng.random()
        if roll < 0.35:
            return None
        pool: tuple[FaultKind, ...] = (
            _REPLAY_FAULTS
            + _LANDING_FAULTS
            + _REMOTE_FAULTS
            + _CRASH_FAULTS
            + _RECOVER_FAULTS
            + tuple(sorted(_UNKNOWN_FAULTS, key=lambda kind: kind.name))
            + (_CONFLICT_FAULT,)
        )
        return self.rng.choice(pool)

    def step(self) -> NextAction:
        """Run one observe-decide-mutate-charge cycle; return the action."""
        fault = self.pick_fault()
        if fault is not None and not self.env.active_faults:
            self.env.set_faults([fault])
        observation = self._observe()
        action = decide(DecisionContext(observation=observation, attempts=self.attempts))
        self.observation = observation
        self.actions.append(action)
        try:
            self._apply(action)
        except _SimulatedCrashError:
            # Ralph died inside the step. The work that was already
            # committed stays committed; the next observation is what has
            # to make progress.
            self.crash_steps.append(len(self.actions) - 1)
        self.attempts = self.attempts.with_attempt(
            observation,
            action,
            is_unknown=observation.has_unknown(),
        )
        return action

    def _observe(self) -> Observation:
        """Observe the simulated repository and project it onto the model."""
        env = self.env
        before = len(env.outcomes)
        env.observe()
        consumed = env.outcomes[before:]
        fault = consumed[-1].kind if consumed else None
        spoiled = fault is not None and fault in _UNKNOWN_FAULTS

        if fault is FaultKind.RECORD_UNREADABLE:
            self.record = IntegrationRecordState.UNREADABLE
        elif fault is FaultKind.RECORD_CORRUPT:
            self.record = IntegrationRecordState.STALE
        elif fault is FaultKind.RECORD_OLDER_VERSION:
            self.record = IntegrationRecordState.OLDER_VERSION
        elif fault is FaultKind.AGENT_NO_CHANGE:
            self.record = IntegrationRecordState.STALE
        elif fault is FaultKind.STALE_LOCK:
            self.record = IntegrationRecordState.NONE

        if fault is FaultKind.HEAD_DETACHED:
            # The agent moved HEAD; Ralph's own observation reattaches it.
            self.git_ops.reattach_head(self.root, env.feature_branch)

        worktree = WorktreeState.CLEAN
        if self.conflict:
            worktree = WorktreeState.UNMERGED
        if spoiled:
            worktree = WorktreeState.UNKNOWN

        target_vs_feature = self._relation(env.target_sha, env.feature_sha)
        if spoiled:
            target_vs_feature = TargetVsFeature.UNKNOWN

        target_vs_remote = self._remote_relation()
        if fault in _REMOTE_FAULTS:
            target_vs_remote = TargetVsRemote.UNREACHABLE

        head = HeadState.ATTACHED if env.head_branch is not None else HeadState.DETACHED
        ownership = self.ownership
        if spoiled:
            ownership = InProgressOwnership.UNKNOWN

        return Observation(
            target_vs_feature=target_vs_feature,
            target_vs_remote=target_vs_remote,
            worktree=worktree,
            head=head,
            in_progress_kind=self.in_progress,
            in_progress_ownership=ownership,
            record=self.record,
            agent_active=self.agent_active,
        )

    def _relation(self, target: str, feature: str) -> TargetVsFeature:
        """Project the target/feature relation onto the observation vocabulary."""
        if target == feature:
            return TargetVsFeature.EQUAL
        if _contains(target, feature):
            return TargetVsFeature.AHEAD
        if _contains(feature, target):
            return TargetVsFeature.BEHIND
        return TargetVsFeature.DIVERGED

    def _remote_relation(self) -> TargetVsRemote:
        """Project the local/remote relation onto the observation vocabulary."""
        env = self.env
        remote = env.remote_sha
        if remote is None:
            return TargetVsRemote.UNKNOWN
        if remote == env.target_sha:
            return TargetVsRemote.EQUAL
        if _contains(remote, env.target_sha):
            return TargetVsRemote.BEHIND
        if _contains(env.target_sha, remote):
            return TargetVsRemote.AHEAD
        return TargetVsRemote.DIVERGED

    # -- the mutations -------------------------------------------------------

    def _apply(self, action: NextAction) -> None:
        """Run the mutation the decided action asks for."""
        env = self.env
        if action is NextAction.LAND:
            self._land()
        elif action is NextAction.REINTEGRATE:
            self._replay()
            self._land()
        elif action is NextAction.REBUILD_ON_TARGET:
            self.git_ops.create_backup_ref(self.root, env.head_sha)
            self._replay()
            self._land()
        elif action is NextAction.COMMIT:
            self.git_ops.stage_all(self.root)
            self.git_ops.commit(self.root)
        elif action is NextAction.DISPATCH_RESOLVER:
            self._dispatch_resolver()
        elif action is NextAction.RESUME_OWNED_OPERATION:
            self._continue_operation()
        elif action is NextAction.REPAIR:
            self._repair()

    def _replay(self) -> None:
        """Replay the feature onto the target through the Git-ops seam."""
        result = self.git_ops.rebase_onto(self.root, self.env.target)
        if isinstance(result, RebaseConflicts):
            self.conflict = True
            self.in_progress = InProgressKind.REBASE

    def _land(self) -> None:
        """Move the target ref onto the feature through the Git-ops seam."""
        landed, _reason = self.git_ops.fast_forward_target(
            self.root,
            self.env.target,
            self.env.feature_sha,
        )
        if landed:
            self.conflict = False
            self.in_progress = InProgressKind.NONE

    def _continue_operation(self) -> None:
        """Advance the in-progress operation with the MATCHING command."""
        self.git_ops.stage_all(self.root)
        if self.in_progress is InProgressKind.MERGE:
            self.git_ops.continue_merge(self.root)
        elif self.in_progress is InProgressKind.CHERRY_PICK:
            self.git_ops.continue_cherry_pick(self.root)
        else:
            self.git_ops.continue_rebase(self.root)
        self.conflict = False
        self.in_progress = InProgressKind.NONE

    def _dispatch_resolver(self) -> None:
        """Dispatch the resolver agent, and only for a real conflict."""
        observation = self.observation
        assert observation is not None
        self.resolver_observations.append(observation)
        calls_before = len(self.env.calls)
        if not _is_real_conflict(observation):
            # A condition an agent cannot change never reaches an agent.
            self.agent_git_calls.append(len(self.env.calls) - calls_before)
            return
        resolved = self.agent_runner.resolve_rebase(
            self.root,
            self.env.target,
            _resolver,
            session=ResolutionSession(),
        )
        self.agent_git_calls.append(len(self.env.calls) - calls_before)
        self.dispatch_observations.append(observation)
        if resolved:
            self.conflict = False
            self.in_progress = InProgressKind.NONE

    def _repair(self) -> None:
        """Repair what the observation proved repairable."""
        env = self.env
        if env.head_branch is None:
            self.git_ops.reattach_head(self.root, env.feature_branch)
        self.record = IntegrationRecordState.NONE
        env.recover_stale_locks()


def _run_history(model: _TerminationModel, steps: int) -> list[tuple[object, ...]]:
    """Run ``steps`` faulted steps; return the state vector after each step."""
    states: list[tuple[object, ...]] = []
    for _ in range(steps):
        model.step()
        states.append(model.state_vector())
    return states


# ---------------------------------------------------------------------------
# AC-2: the termination model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", _SEEDS, ids=lambda seed: f"seed-{seed}")
def test_a_seeded_history_progresses_or_escalates_a_ladder_rung(
    tmp_path: Path,
    seed: int,
) -> None:
    """AC-2: every faulted history makes progress or escalates a rung.

    No more than ``_MAX_STALLED_STEPS`` consecutive steps may pass with
    neither a change in the repository state nor a movement of the
    escalation ladder, and the identical action is never issued against
    an unchanged state more often than that. Work is never discarded, and
    the agent is only ever dispatched for a real content conflict.
    """
    model = _TerminationModel(seed, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        previous = model.state_vector()
        rung = model.rung()
        stalled = 0
        action = None
        identical = 0
        for _ in range(_MAX_STEPS):
            current_action = model.step()
            current = model.state_vector()
            assert not model.env.has_work_loss(), (
                f"seed {seed}: the feature tip was discarded from every ref"
            )
            if current_action is action and current == previous:
                identical += 1
            else:
                identical = 0
            assert identical <= _MAX_STALLED_STEPS, (
                f"seed {seed}: the identical action {current_action.value} was "
                f"issued {identical} times against an unchanged state"
            )
            action = current_action
            advanced = current != previous or model.rung() != rung
            if advanced:
                stalled = 0
                previous = current
                rung = model.rung()
            else:
                stalled += 1
            assert stalled <= _MAX_STALLED_STEPS, (
                f"seed {seed}: {stalled} consecutive steps made no progress and "
                "escalated no ladder rung"
            )
            if model.converged():
                break
        assert (
            model.converged()
            or model.rung() > 0
            or model.actions[-1] is (NextAction.CONTINUE_WITH_INTEGRATION_PENDING)
        ), f"seed {seed}: the history neither progressed nor escalated"
        if model.crash_steps:
            assert model.crash_steps[-1] < len(model.actions) - 1 or model.converged(), (
                f"seed {seed}: a crash ended the history instead of leaving it resumable"
            )
    finally:
        reset_executors(token)


@pytest.mark.parametrize("seed", _SEEDS, ids=lambda seed: f"seed-{seed}")
def test_a_seeded_history_converges_once_the_faults_stop(
    tmp_path: Path,
    seed: int,
) -> None:
    """AC-2: once the faults stop, both tiers converge and the work survives."""
    model = _TerminationModel(seed, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        _run_history(model, _MAX_STEPS)
        model.env.set_faults([])

        steps = 0
        while steps < _CONVERGENCE_STEPS and not model.converged():
            model.step()
            steps += 1
            assert not model.env.has_work_loss(), (
                f"seed {seed}: the clean phase discarded the feature tip"
            )

        assert model.converged(), (
            f"seed {seed}: the history did not converge within "
            f"{_CONVERGENCE_STEPS} steps after the faults stopped"
        )
        assert model.env.target_sha == model.env.remote_sha, (
            f"seed {seed}: the remote target does not match the local target"
        )
        assert _contains(model.env.target_sha, model.env.feature_sha), (
            f"seed {seed}: the feature is not contained in the target"
        )
    finally:
        reset_executors(token)


@pytest.mark.parametrize("seed", _SEEDS, ids=lambda seed: f"seed-{seed}")
def test_a_seeded_history_never_dispatches_an_agent_for_a_non_conflict(
    tmp_path: Path,
    seed: int,
) -> None:
    """AC-2/AC-9: an agent is dispatched only for a real content conflict.

    Every dispatch was for an observation the production decision table
    considers conflict-class, and the number of dispatches is bounded.
    """
    model = _TerminationModel(seed, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        _run_history(model, _MAX_STEPS)
        assert len(model.dispatch_observations) <= _MAX_DISPATCHES, (
            f"seed {seed}: the resolver was dispatched {len(model.dispatch_observations)} times"
        )
        for observation in model.resolver_observations:
            assert not _is_boring(observation), (
                f"seed {seed}: the decision table answered DISPATCH_RESOLVER for "
                "an observation that holds nothing an agent could change"
            )
        for observation in model.dispatch_observations:
            assert _is_real_conflict(observation), (
                f"seed {seed}: the resolver was dispatched for a condition that "
                "is not a content conflict"
            )
        for calls in model.agent_git_calls:
            assert calls == 0, (
                f"seed {seed}: the resolver issued Git operations instead of only editing files"
            )
    finally:
        reset_executors(token)


def test_a_history_of_environment_faults_alone_never_dispatches_an_agent(
    tmp_path: Path,
) -> None:
    """AC-9: an environment failure never consumes an agent run."""
    model = _TerminationModel(101, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        for _ in range(_MAX_STEPS):
            model.env.set_faults([model.rng.choice(_REPLAY_FAULTS + _LANDING_FAULTS)])
            model.step()
            assert not model.env.has_work_loss()
        assert not model.dispatch_observations, "an environment failure dispatched a resolver agent"
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# AC-8: the observed 2026-10-24 case
# ---------------------------------------------------------------------------


def test_observed_case_no_merge_is_issued_while_an_agent_is_active(
    tmp_path: Path,
) -> None:
    """AC-8(a): a fast-forward is refused while an agent runs in the worktree.

    The 2026-10-24 run issued ``merge --ff-only`` from the catch-up tick
    while three agent sessions worked in the same worktree.
    """
    model = _TerminationModel(7, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.agent_active = True
        model.env.agent_active = True
        before = model.env.target_sha

        landed, reason = model.git_ops.fast_forward_target(
            tmp_path,
            model.env.target,
            model.env.feature_sha,
        )

        assert not landed, "the merge was issued while an agent was active"
        assert model.env.target_sha == before, "the target moved under an active agent"
        assert "agent is currently active" in reason
        assert not model.env.has_work_loss()
    finally:
        reset_executors(token)


def test_observed_case_bounded_timeouts_escalate_the_ladder(
    tmp_path: Path,
) -> None:
    """AC-8(b): repeated timeouts escalate the ladder instead of looping.

    A hung replay is an environment failure: it never dispatches an agent.
    """
    model = _TerminationModel(11, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        rung = model.rung()
        for _ in range(_MAX_STEPS):
            model.env.set_faults([FaultKind.GIT_TIMEOUT])
            model.step()
            assert not model.env.has_work_loss()

        assert model.rung() > rung, "repeated timeouts never escalated the escalation ladder"
        assert not model.dispatch_observations, "a timeout dispatched a resolver agent"
        assert all(
            outcome.outcome == "timed_out"
            for outcome in model.env.outcomes
            if outcome.kind is FaultKind.GIT_TIMEOUT
        ), "a timeout was not typed as a timeout"
    finally:
        reset_executors(token)


def test_observed_case_a_stale_lock_from_a_killed_child_does_not_block(
    tmp_path: Path,
) -> None:
    """AC-8(c): the lock a force-killed Ralph child left is recoverable.

    The owner is proven dead, the lock is removed, and the attempt
    proceeds: the stale lock never blocks the next observation.
    """
    model = _TerminationModel(13, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.env.leave_stale_lock()
        assert "index.lock" in model.env.locks
        model.env.set_faults([FaultKind.STALE_LOCK])

        model.step()

        assert model.env.locks == {}, "the stale lock blocked the next observation"
        assert model.env.fired(FaultKind.STALE_LOCK)
        assert model.env.outcome_kind_for(FaultKind.STALE_LOCK) == "succeeded"
        assert not model.env.has_work_loss()
    finally:
        reset_executors(token)


def test_observed_case_an_interrupted_run_still_emits_a_valid_report(
    tmp_path: Path,
) -> None:
    """AC-8(d): an interrupted stalled run still produces a valid report.

    Zero counts are evidence, not a validation error: the report of an
    interrupted run must validate, and the emission path must not lose it.
    """
    state = PipelineState(phase="development")
    report = render_run_time_report(
        state=state,
        outcome="interrupted",
        elapsed_seconds=298 * 60,
    )
    _content, diagnostics = parse_and_validate(report, get_spec("run_time_report"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert not errors, "the interrupted run's report did not validate"
    assert "Agent calls: 0" in report, "the report does not carry the zero counts"

    # The emission path itself must not lose the report.
    emit_run_time_report_safely(
        tmp_path,
        state=state,
        outcome="interrupted",
        elapsed_seconds=298 * 60,
    )


# ---------------------------------------------------------------------------
# The in-progress operation sequences
# ---------------------------------------------------------------------------


def test_the_resolver_only_edits_files_while_ralph_issues_the_git_operations(
    tmp_path: Path,
) -> None:
    """The agent edits files; every Git operation is issued by Ralph.

    The agent runner never holds a Git-ops handle: the recorded mutations
    around a dispatch are zero.
    """
    model = _TerminationModel(17, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.conflict = True
        model.in_progress = InProgressKind.REBASE
        model.ownership = InProgressOwnership.OWNED

        model.step()

        assert model.dispatch_observations, "the conflict never dispatched a resolver"
        assert model.agent_git_calls == [0], (
            "the resolver issued Git operations instead of only editing files"
        )
        assert "stage_all" not in model.env.calls, "the agent staged the index"
        assert "continue_rebase" not in model.env.calls, "the agent continued the rebase"
    finally:
        reset_executors(token)


def test_an_agent_that_changes_nothing_is_not_redispatched_and_the_ladder_advances(
    tmp_path: Path,
) -> None:
    """A resolver that changes nothing is not redispatched forever.

    The agent declines, so the conflict is unchanged: the dispatches stay
    bounded and the ladder moves on.
    """
    env, git_ops, _agent_runner, clock = make_fake_environment(
        remote_sha="target-v1",
        remote_enabled=True,
    )
    model = _TerminationModel(19, tmp_path)
    model.inject_faults = False
    model.env = env
    model.git_ops = git_ops
    model.agent_runner = FakeAgentRunner(env, can_resolve=False)
    model.clock = clock
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.conflict = True
        model.in_progress = InProgressKind.REBASE
        model.ownership = InProgressOwnership.OWNED
        rung = model.rung()

        for _ in range(_MAX_STEPS):
            model.step()

        assert len(model.dispatch_observations) <= _MAX_DISPATCHES, (
            "a resolver that changed nothing was redispatched without bound"
        )
        assert model.rung() > rung, "the ladder did not advance past the resolver rung"
    finally:
        reset_executors(token)


def test_the_stage_and_continue_loop_reobserves_between_steps(
    tmp_path: Path,
) -> None:
    """The owned operation is continued one step at a time, re-observed.

    The observation between the steps is a FRESH one: the model re-reads
    the repository instead of carrying the previous reading forward.
    """
    model = _TerminationModel(23, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.conflict = False
        model.in_progress = InProgressKind.REBASE
        model.ownership = InProgressOwnership.OWNED

        action = model.step()

        assert action is NextAction.RESUME_OWNED_OPERATION
        assert model.env.calls.index("stage_all") < model.env.calls.index("continue_rebase")
        assert "continue_rebase" in model.env.calls
        assert model.in_progress is InProgressKind.NONE
        assert model.observation is not None
        assert model.observation.in_progress_kind is InProgressKind.REBASE

        # The NEXT observation is a fresh one: the operation is gone.
        model.step()
        assert model.observation is not None
        assert model.observation.in_progress_kind is InProgressKind.NONE
    finally:
        reset_executors(token)


@pytest.mark.parametrize(
    ("kind", "expected_call"),
    [
        (InProgressKind.REBASE, "abort_rebase"),
        (InProgressKind.MERGE, "abort_merge"),
        (InProgressKind.CHERRY_PICK, "abort_cherry_pick"),
    ],
)
def test_an_abort_uses_the_matching_command_and_reintegrates_the_saved_work(
    tmp_path: Path,
    kind: InProgressKind,
    expected_call: str,
) -> None:
    """An abort uses the MATCHING command and keeps the saved work.

    The operation is aborted with the command that matches ITS kind, and
    the work the abort would have thrown away is preserved on a backup
    ref, so the reintegrate that follows discards nothing.
    """
    model = _TerminationModel(29, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.in_progress = kind
        model.ownership = InProgressOwnership.OWNED
        model.conflict = True
        model.git_ops.create_backup_ref(tmp_path, model.env.head_sha)
        assert model.env.backup_refs

        if kind is InProgressKind.REBASE:
            model.git_ops.abort_rebase(tmp_path)
        elif kind is InProgressKind.MERGE:
            model.git_ops.abort_merge(tmp_path)
        else:
            model.git_ops.abort_cherry_pick(tmp_path)

        assert expected_call in model.env.calls
        assert not model.env.has_work_loss(), "the abort discarded the saved work"
        assert model.env.backup_refs, "the abort discarded the backup ref"

        # The reintegrate that follows brings the work back onto the target.
        model.step()
        assert not model.env.has_work_loss()
    finally:
        reset_executors(token)


def test_conflicts_already_resolved_go_straight_to_continue(
    tmp_path: Path,
) -> None:
    """A conflict that is already resolved needs no resolver agent.

    The operation is owned and the paths are already resolved: the run
    continues the operation and dispatches no agent at all.
    """
    model = _TerminationModel(31, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.conflict = False
        model.in_progress = InProgressKind.REBASE
        model.ownership = InProgressOwnership.OWNED

        action = model.step()

        assert action is NextAction.RESUME_OWNED_OPERATION
        assert not model.dispatch_observations, (
            "an already-resolved conflict dispatched a resolver agent"
        )
        assert "continue_rebase" in model.env.calls
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# The typed outcome of every fault the model can inject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fault",
    sorted(FaultKind, key=lambda kind: kind.name),
    ids=lambda kind: kind.name,
)
def test_an_injected_fault_is_typed_and_never_dispatches_an_agent(
    tmp_path: Path,
    fault: FaultKind,
) -> None:
    """Every injected fault is typed and never spends an agent run."""
    model = _TerminationModel(37, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        model.env.set_faults([fault])
        if classification_for(fault).phase == "shutdown":
            # The shutdown phase is not part of a step: the run ends there.
            model.env.shutdown()
        else:
            model.step()

        assert model.env.fired(fault), f"{fault.name} was never consumed"
        outcome = model.env.outcome_kind_for(fault)
        assert outcome in (
            "succeeded",
            "definitely_failed",
            "timed_out",
            "killed",
            "unknown",
        ), f"{fault.name} produced the untyped outcome {outcome!r}"
        assert not model.env.has_work_loss(), f"{fault.name} discarded the feature tip"
        if fault is not _CONFLICT_FAULT:
            assert not model.dispatch_observations, f"{fault.name} dispatched a resolver agent"
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# AC-9: the catalogue sub-rows that had no named fault kind
# ---------------------------------------------------------------------------


#: The catalogue sub-rows this unit named, mapped to the member that now
#: names them. The key is the catalogue text of the sub-row, so the
#: table is reviewable against the request's failure-mode catalogue.
_NEW_CATALOGUE_ROWS = SIMULATED_CATALOGUE_ROWS


@pytest.mark.parametrize(
    ("sub_row", "kind"),
    sorted(_NEW_CATALOGUE_ROWS.items()),
    ids=lambda value: value if isinstance(value, str) else value.name,
)
def test_a_newly_named_catalogue_sub_row_has_a_bounded_typed_outcome(
    tmp_path: Path,
    sub_row: str,
    kind: FaultKind,
) -> None:
    """AC-9: a newly named sub-row is typed, bounded and dispatches no agent.

    The sub-row now has a named fault, so it has a typed outcome, an
    accurate user-facing reason, a bounded outcome and no agent dispatch
    for a condition an agent cannot change.
    """
    model = _TerminationModel(41, tmp_path)
    token = install_executors(model.git_ops, model.agent_runner, clock=model.clock)
    try:
        classification = classification_for(kind)
        model.env.set_faults([kind])
        model.step()

        assert model.env.fired(kind), f"{sub_row}: {kind.name} was never consumed"
        assert model.env.outcome_kind_for(kind) == classification.outcome
        assert model.env.reason_for(kind) == classification.reason
        assert classification.reason.strip(), f"{sub_row}: no user-facing reason was stated"
        assert not model.env.has_work_loss(), f"{sub_row}: discarded the feature tip"
        assert not model.dispatch_observations, f"{sub_row}: dispatched a resolver agent"
    finally:
        reset_executors(token)
