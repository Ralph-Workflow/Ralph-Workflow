"""Failure-mode catalogue coverage for the simulated integration environment.

Acceptance Criterion 9: every row (A-F) of the integration adversity
catalogue is a NAMED fault in the simulated environment, and each named
fault has a test asserting a bounded, non-discarding outcome, no agent
dispatch for a non-conflict, and an accurate user-facing reason. A new
fault kind cannot be added without being classified into a typed outcome:
the completeness guard below iterates every :class:`FaultKind` member and
fails when a member has no registered classification.

Acceptance Criterion 8(c): a stale lock left by a killed Ralph child does
NOT block the next observation -- the observation proves the owner is dead,
removes the lock and proceeds.

Every test drives the PRODUCTION integration seam
(:func:`ralph.pipeline.auto_integrate.auto_integrate_after_commit`) on the
simulated environment, so the assertions are about production behaviour and
not only about the double.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.integration_actions import (
    current_git_ops,
    install_executors,
    reset_executors,
)
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.workspace.scope import WorkspaceScope
from tests._fake_git_env import (
    FaultKind,
    SimulatedGitEnvironment,
    make_fake_environment,
)
from tests._fake_git_fault import (
    CATALOGUE_ROWS,
    catalogue,
    catalogue_rows,
    classification_for,
)
from tests._fake_git_ops import _SimulatedCrashError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.timeout_seconds(10)]

#: The bound on the simulated mutations one integration attempt may issue.
#: The attempt budget is three attempts and each attempt issues at most
#: four mutations (backup ref, replay, abort, landing), so anything above
#: this bound is an unbounded retry.
_MAX_FAKE_CALLS = 12

_VALID_OUTCOMES = ("succeeded", "definitely_failed", "timed_out", "killed", "unknown")
_VALID_PHASES = ("rebase", "landing", "remote", "observation", "shutdown", "any")
_VALID_EFFECTS = (
    "refuse",
    "fail",
    "recover",
    "defer",
    "concurrent_advance",
    "crash_before",
    "crash_after",
    "noop",
)


@pytest.fixture(autouse=True)
def _stub_production_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every production Git seam to the simulated environment.

    The stubs mirror the production seams the integration attempt reads:
    the target, the branch pointers, the durable record and the in-progress
    operation probes. Nothing here touches a real repository, a real
    remote or a real clock.
    """

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


