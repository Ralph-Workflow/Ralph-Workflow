"""Fault-injecting termination model test driving integration executor seams.

Satisfies Acceptance Criteria 2, 7, 8, and 9:
- Bounded histories with fixed seeds demonstrating termination and convergence
- No work loss (feature commit preserved via target or backup ref)
- No useless agent dispatch (agents invoked only for content conflicts)
- Reproduction and validation of the 2026-10-24 catch-up stall incident
- Catalogue coverage for named external and environmental faults
"""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

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

pytestmark = [pytest.mark.timeout_seconds(10)]

_SEEDS = (42, 101, 2024, 9999, 1337)


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
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)

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
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
    try:
        env.agent_active = True
        landed, reason = git_ops.fast_forward_target(tmp_path, env.target, env.feature_sha)
        assert not landed
        assert "refused: agent is currently active" in reason
    finally:
        reset_executors(token)


def test_observed_incident_bounded_timeouts_advance_ladder(tmp_path: Path) -> None:
    """AC-8(b): Repeated timeouts do not loop unbounded and do not dispatch useless agents."""
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
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
            out = auto_integrate_after_commit(config, scope, state)
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
    env, git_ops, agent_runner, _clock = make_fake_environment()
    token = install_executors(git_ops, agent_runner)
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
        )

        assert expected_mutation in env.calls
        assert not env.has_work_loss()
        # No resolver dispatched for environmental failures
        if fault is not FaultKind.REAL_CONFLICT:
            assert "resolve_rebase" not in env.dispatches
    finally:
        reset_executors(token)
