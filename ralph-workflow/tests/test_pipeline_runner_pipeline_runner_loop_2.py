"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import (
    Verbosity,
)
from ralph.config.mcp_loader import McpConfigError
from ralph.display.context import make_display_context
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline import prompt_prep as prompt_prep_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_retry_intent import cleared_agent_retry_intent
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


DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module.MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module.MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(console=console, force_width=width, force_mode="wide")
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
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


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> object:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
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
            ".agent/artifacts/plan.json",
            json.dumps(
                {
                    "type": "plan",
                    "content": {
                        "summary": {
                            "context": "Existing plan",
                            "scope_items": [
                                {"text": "one"},
                                {"text": "two"},
                                {"text": "three"},
                            ],
                        },
                        "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
                        "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                        "critical_files": {
                            "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                            "reference_files": [],
                        },
                        "risks_mitigations": [{"risk": "drift", "mitigation": "revise"}],
                        "verification_strategy": [
                            {"method": "pytest", "expected_outcome": "passes"}
                        ],
                        "work_units": [],
                    },
                }
            ),
        )
        workspace.write(
            ".agent/artifacts/planning_analysis_decision.json",
            json.dumps(
                {
                    "type": "planning_analysis_decision",
                    "content": {
                        "status": "request_changes",
                        "summary": "Need revisions",
                        "what_came_up_short": ["issue"],
                        "how_to_fix": ["fix it"],
                    },
                }
            ),
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
            lambda *_args, **_kwargs: effect,
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
            lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
        )
        monkeypatch.setattr(
            runner_module,
            "reducer_reduce",
            lambda current_state, _event, _policy, recovery=None: (current_state, []),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        display_context = make_display_context()
        display = runner_module.ParallelDisplay(display_context)
        registry = MagicMock()
        registry.get.return_value = None

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
        state = MagicMock()
        state.phase = "planning"
        effects = [SaveCheckpointEffect(), ExitSuccessEffect()]

        def stub_determine_effect(*_args: object, **_kwargs: object) -> object:
            return effects.pop(0)

        ckpt_save = MagicMock()
        reducer_events: list[object] = []

        def stub_reducer(current_state: object, event: object) -> object:
            reducer_events.append(event)
            return current_state, None

        def stub_reducer_with_policy(
            current_state: object, event: object, _policy: object = None
        ) -> object:
            return stub_reducer(current_state, event)

        captured_console = _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            stub_determine_effect,
        )
        monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer_with_policy)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(
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
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.NORMAL)

        assert result == 0
        printed = captured_console.export_text()
        assert "Recovery triggered: bad" in printed

    def test_keyboard_interrupt_triggers_checkpoint_and_returns_130(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        interrupted_state = MagicMock()
        state.copy_with.return_value = interrupted_state

        def raise_interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        ckpt_save = MagicMock()
        monkeypatch.setattr(runner_module, "determine_effect_from_policy", raise_interrupt)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(
            MagicMock(),
            initial_state=state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == INTERRUPT_EXIT_CODE
        state.copy_with.assert_called_once_with(interrupted_by_user=True)
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
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "invoke_execute_effect_with_optional_display",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("boom")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )

        def record_saved_state(
            saved_state: PipelineState, *_args: object, **_kwargs: object
        ) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

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
            lambda *_args, **_kwargs: effect,
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "invoke_execute_effect_with_optional_display",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                McpConfigError("fallback backend 'searxng' is not configured")
            ),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        recovery = RecoveryController(
            options=RecoveryControllerOptions(policy_bundle=bundle, cycle_cap=10)
        )
        display_context = make_display_context()
        display = runner_module.ParallelDisplay(display_context)
        registry = MagicMock()
        registry.get.return_value = None

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
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

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
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("prompt blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

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
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "execute_fan_out_sync",
            lambda **_kwargs: (_ for _ in ()).throw(SystemExit("fanout blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

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
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0

    def test_prepare_prompt_effect_advances_state_without_execute_effect(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        advanced_state = MagicMock()
        advanced_state.phase = "development"
        state.copy_with.return_value = advanced_state

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
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(
            MagicMock(),
            initial_state=state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == 0
        # Advancing to a different phase must also clear the next-attempt session
        # action so a stale resume id/intent cannot leak into the new phase.
        state.copy_with.assert_called_once_with(
            phase="development",
            current_drain="development",
            last_agent_session_id=None,
            agent_retry_intent=cleared_agent_retry_intent(),
        )
        execute_effect.assert_not_called()
        reducer.assert_not_called()
        ckpt_save.assert_called_once_with(advanced_state, ANY)

    def test_invoke_agent_effect_materializes_prompt_before_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        state.copy_with.return_value = state

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
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "reducer_reduce", MagicMock(return_value=(state, None)))
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        materialize.assert_called_once()
        execute_effect.assert_called_once()

    def test_run_passes_policy_and_recovery_controller_to_reducer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"

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
        reducer = MagicMock(return_value=(state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        reducer.assert_called_once()
        args, kwargs = reducer.call_args
        assert args[1] == PipelineEvent.AGENT_SUCCESS
        assert args[2] == policy_bundle.pipeline
        assert kwargs.get("recovery") is not None

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
        reducer = MagicMock(return_value=(failed_state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_FAILURE])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "execute_effect", execute_effect)
        monkeypatch.setattr(prompt_prep_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(
            MagicMock(),
            initial_state=planning_state,
            verbosity=Verbosity.QUIET,
            connectivity_monitor=_unknown_connectivity_monitor(),
        )

        assert result == 0
        reducer.assert_called_once()
        args, kwargs = reducer.call_args
        assert args[0] is planning_state
        assert args[1] == PipelineEvent.AGENT_FAILURE
        assert args[2] == policy_bundle.pipeline
        assert kwargs.get("recovery") is not None

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
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        monkeypatch.setattr(
            runner_module,
            "call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )

        runner_module.run(
            MagicMock(),
            initial_state=state,
            dashboard_subscriber=_RecordingSubscriber(),
            verbosity=Verbosity.QUIET,
        )

        assert len(notify_calls) >= 1, "subscriber was never seeded with initial state"
        assert notify_calls[0] is state
