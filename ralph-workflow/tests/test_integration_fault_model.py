"""Fault-injecting termination model test driving integration executor seams.

Satisfies Acceptance Criteria 2, 7, 8, and 9:
- Bounded histories with fixed seeds demonstrating termination and convergence
- No work loss (feature commit preserved via target or backup ref)
- No useless agent dispatch (agents invoked only for content conflicts)
- Reproduction and validation of the 2026-10-24 catch-up stall incident
- Catalogue coverage for named external and environmental faults
- AC-7: every wait runs on an INJECTED clock, so no test sleeps for real
"""

from __future__ import annotations

import random
from collections import OrderedDict, deque
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git._worktree_lease import (
    LeaseClock,
    default_lease_clock,
    lease_age_seconds,
    lease_payload,
)
from ralph.pipeline import (
    IntegrationAction,
    integration_director,
    integration_owner_seam,
)
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_backoff import RETRY_BASE_DELAY_SECONDS
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.integration_actions import (
    current_clock,
    current_git_ops,
    default_clock,
    install_executors,
    reset_executors,
)
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    LADDER_ORDER,
    AttemptState,
    CleanupLadderRung,
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
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    read_in_progress_kind,
)
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._fake_git_agent_runner import FakeAgentRunner
from tests._fake_git_clock import FakeClock
from tests._fake_git_env import (
    FaultKind,
    SimulatedGitEnvironment,
    make_fake_environment,
)
from tests._fake_git_ops import FakeGitOps

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult

pytestmark = [pytest.mark.timeout_seconds(10)]

_SEEDS = (42, 101, 2024, 9999, 1337)

#: Cap on the waits one recording clock remembers. A bounded FIFO keeps
#: the recorder from growing with the number of attempts it witnesses.
_MAX_RECORDED_WAITS = 64

#: The wait the production back-off takes before the FIRST retry: the
#: exponential step with FULL jitter, drawn at its lowest spread.
_EXPECTED_FIRST_BACKOFF = RETRY_BASE_DELAY_SECONDS * 0.5

#: The reason the production fast-forward reports when the target moved
#: under the landing. It is the reason that earns a bounded retry, and a
#: bounded retry is what waits.
_LANDING_RACE_REASON = "target advanced concurrently (not an ancestor of feature)"


def _zero_jitter() -> float:
    """A constant jitter source, so the back-off schedule is deterministic."""
    return 0.0


class _RecordingClock(FakeClock):
    """The fake clock, extended to RECORD every wait it is asked to perform.

    The record IS the assertion: a production back-off that waits on this
    clock leaves its schedule here, so a test can prove the schedule
    without waiting for it.
    """

    def __init__(self, initial_time: float = 1000.0) -> None:
        super().__init__(initial_time)
        self.slept: deque[float] = deque(maxlen=_MAX_RECORDED_WAITS)

    def sleep(self, seconds: float) -> None:
        """Record the wait and move the fake clock forward."""
        self.slept.append(seconds)
        super().sleep(seconds)