def _config(env: SimulatedGitEnvironment) -> UnifiedConfig:
    """Return the config the integration attempt runs with."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": env.target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _run_attempt(
    env: SimulatedGitEnvironment,
    tmp_path: Path,
    state: RebaseState | None = None,
) -> RebaseState | None:
    """Run one integration attempt on the simulated environment.

    The back-off seam is a no-op and the jitter is constant, so the attempt
    never waits on a real clock.
    """
    return auto_integrate_after_commit(
        _config(env),
        WorkspaceScope(tmp_path),
        state if state is not None else RebaseState(),
        rebase_stop_resolver=lambda *_args, **_kwargs: True,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )


def _drive_fault(
    env: SimulatedGitEnvironment,
    kind: FaultKind,
    tmp_path: Path,
) -> str | None:
    """Drive the simulated phase that owns ``kind``.

    Returns:
        The crash reason when the phase modelled a crash, else ``None``.
    """
    classification = classification_for(kind)
    try:
        if classification.phase == "remote":
            env.publish_remote()
        elif classification.phase == "observation":
            env.observe()
        elif classification.phase == "shutdown":
            env.shutdown()
        elif classification.phase == "landing":
            _fast_forward(env, tmp_path)
        else:
            _rebase(env, tmp_path)
    except _SimulatedCrashError as crash:
        return str(crash)
    return None


def _fast_forward(env: SimulatedGitEnvironment, tmp_path: Path) -> None:
    """Run the landing mutation through the installed Git-ops seam."""
    current_git_ops().fast_forward_target(tmp_path, env.target, env.feature_sha)


def _rebase(env: SimulatedGitEnvironment, tmp_path: Path) -> None:
    """Run the replay mutation through the installed Git-ops seam."""
    current_git_ops().rebase_onto(tmp_path, env.target)


# ---------------------------------------------------------------------------
# AC-9: the completeness guard
# ---------------------------------------------------------------------------


def test_fault_catalogue_guard_every_fault_kind_is_classified() -> None:
    """AC-9: a new fault kind cannot be added without a typed outcome.

    The guard iterates EVERY member of the enum. A member that has no
    registration in the catalogue has no typed outcome, no bounded
    expectation and no user-facing reason, so the guard fails on it.
    """
    registry = catalogue()
    unclassified = sorted(kind.name for kind in FaultKind if kind not in registry)
    assert not unclassified, "fault kinds without a typed outcome: " + ", ".join(unclassified)
    for kind in FaultKind:
        classification = classification_for(kind)
        assert classification.kind is kind
        assert classification.row in (*CATALOGUE_ROWS, "control")
        assert classification.phase in _VALID_PHASES
        assert classification.effect in _VALID_EFFECTS
        assert classification.outcome in _VALID_OUTCOMES
        assert classification.reason.strip(), f"{kind.name} has no user-facing reason"


def test_fault_catalogue_registry_has_no_stale_entries() -> None:
    """The registry names only real fault kinds."""
    members = set(FaultKind)
    stale = sorted(kind.name for kind in catalogue() if kind not in members)
    assert not stale, "catalogue entries without a fault kind: " + ", ".join(stale)


def test_every_catalogue_row_carries_a_named_fault_kind() -> None:
    """AC-9: every row A-F of the catalogue is a named fault kind."""
    registry = catalogue()
    for row in CATALOGUE_ROWS:
        named = sorted(kind.name for kind, entry in registry.items() if entry.row == row)
        assert named, f"catalogue row {row} has no named fault kind"
    assert catalogue_rows() == CATALOGUE_ROWS


# ---------------------------------------------------------------------------
# AC-9: every named fault is consumed and yields a bounded outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(FaultKind), ids=lambda kind: kind.name)
def test_every_fault_kind_is_consumed_by_the_simulated_environment(
    kind: FaultKind,
    tmp_path: Path,
) -> None:
    """AC-9: every named fault is really consumed by the simulated environment."""
    classification = classification_for(kind)
    env, git_ops, agent_runner, _clock = make_fake_environment(
        remote_sha="target-v1",
        remote_enabled=True,
    )
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([kind])

        crash_reason = _drive_fault(env, kind, tmp_path)

        assert env.fired(kind), f"{kind.name} was never consumed by the simulated environment"
        assert env.outcome_kind_for(kind) == classification.outcome
        assert env.reason_for(kind) == classification.reason
        if crash_reason is not None:
            assert crash_reason == classification.reason
        assert not env.has_work_loss(), f"{kind.name} discarded the feature tip"
        assert len(env.calls) <= 2, f"{kind.name} issued an unbounded number of mutations"
        if not classification.dispatches_agent:
            assert "resolve_rebase" not in env.dispatches
    finally:
        reset_executors(token)


@pytest.mark.parametrize("kind", list(FaultKind), ids=lambda kind: kind.name)
def test_every_fault_kind_yields_a_bounded_non_discarding_outcome(
    kind: FaultKind,
    tmp_path: Path,
) -> None:
    """AC-9: each named fault yields a bounded, non-discarding outcome.

    The attempt runs through the production integration seam on the
    simulated environment: it terminates, it discards no work, it dispatches
    no resolver agent for a condition an agent cannot change, and it states
    an accurate user-facing reason.
    """
    classification = classification_for(kind)
    env, git_ops, agent_runner, _clock = make_fake_environment(
        remote_sha="target-v1",
        remote_enabled=True,
    )
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([kind])
        state = None
        if classification.reachable_by_integration:
            state = _run_attempt(env, tmp_path)
            assert state is not None, f"{kind.name} left the attempt without an outcome"
        else:
            env.shutdown()

        # The simulated environment consumed the fault and typed it.
        assert env.fired(kind), f"{kind.name} was never consumed by the simulated environment"
        assert env.outcome_kind_for(kind) == classification.outcome
        assert env.reason_for(kind) == classification.reason

        # Bounded: the attempt issued a bounded number of mutations.
        assert len(env.calls) <= _MAX_FAKE_CALLS, f"{kind.name} retried without a bound"

        # Non-discarding: the feature tip is still reachable.
        assert not env.has_work_loss(), f"{kind.name} discarded the feature tip"

        # No agent dispatch for a condition an agent cannot change.
        if classification.dispatches_agent:
            assert "resolve_rebase" in env.dispatches
        else:
            assert "resolve_rebase" not in env.dispatches, (
                f"{kind.name} dispatched a resolver agent for a non-conflict"
            )

        if state is None:
            return
        assert state.last_reason, f"{kind.name} left the operator without a reason"
        if not classification.local_landing_succeeds:
            # A local failure is never blamed on the remote.
            assert "remote" not in state.last_reason.lower()
        if classification.phase == "landing":
            # The landing refusal is the headline the operator reads.
            assert classification.reason in state.last_reason
        if classification.effect in ("crash_before", "crash_after"):
            assert classification.reason in state.last_reason
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# AC-8(c): a stale lock from a killed child does not block the next observation
# ---------------------------------------------------------------------------


def test_stale_lock_from_a_killed_child_does_not_block_the_next_observation(
    tmp_path: Path,
) -> None:
    """AC-8(c): the stale lock a killed Ralph child left is recoverable."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        # A force-killed Ralph child left its lock behind.
        env.leave_stale_lock()
        assert "index.lock" in env.locks
        env.set_faults([FaultKind.STALE_LOCK])

        state = _run_attempt(env, tmp_path)

        # The next observation was NOT blocked: the stale lock was removed.
        assert env.locks == {}, "the stale lock of the killed child blocked the observation"
        assert env.fired(FaultKind.STALE_LOCK)
        assert env.outcome_kind_for(FaultKind.STALE_LOCK) == "succeeded"
        assert env.reason_for(FaultKind.STALE_LOCK) == (
            "stale index.lock from a killed Ralph child was recovered; "
            "the next observation proceeded"
        )
        # The run moved on: the work landed and nothing was dispatched.
        assert env.is_converged()
        assert not env.has_work_loss()
        assert state is not None
        assert "resolve_rebase" not in env.dispatches
    finally:
        reset_executors(token)


