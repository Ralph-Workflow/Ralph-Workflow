"""Where a published cycle deadline is revoked, and where it must still be readable.

The deadline lives in the process-global environment, so every exit path from a
pipeline step has to clear it or the next unrelated agent inherits a nag about a
cycle it never joined. It must equally survive until the agent's result has been
graded: grading promotes and validates the result artifact, whose warned
incomplete-work gate asks the runtime -- not the agent -- whether the cycle had
warned, and an environment already cleared answers "no" every time.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.mcp.protocol.env import CYCLE_DEADLINE_EPOCH_ENV, CYCLE_WARN_EPOCH_ENV
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.prompt_prep import cycle_deadline_suspended
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle

_ELAPSED_SECONDS = 3600.0


@lru_cache(maxsize=1)
def _bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


# Warm the policy parse at collection time: these tests share the repo's
# per-test timeout and the cold TOML parse alone can exhaust it under xdist.
_ = _bundle()


def _guarded_state() -> PipelineState:
    return PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
    )


def _drive_step(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    *,
    invoke: object,
    after_agent_run: object = None,
    raise_missing_plan_handoff: bool = False,
) -> object:
    """Run one real pipeline step whose invocation publishes a deadline."""
    state = _guarded_state()
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
        chain_name="development",
    )
    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_args, **_kwargs: effect,
    )

    def _materialize(*_args: object, **_kwargs: object) -> None:
        # Stands in for real materialization, whose only relevant effect here
        # is publishing the deadline for the invocation about to start.
        runner_module.publish_cycle_deadline_env(
            state, "development", _bundle(), _ELAPSED_SECONDS
        )
        if raise_missing_plan_handoff:
            raise MissingPlanHandoffError("no plan handoff at .agent/PLAN.md")

    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", _materialize)
    monkeypatch.setattr(runner_module, "invoke_execute_effect_with_optional_display", invoke)
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        after_agent_run or (lambda **_kwargs: PipelineEvent.AGENT_SUCCESS),
    )
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, _event, _policy, recovery=None, routing_timing=None: (
            current_state,
            [],
        ),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_args, **_kwargs: None)

    display_context = make_display_context()
    registry = MagicMock()
    registry.get.return_value = None
    return runner_module.run_pipeline_step(
        state=state,
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        config=MagicMock(),
        display=runner_module.ParallelDisplay(display_context),
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )


@pytest.fixture(autouse=True)
def _reserve_published_names(monkeypatch: MonkeyPatch) -> None:
    """Register the published names so pytest restores the real environment."""
    monkeypatch.setenv(CYCLE_WARN_EPOCH_ENV, "stale-value")
    monkeypatch.setenv(CYCLE_DEADLINE_EPOCH_ENV, "stale-value")


def test_a_crashing_invocation_does_not_leave_the_deadline_published(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """An agent invocation that raises must not leak its deadline to the run."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("agent transport died")

    _drive_step(monkeypatch, tmp_path, invoke=_boom)

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ


def test_a_keyboard_interrupt_does_not_leave_the_deadline_published(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Ctrl-C propagates, so only a finally-clause revocation covers it."""

    def _interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _drive_step(monkeypatch, tmp_path, invoke=_interrupt)

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ


def test_a_missing_plan_handoff_does_not_leave_the_deadline_published(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Publication precedes the failure, so the early return must revoke too."""
    _drive_step(
        monkeypatch,
        tmp_path,
        invoke=lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
        raise_missing_plan_handoff=True,
    )

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ


def test_the_deadline_is_readable_while_the_agents_result_is_graded(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Grading promotes and validates the result artifact, which asks the clock.

    Revoking before this point silently disabled the warned incomplete-work
    gate for every promoted artifact — the exact invocation it exists for.
    """
    seen: list[bool] = []

    def _grade(**_kwargs: object) -> PipelineEvent:
        seen.append(CYCLE_WARN_EPOCH_ENV in os.environ)
        return PipelineEvent.AGENT_SUCCESS

    _drive_step(
        monkeypatch,
        tmp_path,
        invoke=lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
        after_agent_run=_grade,
    )

    assert seen == [True]
    assert CYCLE_WARN_EPOCH_ENV not in os.environ


def test_conflict_resolution_regression_suspends_inherited_cycle_deadline(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """S-1/S-4: main-run auto-integration cannot pass a cycle deadline to a resolver."""
    seen: list[bool] = []
    state = PipelineState(phase="development")
    config = MagicMock()
    display_context = make_display_context()

    def _integrate(*_args: object, **_kwargs: object) -> None:
        seen.append(CYCLE_WARN_EPOCH_ENV in os.environ)

    runner_module.publish_cycle_deadline_env(
        _guarded_state(), "development", _bundle(), _ELAPSED_SECONDS
    )
    monkeypatch.setattr(runner_module, "auto_integrate_on_phase_transition", _integrate)

    assert (
        runner_module._integrate_on_phase_transition(
            event=PipelineEvent.AGENT_SUCCESS,
            config=config,
            workspace_scope=WorkspaceScope(tmp_path),
            state=state,
            display=runner_module.ParallelDisplay(display_context),
            policy_bundle=None,
            registry=None,
        )
        is None
    )

    assert seen == [False]
    assert CYCLE_WARN_EPOCH_ENV in os.environ


def test_suspending_the_deadline_hides_it_and_restores_it_exactly() -> None:
    """A worker reconciles its siblings mid-cycle and still needs its deadline."""
    runner_module.publish_cycle_deadline_env(
        _guarded_state(), "development", _bundle(), _ELAPSED_SECONDS
    )
    published = dict(os.environ)

    with cycle_deadline_suspended():
        assert CYCLE_WARN_EPOCH_ENV not in os.environ
        assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ

    assert os.environ[CYCLE_WARN_EPOCH_ENV] == published[CYCLE_WARN_EPOCH_ENV]
    assert os.environ[CYCLE_DEADLINE_EPOCH_ENV] == published[CYCLE_DEADLINE_EPOCH_ENV]


def test_suspending_the_deadline_restores_it_when_the_block_raises() -> None:
    """A failed reconciliation must not cost the worker its own deadline."""
    runner_module.publish_cycle_deadline_env(
        _guarded_state(), "development", _bundle(), _ELAPSED_SECONDS
    )
    published = os.environ[CYCLE_DEADLINE_EPOCH_ENV]

    with pytest.raises(RuntimeError), cycle_deadline_suspended():
        raise RuntimeError("conflict resolution failed")

    assert os.environ[CYCLE_DEADLINE_EPOCH_ENV] == published