class _LandingRaceOps(FakeGitOps):
    """The fake Git ops, with the landing race the retry ladder answers.

    The simulated environment's own landing failure is not one of the
    production retryable reasons, so this double reports the reason the
    production fast-forward reports when the target moved under the
    landing: the one reason that earns a bounded retry -- and therefore
    a bounded wait.
    """

    def __init__(self, env: SimulatedGitEnvironment) -> None:
        super().__init__(env)
        self.landing_races = 1

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        """Lose the first landing race; win every one after it."""
        if self.landing_races > 0:
            self.landing_races -= 1
            self.env.calls.append("fast_forward_target")
            return False, _LANDING_RACE_REASON
        return super().fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get_env() -> SimulatedGitEnvironment | None:
        ops = current_git_ops()
        return getattr(ops, "env", None)

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda *_args, **_kwargs: _get_env().target if _get_env() else "main",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._current_branch_or_detached_marker",
        lambda *_args, **_kwargs: _get_env().feature_branch if _get_env() else "feature",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._refresh_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.branch_sha",
        lambda *_args, **_kwargs: _get_env().target_sha if _get_env() else "target-sha",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.get_head_sha",
        lambda *_args, **_kwargs: _get_env().head_sha if _get_env() else "feature-sha",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.check_rebase_preconditions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.observe_conflict_identity",
        lambda *_args, **_kwargs: ConflictIdentity(
            feature_sha=_get_env().feature_sha if _get_env() else "feature-sha",
            target_sha=_get_env().target_sha if _get_env() else "target-sha",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._recover_before_attempt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )

    def _fake_contained_land(*_args: object, **_kwargs: object) -> RebaseState | None:
        env = _get_env()
        if env and env.is_converged():
            return RebaseState(
                target_branch=env.target,
                fast_forwarded=True,
                last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            )
        return None

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.contained_land",
        _fake_contained_land,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.ABSENT,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._clear_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha",
        lambda *_args, **_kwargs: _get_env().head_sha if _get_env() else "feature-sha",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.run_git",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="unavailable",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge._clear_record_if_no_inflight_op",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        lambda *_args, **_kwargs: None,
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_fault_model_bounded_histories_converge_without_work_loss(
    tmp_path: Path,
    seed: int,
) -> None:
    """Fixed-seed fault injection terminates, preserves work, and converges once faults abate."""
    rng = random.Random(seed)
    env, git_ops, agent_runner, clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner, clock=clock)

    try:
        config = UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": env.target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        )
        scope = WorkspaceScope(tmp_path)

        # Fault phase: inject transient adversity for up to 5 attempts
        fault_pool = [
            FaultKind.GIT_TIMEOUT,
            FaultKind.LOCK_CONTENTION,
            FaultKind.CONCURRENT_TARGET_ADVANCE,
            FaultKind.REAL_CONFLICT,
        ]
        history_steps = 0
        state: RebaseState = RebaseState()

        while history_steps < 5 and not env.is_converged():
            chosen_fault = rng.choice(fault_pool)
            env.set_faults([chosen_fault])
            out = auto_integrate_after_commit(
                config,
                scope,
                state,
                rebase_stop_resolver=lambda *_a, **_kw: True,
                sleep=clock.sleep,
                jitter=_zero_jitter,
            )
            if out is not None:
                state = out
            history_steps += 1

            # Invariant: work must never be lost at any step
            assert not env.has_work_loss(), f"Work lost at step {history_steps} for seed {seed}"

        # Clean phase: once faults clear, bounded progression must achieve convergence
        env.set_faults([])
        clean_steps = 0
        while clean_steps < 3 and not env.is_converged():
            out = auto_integrate_after_commit(
                config,
                scope,
                state,
                rebase_stop_resolver=lambda *_a, **_kw: True,
                sleep=clock.sleep,
                jitter=_zero_jitter,
            )
            if out is not None:
                state = out
            clean_steps += 1
            assert not env.has_work_loss()

        assert env.is_converged(), f"Failed to converge within bound for seed {seed}"
    finally:
        reset_executors(token)


def test_observed_incident_no_merge_during_active_agent(tmp_path: Path) -> None:
    """AC-8(a): Refuses worktree mutation while an agent is active in that worktree."""
    env, git_ops, agent_runner, clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner, clock=clock)
    try:
        env.agent_active = True
        landed, reason = git_ops.fast_forward_target(tmp_path, env.target, env.feature_sha)
        assert not landed
        assert "refused: agent is currently active" in reason
    finally:
        reset_executors(token)


def test_observed_incident_bounded_timeouts_advance_ladder(tmp_path: Path) -> None:
    """AC-8(b): Repeated timeouts do not loop unbounded and do not dispatch useless agents."""
    env, git_ops, agent_runner, clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner, clock=clock)
    try:
        config = UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": env.target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        )
        scope = WorkspaceScope(tmp_path)

        # Inject persistent timeout faults on rebase
        state: RebaseState = RebaseState()
        for _ in range(4):
            env.set_faults([FaultKind.GIT_TIMEOUT])
            out = auto_integrate_after_commit(
                config,
                scope,
                state,
                sleep=clock.sleep,
                jitter=_zero_jitter,
            )
            if out is not None:
                state = out

        # Invariant: timeouts are environmental, so no resolver agent was dispatched
        assert "resolve_rebase" not in env.dispatches
        assert not env.has_work_loss()
    finally:
        reset_executors(token)