def test_a_live_foreign_lock_is_never_removed(tmp_path: Path) -> None:
    """A lock with a live foreign holder is never deleted; the work is refused."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        env.hold_live_lock()
        env.set_faults([FaultKind.LIVE_FOREIGN_LOCK])

        state = _run_attempt(env, tmp_path)

        assert env.locks == {"index.lock": "live"}, "a live holder's lock was deleted"
        assert env.reason_for(FaultKind.LIVE_FOREIGN_LOCK) == (
            "refused: a live foreign process holds the lock on the target ref"
        )
        assert state is not None
        assert (
            state.last_reason == "refused: a live foreign process holds the lock on the target ref"
        )
        assert not env.has_work_loss()
        assert "resolve_rebase" not in env.dispatches
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# AC-2: a crash before or after every step converges to progress
# ---------------------------------------------------------------------------


def test_crash_before_a_step_converges_to_progress_or_a_ladder_rung(
    tmp_path: Path,
) -> None:
    """AC-2: a crash before a step leaves a resumable run, not a stuck one."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([FaultKind.CRASH_BEFORE_STEP])
        first = _run_attempt(env, tmp_path)

        assert env.fired(FaultKind.CRASH_BEFORE_STEP)
        assert env.outcome_kind_for(FaultKind.CRASH_BEFORE_STEP) == "killed"
        assert not env.has_work_loss()
        # The killed child left its lock behind.
        assert "index.lock" in env.locks

        # The restart: the next observation recovers the lock and the attempt
        # converges instead of repeating the crashed step.
        env.set_faults([])
        second = _run_attempt(env, tmp_path, state=first)

        assert env.locks == {}
        assert env.is_converged(), "the restart did not converge"
        assert not env.has_work_loss()
        assert second is not None
        assert len(env.calls) <= _MAX_FAKE_CALLS
    finally:
        reset_executors(token)


