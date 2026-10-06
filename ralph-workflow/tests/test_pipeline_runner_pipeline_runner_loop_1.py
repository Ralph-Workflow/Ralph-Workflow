"""Tests for ralph/pipeline/runner.py — pipeline runner.

Consolidated from ``test_pipeline_runner_pipeline_runner_loop_1.py`` and
``test_pipeline_runner_pipeline_runner_loop_2.py``. Both files exercise
``ralph.pipeline.runner`` against the same workspace/policy helpers, so
sharing the imports and helper functions across one module cuts the
per-shard collection cost without changing the observed behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from unittest.mock import ANY, MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import (
    Verbosity,
)
from ralph.config.mcp_loader import McpConfigError
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline import prompt_prep as prompt_prep_module
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent, resume_agent_retry_intent
from ralph.pipeline.effects import (
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import (
        PolicyBundle,
    )


pytestmark = pytest.mark.timeout_seconds(5)


INTERRUPT_EXIT_CODE = 130


class _Callback[T](Protocol):
    def __call__(self, *_args: object, **_kwargs: object) -> T: ...


def _returns[T](value: T) -> _Callback[T]:
    def result(*_args: object, **_kwargs: object) -> T:
        return value

    return result


def _nothing(*_args: object, **_kwargs: object) -> None:
    return None


def _unchanged_state(state: PipelineState, _context: object) -> PipelineState:
    return state


def _unchanged_reducer(
    state: PipelineState, *_args: object, **_kwargs: object
) -> tuple[PipelineState, list[object]]:
    return state, []


def _next_effect[T](effects: Iterator[T]) -> _Callback[T]:
    def result(*_args: object, **_kwargs: object) -> T:
        return next(effects)

    return result


def _raises(error: BaseException) -> _Callback[None]:
    def raise_error(*_args: object, **_kwargs: object) -> None:
        raise error

    return raise_error


def _phase_transition(_display: object, previous: str, *_args: object, **_kwargs: object) -> str:
    return previous


def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(
        console=console,
        force_width=width,
    )
    monkeypatch.setattr(runner_module, "make_display_context", _returns(ctx))
    return console


def _unknown_connectivity_monitor() -> MagicMock:
    """Return a mock connectivity monitor stuck in the ``unknown`` state.

    The real :class:`ralph.recovery.connectivity.ConnectivityMonitor` runs
    a background probe loop. Under heavy parallel load that probe can
    transition to ``online`` between the call to ``run()`` and the first
    ``_apply_connectivity_check`` iteration, which mutates the pipeline
    state via ``copy_with(last_connectivity_state='online')`` and breaks
    tests that assert the original state object is passed untouched to
    ``reducer_reduce`` (e.g. ``is planning_state``) or that count a
    specific number of ``copy_with`` calls.

    Returning a monitor that reports ``unknown`` makes
    ``_apply_connectivity_check`` a no-op, so the tests stay deterministic
    regardless of parallel scheduling.
    """
    monitor = MagicMock()
    monitor.current_state = "unknown"
    return monitor


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", _returns(_load_default_policy_bundle())
    )
    monkeypatch.setattr(
        run_loop_module,
        "_apply_connectivity_check",
        _unchanged_state,
    )
    # Runner-loop unit tests own their injected effects, not startup Git
    # recovery; integration recovery has its own behavioral test modules.
    monkeypatch.setattr(
        run_loop_module,
        "_apply_startup_rebase_outcomes",
        _unchanged_state,
    )
    monkeypatch.setattr(run_loop_module, "_block_unresolved_integration", _nothing)
    monkeypatch.setattr(run_loop_module, "_planning_sync_gap", _nothing)
    monkeypatch.setattr(
        runner_module,
        "_assert_integration_dispatch_invariant",
        _nothing,
    )


class TestPipelineRunnerLoop:
    def test_run_pipeline_step_rewrites_stale_planning_prompt_before_agent_invoke(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace_scope = WorkspaceScope(tmp_path)
        workspace = FsWorkspace(tmp_path)
        workspace.write("PROMPT.md", "Revise the plan after analysis")
        workspace.write(
            ".agent/PLAN.md",
            "# Execution Plan\n\nExisting plan\n",
        )
        workspace.write(
            ".agent/PLANNING_ANALYSIS_DECISION.md",
            "---\ntype: planning_analysis_decision\nstatus: request_changes\n---\n"
            "## Summary\n- [S1] Need revisions\n"
            "## What Came Up Short\n- [W1] issue\n"
            "## How To Fix\n- [W1] fix it\n",
        )
        workspace.write(
            ".agent/tmp/planning_prompt.md",
            "You are in PLANNING MODE. Create a detailed, structured execution plan.",
        )
        state = PipelineState(phase="planning", previous_phase="planning_analysis")
        effect = InvokeAgentEffect(
            agent_name="planner",
            phase="planning",
            prompt_file=".agent/tmp/planning_prompt.md",
            drain="planning",
        )
        seen: dict[str, str] = {}

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _returns(effect),
        )

        def fake_invoke(*_args: object, **_kwargs: object) -> object:
            seen["prompt"] = workspace.read(".agent/tmp/planning_prompt.md")
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(
            runner_module,
            "invoke_execute_effect_with_optional_display",
            fake_invoke,
        )
        monkeypatch.setattr(
            runner_module,
            "phase_event_after_agent_run",
            _returns(PipelineEvent.AGENT_SUCCESS),
        )
        monkeypatch.setattr(
            runner_module,
            "reducer_reduce",
            _unchanged_reducer,
        )
        monkeypatch.setattr(ckpt, "save", MagicMock())
        display_context = make_display_context()
        display = ParallelDisplay(display_context)
        registry = MagicMock(get=MagicMock(return_value=None))

        result = runner_module.run_pipeline_step(
            state=state,
            policy_bundle=bundle,
            workspace_scope=workspace_scope,
            config=MagicMock(),
            display=display,
            display_context=display_context,
            verbosity=Verbosity.QUIET,
            registry=registry,
            pipeline_subscriber=None,
        )

        assert isinstance(result, PipelineState)
        assert "PLANNING EDIT MODE" in seen["prompt"]
        assert "You are in PLANNING MODE" not in seen["prompt"]

    def test_save_checkpoint_effect_triggers_checkpoint_and_returns_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = PipelineState(phase="planning")
        effects = [SaveCheckpointEffect(), ExitSuccessEffect()]

        def stub_determine_effect(*_args: object, **_kwargs: object) -> object:
            return effects.pop(0)

        ckpt_save = MagicMock()
        reducer_events: list[object] = []

        def stub_reducer(current_state: object, event: object) -> object:
            reducer_events.append(event)
            return current_state, None

        def stub_reducer_with_policy(
            current_state: object, event: object, _policy: object = None, **_kwargs: object
        ) -> object:
            return stub_reducer(current_state, event)

        captured_console = _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            stub_determine_effect,
        )
        monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer_with_policy)
        monkeypatch.setattr(ckpt, "save", ckpt_save)

        result = run_loop_module.run(
            MagicMock(),
            initial_state=state,
            verbosity=Verbosity.NORMAL,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == 0
        ckpt_save.assert_called_once_with(state, ANY)
        assert reducer_events == [PipelineEvent.CHECKPOINT_SAVED]
        # Verify success message was printed (among other display calls)
        printed = captured_console.export_text()
        assert "Pipeline completed successfully" in printed

    def test_exit_failure_effect_enters_recovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = PipelineState(phase="planning")
        captured_console = _install_runner_display_context(monkeypatch)
        effects = iter([ExitFailureEffect(reason="bad"), ExitSuccessEffect()])

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(ckpt, "save", MagicMock())

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.NORMAL)

        assert result == 0
        printed = captured_console.export_text()
        assert "Recovery triggered: bad" in printed

    def test_keyboard_interrupt_triggers_checkpoint_and_returns_130(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(phase="planning")
        interrupted_state = state.copy_with(interrupted_by_user=True)

        def raise_interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        ckpt_save = MagicMock()
        monkeypatch.setattr(runner_module, "determine_effect_from_policy", raise_interrupt)
        monkeypatch.setattr(ckpt, "save", ckpt_save)

        result = run_loop_module.run(
            MagicMock(),
            initial_state=state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == INTERRUPT_EXIT_CODE
        ckpt_save.assert_called_once_with(interrupted_state, ANY)

    def test_run_converts_system_exit_during_effect_execution_into_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["planner"])},
        )
        effects = iter(
            [
                InvokeAgentEffect(
                    agent_name="planner",
                    phase="planning",
                    prompt_file=".agent/tmp/planning_prompt.md",
                ),
                ExitSuccessEffect(),
            ]
        )
        saved_states: list[PipelineState] = []

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            _nothing,
        )
        monkeypatch.setattr(
            runner_module,
            "invoke_execute_effect_with_optional_display",
            _raises(SystemExit("boom")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )

        def record_saved_state(
            saved_state: PipelineState, *_args: object, **_kwargs: object
        ) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(ckpt, "save", record_saved_state)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        recovered_chain = recovered_state.chain_for_phase("planning")
        assert recovered_chain is not None
        assert recovered_chain.retries == 1
        assert recovered_state.recovery_epoch == 0
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "boom" in recovered_state.last_error

    def test_run_pipeline_step_treats_mcp_config_error_as_user_config_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace_scope = WorkspaceScope(tmp_path)
        state = PipelineState(
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude/haiku"])},
        )
        effect = InvokeAgentEffect(
            agent_name="claude/haiku",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
        )

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _returns(effect),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            _nothing,
        )
        monkeypatch.setattr(
            runner_module,
            "invoke_execute_effect_with_optional_display",
            _raises(McpConfigError("fallback backend 'searxng' is not configured")),
        )
        monkeypatch.setattr(ckpt, "save", MagicMock())

        recovery = RecoveryController(
            options=RecoveryControllerOptions(policy_bundle=bundle, cycle_cap=10)
        )
        display_context = make_display_context()
        display = ParallelDisplay(display_context)
        registry = MagicMock(get=MagicMock(return_value=None))

        result = runner_module.run_pipeline_step(
            state=state,
            policy_bundle=bundle,
            workspace_scope=workspace_scope,
            config=MagicMock(),
            display=display,
            display_context=display_context,
            verbosity=Verbosity.QUIET,
            registry=registry,
            pipeline_subscriber=None,
            recovery_controller=recovery,
        )

        assert isinstance(result, PipelineState)
        assert result.phase == bundle.pipeline.recovery.failed_route
        assert result.previous_phase == "development"
        assert result.last_failure_category == "user_config"
        assert result.last_error is not None
        assert "fallback backend 'searxng'" in result.last_error

    def test_run_converts_system_exit_during_effect_determination_into_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["planner"])},
        )
        saved_states: list[PipelineState] = []
        calls = iter([SystemExit("determine blew up"), ExitSuccessEffect()])

        def determine_effect(*_args: object, **_kwargs: object) -> object:
            result = next(calls)
            if isinstance(result, BaseException):
                raise result
            return result

        def record_saved_state(
            saved_state: PipelineState, *_args: object, **_kwargs: object
        ) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(runner_module, "call_determine_effect_from_policy", determine_effect)
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )
        monkeypatch.setattr(ckpt, "save", record_saved_state)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        recovered_chain = recovered_state.chain_for_phase("planning")
        assert recovered_chain is not None
        assert recovered_chain.retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "determine blew up" in recovered_state.last_error

    def test_run_converts_system_exit_during_prepare_prompt_inline_handling_into_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["planner"])},
        )
        effects = iter([PreparePromptEffect(phase="planning", iteration=0), ExitSuccessEffect()])
        saved_states: list[PipelineState] = []

        def record_saved_state(
            saved_state: PipelineState, *_args: object, **_kwargs: object
        ) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            _raises(SystemExit("prompt blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )
        monkeypatch.setattr(ckpt, "save", record_saved_state)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        recovered_chain = recovered_state.chain_for_phase("planning")
        assert recovered_chain is not None
        assert recovered_chain.retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "prompt blew up" in recovered_state.last_error

    def test_run_converts_system_exit_during_fanout_dispatch_into_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(
            phase="development",
            work_units=(WorkUnit(unit_id="unit-a", description="A"),),
            phase_chains={"development": AgentChainState(agents=["claude"])},
        )
        effects = iter(
            [
                FanOutEffect(
                    work_units=(WorkUnit(unit_id="unit-a", description="A"),),
                    max_workers=1,
                ),
                ExitSuccessEffect(),
            ]
        )
        saved_states: list[PipelineState] = []

        def record_saved_state(
            saved_state: PipelineState, *_args: object, **_kwargs: object
        ) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "execute_fan_out_sync",
            _raises(SystemExit("fanout blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )
        monkeypatch.setattr(ckpt, "save", record_saved_state)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "development"
        recovered_chain = recovered_state.chain_for_phase("development")
        assert recovered_chain is not None
        assert recovered_chain.retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "fanout blew up" in recovered_state.last_error

    def test_failed_state_reenters_recovery_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = PipelineState(
            phase="failed",
            previous_phase="development",
            last_error="bad error",
            current_drain="development",
        )
        effects = iter(
            [
                PreparePromptEffect(phase="development", iteration=0, drain="development"),
                ExitSuccessEffect(),
            ]
        )

        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            _nothing,
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )
        monkeypatch.setattr(ckpt, "save", MagicMock())

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0

    def test_prepare_prompt_effect_advances_state_without_execute_effect(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = PipelineState(
            phase="planning",
            last_agent_session_id="stale-session",
            agent_retry_intent=resume_agent_retry_intent("stale-session"),
        )

        effects = [
            PreparePromptEffect(phase="development", iteration=0),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(
            _state: object, _bundle: object, _workspace_scope: object
        ) -> object:
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_FAILURE)
        reducer = MagicMock()
        saved: list[PipelineState] = []

        def save_checkpoint(current: PipelineState, _scope: WorkspaceScope) -> None:
            saved.append(current)

        _install_runner_display_context(monkeypatch)

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            _nothing,
        )
        monkeypatch.setattr(ckpt, "save", save_checkpoint)

        result = run_loop_module.run(
            MagicMock(),
            initial_state=state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == 0
        # Advancing to a different phase must also clear the next-attempt session
        # action so a stale resume id/intent cannot leak into the new phase.
        assert len(saved) == 1
        assert saved[0].phase == "development"
        assert saved[0].current_drain == "development"
        assert saved[0].last_agent_session_id is None
        assert saved[0].agent_retry_intent == cleared_agent_retry_intent()
        execute_effect.assert_not_called()
        reducer.assert_not_called()

    def test_invoke_agent_effect_materializes_prompt_before_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = PipelineState(phase="planning")

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(
            _state: object, _bundle: object, _workspace_scope: object
        ) -> object:
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = _returns([PipelineEvent.AGENT_SUCCESS])

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "reducer_reduce", MagicMock(return_value=(state, None)))
        monkeypatch.setattr(ckpt, "save", ckpt_save)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        materialize.assert_called_once()
        execute_effect.assert_called_once()

    def test_run_passes_policy_and_recovery_controller_to_reducer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        state = PipelineState(phase="planning")

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(
            _state: object, _bundle: object, _workspace_scope: object
        ) -> object:
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        received: list[tuple[PipelineState, object, object, dict[str, object]]] = []

        def reducer(
            current: PipelineState,
            event: object,
            policy: object,
            **kwargs: object,
        ) -> tuple[PipelineState, list[object]]:
            received.append((current, event, policy, kwargs))
            return state, []

        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = _returns([PipelineEvent.AGENT_SUCCESS])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", _returns(policy_bundle))
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(ckpt, "save", ckpt_save)

        result = run_loop_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert len(received) == 1
        _current, event, policy, kwargs = received[0]
        assert event == PipelineEvent.AGENT_SUCCESS
        assert policy == policy_bundle.pipeline
        assert isinstance(kwargs.get("recovery"), RecoveryController)

    def test_run_uses_phase_handler_event_after_agent_execution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        planning_state = PipelineState(
            phase="planning",
            phase_chains={
                "planning": AgentChainState(agents=["claude"], current_index=0, retries=3)
            },
        )
        failed_state = planning_state.copy_with(
            phase="failed",
            previous_phase="planning",
            last_error="Agent chain exhausted in planning",
        )

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitFailureEffect(reason="Agent chain exhausted in planning"),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(
            _state: object, _bundle: object, _workspace_scope: object
        ) -> object:
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        received: list[tuple[PipelineState, object, object, dict[str, object]]] = []

        def reducer(
            current: PipelineState,
            event: object,
            policy: object,
            **kwargs: object,
        ) -> tuple[PipelineState, list[object]]:
            received.append((current, event, policy, kwargs))
            return failed_state, []

        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = _returns([PipelineEvent.AGENT_FAILURE])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", _returns(policy_bundle))
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(ckpt, "save", ckpt_save)

        result = run_loop_module.run(
            MagicMock(),
            initial_state=planning_state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == 0
        assert len(received) == 1
        current, event, policy, kwargs = received[0]
        assert current is planning_state
        assert event == PipelineEvent.AGENT_FAILURE
        assert policy == policy_bundle.pipeline
        assert isinstance(kwargs.get("recovery"), RecoveryController)

    def test_run_notifies_subscriber_with_initial_state_before_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run() must seed the subscriber with initial state before executing any effects.

        Without this seed call, DashboardSubscriber._last_state is None during the first
        long-running phase (e.g., planning). record_activity() calls during that phase
        cannot build snapshots, leaving the dashboard stuck on 'Starting…' for the entire
        phase duration. Seeding before the loop fixes the blank dashboard bug.
        """
        notify_calls: list[object] = []

        class _RecordingSubscriber:
            def notify(self, state: object) -> None:
                notify_calls.append(state)

        state = PipelineState(
            phase="failed",
            previous_phase="planning",
            last_error="pre-failed for seed test",
        )
        effects = iter([PreparePromptEffect(phase="planning", iteration=0), ExitSuccessEffect()])

        _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(ckpt, "save", MagicMock())
        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            _next_effect(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            _nothing,
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            _phase_transition,
        )

        run_loop_module.run(
            MagicMock(),
            initial_state=state,
            dashboard_subscriber=_RecordingSubscriber(),
            verbosity=Verbosity.QUIET,
        )

        assert len(notify_calls) >= 1, "subscriber was never seeded with initial state"
        assert notify_calls[0] is state