@pytest.mark.parametrize(
    ("fault", "expected_mutation"),
    [
        (FaultKind.GIT_TIMEOUT, "rebase_onto"),
        (FaultKind.LOCK_CONTENTION, "rebase_onto"),
        (FaultKind.CONCURRENT_TARGET_ADVANCE, "fast_forward_target"),
    ],
)
def test_catalogue_coverage_for_named_faults(
    tmp_path: Path,
    fault: FaultKind,
    expected_mutation: str,
) -> None:
    """AC-9: Every named fault in the catalogue produces a non-discarding, typed outcome."""
    env, git_ops, agent_runner, clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner, clock=clock)
    try:
        config = UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": env.target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        )
        scope = WorkspaceScope(tmp_path)
        env.set_faults([fault])

        auto_integrate_after_commit(
            config,
            scope,
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
            sleep=clock.sleep,
            jitter=_zero_jitter,
        )

        assert expected_mutation in env.calls
        assert not env.has_work_loss()
        # No resolver dispatched for environmental failures
        if fault is not FaultKind.REAL_CONFLICT:
            assert "resolve_rebase" not in env.dispatches
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# AC-7: the clock seam -- every wait runs on an INJECTED clock
# ---------------------------------------------------------------------------


def test_the_installed_clock_records_the_production_backoff(tmp_path: Path) -> None:
    """The wait between landing retries happens on the INSTALLED clock.

    The lost landing race earns exactly one bounded retry, and the retry
    waits before it runs. The wait is recorded by the installed clock, so
    the schedule is asserted without the test ever sleeping for real.
    """
    env = SimulatedGitEnvironment()
    git_ops = _LandingRaceOps(env)
    agent_runner = FakeAgentRunner(env)
    clock = _RecordingClock()
    token = install_executors(git_ops, agent_runner, clock=clock)
    try:
        # The installed clock IS the clock of the scope: both the seam
        # resolver and the "no clock supplied" resolver answer it.
        assert current_clock() is clock
        assert default_clock() is clock

        config = UnifiedConfig.model_validate(
            {
                "general": {
                    "auto_integrate_enabled": True,
                    "auto_integrate_target": env.target,
                    "auto_integrate_remote_enabled": False,
                }
            }
        )
        auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
            sleep=clock.sleep,
            jitter=_zero_jitter,
        )

        # The production back-off waited once, on the installed clock.
        assert list(clock.slept) == [_EXPECTED_FIRST_BACKOFF]
        assert clock.now() == 1000.0 + _EXPECTED_FIRST_BACKOFF
    finally:
        reset_executors(token)

    # The scope ended: the production clock is back, and the fake one is
    # not reachable from the production seam any more.
    assert current_clock() is default_clock()
    assert current_clock() is not clock


def test_the_lease_age_is_read_from_the_injected_clock() -> None:
    """The lease age comes from the injected clock, never from the wall clock.

    The lease payload records the timestamp the writer's clock read, and
    the age is the reader's clock minus that stamp. A test therefore
    places the lease at any age it likes -- no waiting, no sleeping.
    """
    clock = LeaseClock(monotonic=lambda: 1300.0, wall_time=lambda: 1700.0)
    assert lease_age_seconds(1000.0, clock=clock) == 300.0

    # The payload the writer leaves behind carries the INJECTED stamp, so
    # the reader ages it on the same clock.
    payload = lease_payload("agent-phase", clock=clock).decode(encoding="utf-8")
    stamped = float(payload.split("|")[1])
    assert stamped == 1300.0
    assert lease_age_seconds(stamped, clock=clock) == 0.0

    # The production clock stays the default: a lease stamped now has no
    # age at all.
    production = default_lease_clock()
    fresh_age = lease_age_seconds(production.monotonic(), clock=production)
    assert 0.0 <= fresh_age < 1.0


# ---------------------------------------------------------------------------
# AC-10: the operation-marker fault catalogue.
#
# Every non-NONE :class:`InProgressKind` member names the marker layout in
# the per-worktree git directory that produces it. The completeness guard
# iterates EVERY kind member, so a new kind without a catalogue row fails
# the guard -- a missing fault-kind case can never pass silently. The probe
# tests below drive the production observation probe against marker layouts
# built in ``tmp_path`` (no real repository, no subprocess, no clock).
# ---------------------------------------------------------------------------