def test_crash_after_a_step_converges_to_progress_or_a_ladder_rung(
    tmp_path: Path,
) -> None:
    """AC-2: a crash after a step leaves a resumable run, not a stuck one."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([FaultKind.CRASH_AFTER_STEP])
        first = _run_attempt(env, tmp_path)

        assert env.fired(FaultKind.CRASH_AFTER_STEP)
        assert env.outcome_kind_for(FaultKind.CRASH_AFTER_STEP) == "killed"
        assert not env.has_work_loss()

        env.set_faults([])
        second = _run_attempt(env, tmp_path, state=first)

        assert env.locks == {}
        assert env.is_converged(), "the restart did not converge"
        assert not env.has_work_loss()
        assert second is not None
        assert len(env.calls) <= _MAX_FAKE_CALLS
    finally:
        reset_executors(token)


def test_crash_after_the_landing_leaves_the_work_landed(tmp_path: Path) -> None:
    """A crash between the landing and the publish leaves the work landed."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([FaultKind.CRASH_AFTER_STEP])
        with pytest.raises(_SimulatedCrashError):
            _fast_forward(env, tmp_path)

        # The landing happened before the crash, so the next observation
        # sees the work landed and converges without redoing it.
        assert env.target_sha == env.feature_sha
        assert env.is_converged()
        assert not env.has_work_loss()
    finally:
        reset_executors(token)


# ---------------------------------------------------------------------------
# The work-loss detector and the remote half of the postcondition
# ---------------------------------------------------------------------------


def test_has_work_loss_detects_a_feature_tip_detached_from_every_ref() -> None:
    """The work-loss detector fires when the tip is in no ref at all."""
    env, _git_ops, _agent_runner, _clock = make_fake_environment()
    assert not env.has_work_loss(), "the control state must not look like work loss"

    # The feature tip is detached from HEAD, from the target and from every
    # backup ref: nothing points at the work any more.
    env.feature_sha = "orphaned-feature-v9"
    env.head_sha = "feature-v1"
    env.target_sha = "target-v1"
    env.backup_refs = {}

    assert env.has_work_loss()


def test_convergence_requires_the_remote_to_match_the_target() -> None:
    """Convergence compares the local target with ``<remote>/<target>``."""
    env, _git_ops, _agent_runner, _clock = make_fake_environment(
        remote_sha="target-v1",
        remote_enabled=True,
    )
    env.target_sha = "feature-v1"
    env.feature_sha = "feature-v1"

    assert not env.is_converged(), "the remote still holds the previous target"

    env.remote_sha = "feature-v1"

    assert env.is_converged()


def test_remote_outage_leaves_the_local_work_landed_and_recovers(tmp_path: Path) -> None:
    """A remote outage never discards the local work and recovers afterwards."""
    env, git_ops, agent_runner, _clock = make_fake_environment(
        remote_sha="target-v1",
        remote_enabled=True,
    )
    token = install_executors(git_ops, agent_runner)
    try:
        env.set_faults([FaultKind.REMOTE_OUTAGE])
        _run_attempt(env, tmp_path)

        # The local landing landed; the remote did not take the push.
        assert env.target_sha == env.feature_sha
        assert env.remote_sha != env.target_sha
        assert not env.is_converged()
        assert env.last_remote_reason == (
            "the remote is unreachable; the local landing is unaffected"
        )
        assert not env.has_work_loss()

        # The outage recovers: the next publish reaches the remote and both
        # tiers converge.
        env.set_faults([])
        assert env.publish_remote() is None

        assert env.remote_sha == env.target_sha
        assert env.is_converged()
        assert not env.has_work_loss()
    finally:
        reset_executors(token)