#: Marker path components that name state DIRECTORIES rather than files.
_MARKER_DIRS: frozenset[str] = frozenset({"rebase-merge", "rebase-apply", "sequencer"})

#: The operation-marker fault catalogue: each row is a marker layout (paths
#: relative to the git directory) and the kind the probe must classify it
#: as. The ``rebase-apply`` rows pin the am-vs-rebase distinction: the
#: counter-free directory stays a REBASE while the directory carrying the
#: ``next``/``last`` patch counters classifies as AM.
_OPERATION_MARKER_CATALOGUE: tuple[tuple[tuple[str, ...], InProgressKind], ...] = (
    (("rebase-merge",), InProgressKind.REBASE),
    (("rebase-apply",), InProgressKind.REBASE),
    (("rebase-apply/next", "rebase-apply/last"), InProgressKind.AM),
    (("MERGE_HEAD",), InProgressKind.MERGE),
    (("CHERRY_PICK_HEAD",), InProgressKind.CHERRY_PICK),
    (("REVERT_HEAD",), InProgressKind.REVERT),
    (("sequencer",), InProgressKind.SEQUENCER),
)


def test_operation_marker_catalogue_covers_every_in_progress_kind() -> None:
    """AC-10 completeness guard: every operation kind has a marker fault row.

    A kind without a catalogue row has no marker the probe is proven
    against, so the guard fails on it -- extending the enum without
    extending the fault catalogue can never pass silently.
    """
    catalogued = frozenset(kind for _layout, kind in _OPERATION_MARKER_CATALOGUE)
    expected = frozenset(kind for kind in InProgressKind if kind is not InProgressKind.NONE)
    missing = sorted(kind.name for kind in expected - catalogued)
    assert missing == [], f"in-progress kinds without a marker fault row: {missing}"


@pytest.mark.parametrize(
    ("layout", "expected"),
    _OPERATION_MARKER_CATALOGUE,
    ids=["+".join(layout) for layout, _kind in _OPERATION_MARKER_CATALOGUE],
)
def test_read_in_progress_kind_classifies_the_marker_fault_catalogue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    layout: tuple[str, ...],
    expected: InProgressKind,
) -> None:
    """Each marker layout in the fault catalogue classifies to its kind.

    The probe runs against a marker layout built in ``tmp_path`` and a
    patched ``run_git`` that reports the fake git directory, so the test
    stays pure-table plus fakes: no real repository, no subprocess.
    """
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    for relative in layout:
        marker = git_dir / relative
        if marker.name in _MARKER_DIRS and marker.parent == git_dir:
            marker.mkdir()
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("state\n", encoding="utf-8")

    def _fake_run_git(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=str(git_dir), stderr="")

    monkeypatch.setattr("ralph.pipeline.integration_observation.run_git", _fake_run_git)

    assert read_in_progress_kind(tmp_path) == Reading.known(expected)


def test_unreadable_operation_probe_stays_unknown_and_never_positive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AC-10: an unreadable probe is UNKNOWN, never a positive kind.

    A git directory query that fails is environmental: the decision table
    must back off and re-observe, not treat the failure as evidence of an
    operation. Both failure seams (the ``git rev-parse`` query and the
    marker stat) map to :meth:`Reading.unknown`.
    """

    def _failing_run_git(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=128, stdout="", stderr="not a repository")

    monkeypatch.setattr("ralph.pipeline.integration_observation.run_git", _failing_run_git)

    reading = read_in_progress_kind(tmp_path)
    assert reading.is_unknown
    assert not reading.is_known_good

    # The unknown probe feeds the observation model as "kind NONE with
    # UNKNOWN ownership", which the decision table answers with back-off
    # (WAIT_FOR_ENVIRONMENT), never a resolver or resume dispatch.
    observation = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.UNKNOWN,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    action = decide(DecisionContext(observation=observation, attempts=AttemptState()))
    assert action is NextAction.WAIT_FOR_ENVIRONMENT


#: The new AC-10 operation kinds, paired with a legacy kind so the test can
#: prove the decision rows match the existing operation kinds exactly.
_NEW_OPERATION_KINDS: tuple[InProgressKind, ...] = (
    InProgressKind.REVERT,
    InProgressKind.AM,
    InProgressKind.SEQUENCER,
)


def _operation_observation(
    kind: InProgressKind,
    ownership: InProgressOwnership,
) -> Observation:
    """Build the canonical clean-tree observation carrying ``kind``."""
    return Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=kind,
        in_progress_ownership=ownership,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )


def test_new_operation_kinds_match_the_existing_operation_decision_rows() -> None:
    """AC-10: revert/am/sequencer rows equal the rebase/merge/cherry-pick rows.

    Owned (resolve/continue) maps to RESUME_OWNED_OPERATION at every rung;
    foreign (preserve) is a real conflict and maps to DISPATCH_RESOLVER at
    every rung; an unknown ownership reading backs off, never blocks; and
    under repeat pressure the owned row escalates to abort-and-rebuild
    (REBUILD_ON_TARGET) exactly like the legacy kinds.
    """
    legacy_kinds = (
        InProgressKind.REBASE,
        InProgressKind.MERGE,
        InProgressKind.CHERRY_PICK,
    )
    assert len(_NEW_OPERATION_KINDS) == len(legacy_kinds)
    for new_kind, legacy_kind in zip(_NEW_OPERATION_KINDS, legacy_kinds, strict=True):
        for ownership, expected_direct in (
            (InProgressOwnership.OWNED, NextAction.RESUME_OWNED_OPERATION),
            (InProgressOwnership.FOREIGN, NextAction.DISPATCH_RESOLVER),
        ):
            for rung in LADDER_ORDER:
                new_ctx = DecisionContext(
                    observation=_operation_observation(new_kind, ownership),
                    attempts=AttemptState(rung=rung),
                )
                legacy_ctx = DecisionContext(
                    observation=_operation_observation(legacy_kind, ownership),
                    attempts=AttemptState(rung=rung),
                )
                assert decide(new_ctx) is expected_direct, (
                    f"{new_kind.value} {ownership.value} @ {rung.value} did not "
                    f"yield {expected_direct.name}"
                )
                assert decide(new_ctx) is decide(legacy_ctx), (
                    f"{new_kind.value} row diverged from the {legacy_kind.value} row "
                    f"at {ownership.value} @ {rung.value}"
                )

        # Repeat pressure escalates the owned row to abort-and-rebuild,
        # identical to the legacy owned rows.
        owned_observation = _operation_observation(new_kind, InProgressOwnership.OWNED)
        repeat_counts: OrderedDict[tuple[str, str], int] = OrderedDict()
        repeat_counts[
            (AttemptState().signature(owned_observation), NextAction.RESUME_OWNED_OPERATION.value)
        ] = 5
        pressured = AttemptState(rung=LADDER_ORDER[0], repeat_counts=repeat_counts)
        escalated = decide(
            DecisionContext(
                observation=_operation_observation(new_kind, InProgressOwnership.OWNED),
                attempts=pressured,
            )
        )
        assert escalated is NextAction.REBUILD_ON_TARGET, (
            f"owned {new_kind.value} under repeat pressure escalated to {escalated.name}"
        )

        # Unknown ownership is environmental: back off, never block.
        unknown_observation = _operation_observation(new_kind, InProgressOwnership.UNKNOWN)
        assert unknown_observation.has_unknown()
        back_off = decide(DecisionContext(observation=unknown_observation, attempts=AttemptState()))
        assert back_off is NextAction.WAIT_FOR_ENVIRONMENT


def test_the_decision_vocabulary_cannot_require_intervention() -> None:
    """AC-10: REQUIRE_INTERVENTION is never a consumed outcome of any row."""
    intervening = sorted(member.name for member in NextAction if "INTERVENTION" in member.name)
    assert intervening == [], f"the decision vocabulary can require intervention: {intervening}"


# ---------------------------------------------------------------------------
# AC-10 dirt dimension: the planning-sync loop terminates when the tree is
# dirtied at EVERY step. The director is replaced by a recording stand-in
# and the porcelain probe by a seeded, per-step re-drawn dirt blob, so the
# loop runs against the production owner seam and durable ledger with no
# real git, no subprocess and no sleep.
# ---------------------------------------------------------------------------

#: The dirty observation every drawn dirt shape projects onto. All drawn
#: blobs are tracked-dirt shapes, so the signature — and therefore the
#: escalation climb — is the same no matter which shape the seed draws.
_DIRTY_OBSERVATION: Observation = Observation(
    target_vs_feature=TargetVsFeature.EQUAL,
    target_vs_remote=TargetVsRemote.EQUAL,
    worktree=WorktreeState.TRACKED_CHANGES,
    head=HeadState.ATTACHED,
    in_progress_kind=InProgressKind.NONE,
    in_progress_ownership=InProgressOwnership.OWNED,
    record=IntegrationRecordState.NONE,
    agent_active=False,
)

#: Tracked-dirt porcelain blobs the seed re-draws after every step:
#: unstaged, staged, type-change and deleted variants. Every one is a
#: blocking shape, so the tree stays dirty at every step of the loop.
_DIRTY_BLOBS: tuple[str, ...] = (
    " M src/app.py\0",
    "M  src/app.py\0",
    " T src/app.py\0",
    " D src/app.py\0",
)


class _DirtDirector:
    """Director stand-in serving the canned dirty observation."""

    def __init__(
        self,
        root: Path,
        *,
        target: str,
        remote: str,
        agent_active: bool = False,
    ) -> None:
        del root, target, remote, agent_active

    def observe(self) -> Observation:
        return _DIRTY_OBSERVATION


@pytest.mark.parametrize("seed", _SEEDS)
def test_dirt_at_every_planning_sync_step_escalates_and_terminates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    seed: int,
) -> None:
    """The dirt dimension: a tree dirtied at every step climbs and saturates.

    Each step re-draws the dirt blob from the seeded RNG, re-asks the
    planning-sync owner seam (which charges the durable ledger through
    the single charge path) and records the typed verdict. The loop is
    bounded at direct action + one climb per cleanup-ladder rung; the
    assertions prove the bound is the whole story:

    * every step's dirt blocks planning (``may_plan`` is never derived
      as true and the classifier verdict is BLOCKED);
    * the verdict's ladder rung climbs ``CLEANUP_LADDER_ORDER``
      monotonically and saturates at the terminal rung — the escalation
      is finite, so the loop terminates;
    * the coarse seam action stays inside the cleanup-ladder
      projections — never ``RESOLVE_ACTIVE_OPERATION`` (a resolver is
      never dispatched for dirt) and never ``LAND_TARGET``.
    """
    rng = random.Random(seed)
    ledger_path = tmp_path / ".agent" / f"dirt-dimension-ledger-{seed}.json"
    porcelain = [_DIRTY_BLOBS[0]]

    monkeypatch.setattr(integration_director, "IntegrationDirector", _DirtDirector)
    monkeypatch.setattr(
        integration_owner_seam,
        "read_worktree_porcelain",
        lambda _root: Reading.known(porcelain[0]),
    )

    rungs: list[CleanupLadderRung | None] = []
    actions: list[IntegrationAction] = []
    # The direct action answers the first two contacts (a first contact
    # is never a repeat); then one rung per charged repeat reaches the
    # terminal rung: direct + ladder rungs + saturation margin.
    for _ in range(2 + len(CLEANUP_LADDER_ORDER)):
        verdict = integration_owner_seam.decide_planning_sync(tmp_path, ledger_path=ledger_path)
        porcelain[0] = rng.choice(_DIRTY_BLOBS)
        rungs.append(verdict.ladder_rung)
        actions.append(verdict.action)
        assert verdict.may_plan is False, f"dirt let planning through at seed {seed}"
        assert verdict.worktree.dispatch_blocked, (
            f"a drawn dirt shape did not block dispatch at seed {seed}: {verdict.dirt_shape.value}"
        )

    climbed = [CLEANUP_LADDER_ORDER.index(rung) for rung in rungs if rung is not None]
    assert climbed == sorted(climbed), f"the climb is not monotone at seed {seed}"
    assert set(rungs) == set(CLEANUP_LADDER_ORDER), (
        f"the climb did not reach every rung at seed {seed}: {rungs}"
    )
    assert rungs[-1] is CLEANUP_LADDER_ORDER[-1], (
        f"the climb did not saturate at the terminal rung at seed {seed}"
    )
    assert IntegrationAction.RESOLVE_ACTIVE_OPERATION not in actions, (
        f"a resolver was dispatched for dirt at seed {seed}"
    )
    assert IntegrationAction.LAND_TARGET not in actions
