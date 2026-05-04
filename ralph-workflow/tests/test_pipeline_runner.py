"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from git import Repo as GitRepo
from rich.console import Console
from rich.text import Text

from ralph.agents.invoke import AgentInactivityTimeoutError, AgentInvocationError
from ralph.agents.parsers import AgentOutputLine, ClaudeParser
from ralph.config.enums import (
    AgentTransport,
    JsonParserType,
    Verbosity,
)
from ralph.config.models import AgentConfig, CcsConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.tools.names import claude_tool_name_prefix
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.validation import UpstreamValidationError
from ralph.phases import HANDLERS, PhaseContext, handle_phase
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.runner import _phase_context
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.prompts.materialize import MissingPlanHandoffError
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.prompts.types import SessionCapabilities

DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module._MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module._MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module._MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_factory(return_value):
    class Registry:
        @classmethod
        def from_config(cls, config):
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


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
):
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_resolve_display_defaults_to_legacy_console_display() -> None:
    display = runner_module._resolve_display(None, make_display_context())

    assert isinstance(display, runner_module._LegacyConsoleDisplay)


class TestCreateInitialState:
    def test_creates_state_with_planning_phase(self) -> None:
        config = MagicMock()
        config.agent_chains = {"dev": ["claude"], "rev": ["claude"]}
        config.agent_drains = {"development": "dev", "review": "rev", "planning": "dev"}
        agents_policy = AgentsPolicy(
            agent_chains={
                "dev": AgentChainConfig(agents=["claude"]),
                "rev": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "development": AgentDrainConfig(chain="dev"),
                "review": AgentDrainConfig(chain="rev"),
                "planning": AgentDrainConfig(chain="dev"),
            },
        )
        pipeline_policy = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            budget_counters={
                "iteration": BudgetCounterConfig(default_max=5),
                "reviewer_pass": BudgetCounterConfig(default_max=1),
            },
        )

        state = runner_module._create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
            counter_overrides={"iteration": DEVELOPER_ITERATIONS, "reviewer_pass": REVIEWER_PASSES},
        )
        assert state.phase == "planning"
        assert state.budget_caps.get("iteration") == DEVELOPER_ITERATIONS
        assert state.budget_caps.get("reviewer_pass") == REVIEWER_PASSES
        assert state.chain_for_phase("development").agents == ["claude"]
        assert state.chain_for_phase("review").agents == ["claude"]

    def test_empty_agent_chains(self) -> None:
        config = MagicMock()
        config.agent_chains = {}

        state = runner_module._create_initial_state(
            config, pipeline_policy=_load_default_policy_bundle().pipeline
        )
        dev_chain = state.chain_for_phase("development")
        rev_chain = state.chain_for_phase("review")
        assert dev_chain is None or dev_chain.agents == []
        assert rev_chain is None or rev_chain.agents == []

    def test_initial_state_prefers_config_drain_bindings_over_policy_chains(self) -> None:
        config = MagicMock()
        config.agent_chains = {"plan_chain": ["codex"]}
        config.agent_drains = {"planning": "plan_chain"}
        agents_policy = AgentsPolicy(
            agent_chains={"planner_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"planning": AgentDrainConfig(chain="planner_chain")},
        )
        pipeline_policy = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        state = runner_module._create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )

        assert state.chain_for_phase("planning").agents == ["codex"]

    def test_initial_state_tracks_custom_phase_chain_from_policy(self) -> None:
        config = MagicMock()
        config.agent_chains = {"dev_chain": ["codex"]}
        config.agent_drains = {"development": "dev_chain"}
        agents_policy = AgentsPolicy(
            agent_chains={"policy_dev_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"development": AgentDrainConfig(chain="policy_dev_chain")},
        )
        pipeline_policy = PipelinePolicy(
            phases={
                "feature_build": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="feature_build",
            terminal_phase="complete",
        )

        state = runner_module._create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )

        chain = state.chain_for_phase("feature_build")
        assert chain is not None
        assert chain.agents == ["codex"]

    def test_initial_state_maps_analysis_phases_to_config_drain_by_full_name(self) -> None:
        config = MagicMock()
        config.agent_chains = {"analysis_chain": ["config-analysis-agent"]}
        config.agent_drains = {"development_analysis": "analysis_chain"}
        agents_policy = AgentsPolicy(
            agent_chains={"planner_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"planning": AgentDrainConfig(chain="planner_chain")},
        )
        pipeline_policy = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        state = runner_module._create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )

        assert state.chain_for_phase("development_analysis").agents == ["config-analysis-agent"]

    def test_creates_state_with_correct_development_budget(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"iteration": DEVELOPER_ITERATIONS},
        )
        assert state.get_budget_remaining("iteration") == DEVELOPER_ITERATIONS

    def test_creates_state_with_correct_review_budget(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"reviewer_pass": REVIEWER_PASSES},
        )
        assert state.get_budget_remaining("reviewer_pass") == REVIEWER_PASSES

    def test_creates_state_with_zero_review_budget_when_r_zero(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"reviewer_pass": 0},
        )
        assert state.get_budget_remaining("reviewer_pass") == 0


class TestDetermineEffect:
    def _make_state(
        self,
        phase: str,
        total_iterations: int = 3,
        current_agent: str | None = None,
    ) -> MagicMock:
        state = MagicMock()
        state.phase = phase
        state.budget_caps = {"iteration": total_iterations, "reviewer_pass": 1}
        state.current_agent.return_value = current_agent
        return state

    def test_complete_phase_returns_exit_success(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="complete")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitSuccessEffect)

    def test_failed_phase_returns_prepare_prompt_for_recovery(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="failed_terminal",
            previous_phase="development",
            last_error="Something went wrong",
            current_drain="development",
        )

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "development"

    def test_unknown_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="unknown_phase")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitFailureEffect)
        assert "Unknown phase" in effect.reason

    def test_default_planning_phase_uses_config_drain_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning")
        config = _config_with_agents(
            agent_chains={"plan_chain": ["claude"]},
            agent_drains={"planning": "plan_chain"},
        )

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree"), config=config
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_agent_prompt_materialization_reuses_prepared_planning_prompt(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace = FsWorkspace(tmp_path)
        (tmp_path / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
        (tmp_path / "PROMPT.md").write_text("Revise the plan", encoding="utf-8")
        (tmp_path / ".agent" / "artifacts" / "plan.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".agent" / "PLAN.md").write_text("existing plan", encoding="utf-8")
        (tmp_path / ".agent" / "tmp" / "planning_prompt.md").write_text(
            "prepared edit prompt",
            encoding="utf-8",
        )
        registry = MagicMock()
        registry.get.return_value = None

        runner_module._materialize_agent_prompt_if_needed(
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
                drain="planning",
            ),
            workspace,
            bundle,
            registry,
        )

        assert (tmp_path / ".agent" / "tmp" / "planning_prompt.md").read_text(
            encoding="utf-8"
        ) == "prepared edit prompt"
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists() is True
        assert (tmp_path / ".agent" / "PLAN.md").read_text(encoding="utf-8") == "existing plan"

    def test_development_phase_with_work_units_uses_fan_out_effect(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
            ),
        )
        config = _config_with_agents(
            agent_chains={"developer": ["claude"]},
            agent_drains={"development": "developer"},
        )

        effect = runner_module._determine_effect_from_policy(state, bundle, config=config)

        assert isinstance(effect, FanOutEffect)
        assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}

    def test_development_phase_with_single_work_unit_uses_invoke_agent_effect(self) -> None:
        """Single work unit must not trigger fan-out — fan-out requires >=2 units."""
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="development",
            work_units=(WorkUnit(unit_id="unit-a", description="A"),),
        )
        config = _config_with_agents(
            agent_chains={"developer": ["claude"]},
            agent_drains={"development": "developer"},
        )

        effect = runner_module._determine_effect_from_policy(state, bundle, config=config)

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.phase == "development"

    def test_policy_selected_parallel_phase_with_work_units_uses_fan_out_effect(self) -> None:
        state = PipelineState(
            phase="feature_build",
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=("src/a",)),
                WorkUnit(unit_id="unit-b", description="B", allowed_directories=("src/b",)),
            ),
        )
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"developer": AgentChainConfig(agents=["claude"])},
                agent_drains={"development": AgentDrainConfig(chain="developer")},
            ),
            pipeline=PipelinePolicy(
                phases={
                    "feature_build": PhaseDefinition(
                        drain="development",
                        transitions=PhaseTransition(on_success="complete"),
                        parallelization={"max_parallel_workers": 2},
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        role="terminal",
                        terminal_outcome="success",
                        transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                    ),
                },
                entry_phase="feature_build",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
        )

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, FanOutEffect)
        assert effect.work_units[0].unit_id == "unit-a"

    def test_commit_phase_with_requires_commit_uses_commit_effect(self, tmp_path: Path) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])
        )
        assert isinstance(effect, CommitEffect)

    def test_review_analysis_prefers_its_own_bound_chain_over_review_chain(self) -> None:
        state = PipelineState(
            phase="review_analysis",
            phase_chains={
                "review": AgentChainState(agents=["reviewer-agent"]),
                "review_analysis": AgentChainState(agents=["analysis-agent"]),
            },
        )
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={
                    "review_chain": AgentChainConfig(agents=["reviewer-agent"]),
                    "analysis_chain": AgentChainConfig(agents=["analysis-agent"]),
                },
                agent_drains={
                    "review": AgentDrainConfig(chain="review_chain"),
                    "review_analysis": AgentDrainConfig(chain="analysis_chain"),
                },
            ),
            pipeline=PipelinePolicy(
                phases={
                    "review_analysis": PhaseDefinition(
                        drain="review_analysis",
                        transitions=PhaseTransition(on_success="complete", on_loopback="fix"),
                    ),
                    "fix": PhaseDefinition(
                        drain="review",
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                    ),
                },
                entry_phase="review_analysis",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
        )

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "analysis-agent"
        assert effect.drain == "review_analysis"

    def test_missing_bound_agent_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        broken_bundle = bundle.model_copy(
            update={
                "agents": bundle.agents.model_copy(
                    update={
                        "agent_chains": {
                            **bundle.agents.agent_chains,
                            bundle.agents.agent_drains["development"].chain: MagicMock(agents=[]),
                        }
                    }
                )
            }
        )
        state = PipelineState(phase="development")

        effect = runner_module._determine_effect_from_policy(
            state, broken_bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitFailureEffect)

    def test_policy_driven_custom_phase_uses_config_drain_agent(self) -> None:
        state = PipelineState(phase="custom_phase")
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"custom_chain": AgentChainConfig(agents=["claude"])},
                agent_drains={"development": AgentDrainConfig(chain="custom_chain")},
            ),
            pipeline=PipelinePolicy(
                phases={
                    "custom_phase": PhaseDefinition(
                        drain="development",
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        transitions=PhaseTransition(
                            on_success="complete",
                            on_loopback="complete",
                        ),
                    ),
                },
                entry_phase="custom_phase",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        config = MagicMock()
        config.agent_chains = {"dev_chain": ["codex"]}
        config.agent_drains = {"development": "dev_chain"}

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree"), config=config
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "codex"
        assert effect.drain == "development"

    def test_commit_phase_prefers_config_commit_drain_over_policy_commit_chain(self) -> None:
        state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=False))
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"policy_commit_chain": AgentChainConfig(agents=["claude"])},
                agent_drains={"development_commit": AgentDrainConfig(chain="policy_commit_chain")},
            ),
            pipeline=PipelinePolicy(
                phases={
                    "development_commit": PhaseDefinition(
                        drain="development_commit",
                        role="commit",
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                    ),
                },
                entry_phase="development_commit",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        config = MagicMock()
        config.agent_chains = {"commit_chain": ["ccs/mm"]}
        config.agent_drains = {"development_commit": "commit_chain"}

        effect = runner_module._determine_effect_from_policy(
            state,
            bundle,
            WorkspaceScope("/tmp/worktree"),
            config=config,
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "ccs/mm"
        assert effect.drain == "development_commit"

    def test_handle_inline_prepare_prompt_updates_current_drain_from_policy(
        self,
        monkeypatch,
    ) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning", current_drain="planning")

        monkeypatch.setattr(
            runner_module,
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )

        updated = runner_module._handle_inline_effect(
            effect=PreparePromptEffect(phase="development", iteration=0, drain="development"),
            state=state,
            pipeline_policy=bundle.pipeline,
            artifacts_policy=bundle.artifacts,
            agents_policy=bundle.agents,
            workspace_scope=WorkspaceScope("/tmp/worktree"),
        )

        assert isinstance(updated, PipelineState)
        assert updated.phase == "development"
        assert updated.current_drain == "development"


def test_phase_output_artifact_paths_use_policy_drains_for_custom_phases() -> None:
    policy_bundle = _load_default_policy_bundle()
    assert runner_module._phase_output_artifact_paths(
        "feature_analysis",
        drain="development_analysis",
        policy_bundle=policy_bundle,
    ) == (
        ".agent/artifacts/development_analysis_decision.json",
        ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
    )
    assert runner_module._phase_output_artifact_paths(
        "feature_commit",
        drain="development_commit",
        policy_bundle=policy_bundle,
    ) == (
        ".agent/tmp/commit_message.json",
    )


class TestCommitEffect:
    def test_returns_commit_effect(self, tmp_path: Path) -> None:
        effect = runner_module._commit_effect(tmp_path)
        assert isinstance(effect, CommitEffect)
        assert ".agent/tmp/commit_message.json" in effect.message_file


def test_materialize_agent_prompt_if_needed_prefixes_claude_tools(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_materialize_prompt_for_phase(**kwargs):
        captured.update(kwargs)
        return "PROMPT.md"

    monkeypatch.setattr(
        runner_module, "materialize_prompt_for_phase", fake_materialize_prompt_for_phase
    )

    class Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return AgentConfig(
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                transport=AgentTransport.CLAUDE,
            )

    bundle = _load_default_policy_bundle()
    runner_module._materialize_agent_prompt_if_needed(
        InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt"),
        MagicMock(spec=["root"]),
        bundle,
        Registry(),
    )

    session_caps = cast("SessionCapabilities", captured["session_caps"])
    assert session_caps.tool_name_prefix == claude_tool_name_prefix()


def test_execute_agent_effect_uses_single_workspace_root(monkeypatch, tmp_path: Path) -> None:
    config = MagicMock()
    config.general.verbosity = 0
    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.md")
    agent_config = AgentConfig(cmd="codex", output_flag="--json-stream")

    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, config):
            del cls, config
            return RegistryInstance()

    seen: dict[str, object] = {}

    def fake_start_mcp_server(session, workspace, **_kwargs):
        seen["workspace_root"] = workspace.root

        class Bridge:
            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:9999/mcp"

        return Bridge()

    def fake_shutdown_mcp_server(_bridge) -> None:
        return None

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        seen["system_prompt_root"] = workspace_root
        seen["system_prompt_name"] = name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options=None,
    ):
        del config
        seen["prompt_file"] = prompt_file
        seen["workspace_path"] = options.workspace_path if options is not None else None
        return iter(())

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: runner_module.WorkspaceScope(tmp_path)
    )

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=Registry,
    )

    result = runner_module._execute_agent_effect(
        effect, config, deps, WorkspaceScope(tmp_path), display_context=make_display_context()
    )

    assert result == PipelineEvent.AGENT_SUCCESS
    assert seen["workspace_root"] == tmp_path
    assert seen["system_prompt_root"] == tmp_path
    assert seen["workspace_path"] == tmp_path


class TestPipelineRunnerLoop:
    def test_save_checkpoint_effect_triggers_checkpoint_and_returns_success(
        self,
        monkeypatch,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        effects = [SaveCheckpointEffect(), ExitSuccessEffect()]

        def stub_determine_effect(*_args, **_kwargs):
            return effects.pop(0)

        ckpt_save = MagicMock()
        reducer_events: list[object] = []

        def stub_reducer(current_state, event):
            reducer_events.append(event)
            return current_state, None

        def stub_reducer_with_policy(current_state, event, _policy=None):
            return stub_reducer(current_state, event)

        captured_console = _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(
            runner_module,
            "_call_determine_effect_from_policy",
            stub_determine_effect,
        )
        monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer_with_policy)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        ckpt_save.assert_called_once_with(state)
        assert reducer_events == [PipelineEvent.CHECKPOINT_SAVED]
        # Verify success message was printed (among other display calls)
        printed = captured_console.export_text()
        assert "Pipeline completed successfully" in printed

    def test_exit_failure_effect_enters_recovery(self, monkeypatch) -> None:
        state = PipelineState(phase="planning")
        captured_console = _install_runner_display_context(monkeypatch)
        effects = iter([ExitFailureEffect(reason="bad"), ExitSuccessEffect()])

        monkeypatch.setattr(
            runner_module,
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        printed = captured_console.export_text()
        assert "Recovery triggered: bad" in printed

    def test_keyboard_interrupt_triggers_checkpoint_and_returns_130(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "planning"
        interrupted_state = MagicMock()
        state.copy_with.return_value = interrupted_state

        def raise_interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        ckpt_save = MagicMock()
        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", raise_interrupt)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == INTERRUPT_EXIT_CODE
        state.copy_with.assert_called_once_with(interrupted_by_user=True)
        ckpt_save.assert_called_once_with(interrupted_state)

    def test_run_converts_system_exit_during_effect_execution_into_recovery(
        self, monkeypatch
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
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "_materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "_invoke_execute_effect_with_optional_display",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("boom")),
        )
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )

        def record_saved_state(saved_state: PipelineState) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        assert recovered_state.chain_for_phase("planning").retries == 1
        assert recovered_state.recovery_epoch == 0
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "boom" in recovered_state.last_error

    def test_run_converts_system_exit_during_effect_determination_into_recovery(
        self, monkeypatch
    ) -> None:
        state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["planner"])},
        )
        saved_states: list[PipelineState] = []
        calls = iter([SystemExit("determine blew up"), ExitSuccessEffect()])

        def determine_effect(*_args, **_kwargs):
            result = next(calls)
            if isinstance(result, BaseException):
                raise result
            return result

        def record_saved_state(saved_state: PipelineState) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(runner_module, "_call_determine_effect_from_policy", determine_effect)
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        assert recovered_state.chain_for_phase("planning").retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "determine blew up" in recovered_state.last_error

    def test_run_converts_system_exit_during_prepare_prompt_inline_handling_into_recovery(
        self, monkeypatch
    ) -> None:
        state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["planner"])},
        )
        effects = iter([PreparePromptEffect(phase="planning", iteration=0), ExitSuccessEffect()])
        saved_states: list[PipelineState] = []

        def record_saved_state(saved_state: PipelineState) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(
            runner_module,
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("prompt blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "planning"
        assert recovered_state.chain_for_phase("planning").retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "prompt blew up" in recovered_state.last_error

    def test_run_converts_system_exit_during_fanout_dispatch_into_recovery(
        self, monkeypatch
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

        def record_saved_state(saved_state: PipelineState) -> None:
            saved_states.append(saved_state)

        monkeypatch.setattr(
            runner_module,
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "_execute_fan_out_sync",
            lambda **_kwargs: (_ for _ in ()).throw(SystemExit("fanout blew up")),
        )
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", record_saved_state)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        assert saved_states
        recovered_state = saved_states[0]
        assert recovered_state.phase == "development"
        assert recovered_state.chain_for_phase("development").retries == 1
        assert recovered_state.last_error is not None
        assert "SystemExit" in recovered_state.last_error
        assert "fanout blew up" in recovered_state.last_error

    def test_failed_state_reenters_recovery_loop(self, monkeypatch) -> None:
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
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
            lambda *args, **kwargs: args[1],
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0

    def test_prepare_prompt_effect_advances_state_without_execute_effect(
        self,
        monkeypatch,
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

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_FAILURE)
        reducer = MagicMock()
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(
            runner_module,
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        state.copy_with.assert_called_once_with(
            phase="development",
            current_drain="development",
        )
        execute_effect.assert_not_called()
        reducer.assert_not_called()
        ckpt_save.assert_called_once_with(advanced_state)

    def test_invoke_agent_effect_materializes_prompt_before_execution(self, monkeypatch) -> None:
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

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "reducer_reduce", MagicMock(return_value=(state, None)))
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        materialize.assert_called_once()
        execute_effect.assert_called_once()

    def test_run_passes_policy_to_reducer(self, monkeypatch, tmp_path: Path) -> None:
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

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        reducer = MagicMock(return_value=(state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert result == 0
        reducer.assert_called_once_with(state, PipelineEvent.AGENT_SUCCESS, policy_bundle.pipeline)

    def test_run_uses_phase_handler_event_after_agent_execution(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        planning_state = PipelineState(
            phase="planning",
            phase_chains={"planning": AgentChainState(agents=["claude"], current_index=0, retries=3)},  # noqa: E501
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

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        reducer = MagicMock(return_value=(failed_state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_FAILURE])
        ckpt_save = MagicMock()
        _install_runner_display_context(monkeypatch)
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(
            MagicMock(), initial_state=planning_state, verbosity=Verbosity.QUIET
        )

        assert result == 0
        reducer.assert_called_once_with(
            planning_state,
            PipelineEvent.AGENT_FAILURE,
            policy_bundle.pipeline,
        )

    def test_run_notifies_subscriber_with_initial_state_before_loop(self, monkeypatch) -> None:
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
            "_call_determine_effect_from_policy",
            lambda *_args, **_kwargs: next(effects),
        )
        monkeypatch.setattr(
            runner_module,
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            runner_module,
            "_emit_phase_transition_if_changed",
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


class TestExecuteAgentEffect:
    class AgentError(Exception):
        pass

    class _FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:12345/mcp"

    @staticmethod
    def _config(verbosity: int = 2) -> MagicMock:
        config = MagicMock()
        config.general.verbosity = verbosity
        config.agents = {}
        config.ccs = CcsConfig()
        config.ccs_aliases = {"mm": "ccs mm"}
        return config

    def test_returns_success_when_invocation_succeeds(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        monkeypatch.setattr(
            runner_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge()
        )

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS

    def test_development_session_gets_expected_mcp_capabilities(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())
        captured: dict[str, object] = {}

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(session, *_args, **_kwargs):
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["capabilities"] == {
            "workspace.read",
            "workspace.metadata_read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "workspace.edit",
            "workspace.delete",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "media.read",
        }

    def test_custom_phase_uses_bound_drain_capabilities(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="custom_phase",
            prompt_file="PROMPT.md",
            drain="development",
        )
        registry = _registry_factory(MagicMock())
        captured: dict[str, object] = {}

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(session, *_args, **_kwargs):
            captured["drain"] = session.drain
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["drain"] == "development"
        assert captured["capabilities"] == {
            "workspace.read",
            "workspace.metadata_read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "workspace.edit",
            "workspace.delete",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "media.read",
        }

    def test_returns_failure_when_agent_missing(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(None)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    @pytest.mark.parametrize(
        ("phase", "artifact_paths"),
        [
            (
                "development",
                (
                    ".agent/artifacts/development_result.json",
                    ".agent/DEVELOPMENT_RESULT.md",
                ),
            ),
            (
                "development_analysis",
                (
                    ".agent/artifacts/development_analysis_decision.json",
                    ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
                ),
            ),
        ],
    )
    def test_execute_agent_effect_removes_stale_phase_artifact_before_invocation(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
        phase: str,
        artifact_paths: tuple[str, ...],
    ) -> None:
        effect = InvokeAgentEffect(
            agent_name="ccs/mm",
            phase=phase,
            prompt_file="PROMPT.md",
        )
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("prompt", encoding="utf-8")
        stale_artifacts = [tmp_path / artifact_path for artifact_path in artifact_paths]
        for stale_artifact in stale_artifacts:
            stale_artifact.parent.mkdir(parents=True, exist_ok=True)
            stale_artifact.write_text('{"type":"stale"}', encoding="utf-8")

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: str(prompt_file)
        )

        result = runner_module._execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=runner_module.AgentRegistry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        for stale_artifact in stale_artifacts:
            assert not stale_artifact.exists()

    def test_dynamic_ccs_agent_reaches_invocation(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(
            agent_name="ccs/mm",
            phase="development",
            prompt_file="PROMPT.md",
        )
        invoked: dict[str, object] = {}

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def record_invoke(config: AgentConfig, *_args, **_kwargs):
            invoked["cmd"] = config.cmd
            invoked["transport"] = config.transport
            return iter(["line"])

        result = runner_module._execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module._AgentExecutionDeps(
                invoke_agent=record_invoke,
                agent_invocation_error=self.AgentError,
                agent_registry=runner_module.AgentRegistry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert invoked == {"cmd": "ccs mm", "transport": AgentTransport.CLAUDE}

    def test_handles_invocation_error_gracefully(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_invoke(*_args, **_kwargs):
            raise self.AgentError("boom")

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=raising_invoke,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_handles_unexpected_error_as_failure(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_value_error(*_args, **_kwargs):
            raise ValueError("boom")

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=raising_value_error,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_starts_and_shuts_down_mcp_bridge_around_invocation(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        started: dict[str, bool] = {"value": False}
        shutdown: dict[str, bool] = {"value": False}

        class FakeBridge:
            def start(self) -> None:
                started["value"] = True

            def shutdown(self) -> None:
                shutdown["value"] = True

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        def fake_start_mcp_server(session, workspace, **_kwargs):
            bridge = FakeBridge()
            bridge.start()
            return bridge

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            fake_start_mcp_server,
        )

        seen_options: list[object] = []

        def record_invoke(*_args, **kwargs):
            seen_options.append(kwargs.get("options"))
            return iter(["line"])

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=record_invoke,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert started["value"] is True
        assert shutdown["value"] is True
        assert seen_options

    def test_starts_fresh_mcp_server_for_each_invocation(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        created: list[int] = []

        class FakeBridge:
            def __init__(self, marker: int) -> None:
                self.marker = marker

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return f"http://127.0.0.1:{12345 + self.marker}/mcp"

        def fake_start_mcp_server(*_args, **_kwargs):
            marker = len(created)
            created.append(marker)
            return FakeBridge(marker)

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        first = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )
        second = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert first == PipelineEvent.AGENT_SUCCESS
        assert second == PipelineEvent.AGENT_SUCCESS
        assert created == [0, 1]

    def test_streams_parsed_agent_activity_to_console_by_default(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def start(self) -> None:
                return

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        captured_console = _install_runner_display_context(monkeypatch)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"text_delta","delta":"thinking"}',
                        '{"type":"tool_use","name":"bash","input":{"command":"ls"}}',
                    ]
                ),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=runner_module.make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = captured_console.export_text()
        assert "thinking" in printed
        assert "bash" in printed

    def test_streams_non_text_parsed_events_too(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def start(self) -> None:
                return

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        captured_console = _install_runner_display_context(monkeypatch)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"thread.started"}',
                        '{"type":"result","result":"plan complete"}',
                        '{"type":"turn.completed"}',
                    ]
                ),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=runner_module.make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = captured_console.export_text()
        # Lifecycle events (thread.started) are suppressed — no noise in output
        assert "message_start" not in printed
        # Meaningful events still stream
        assert "plan complete" in printed
        assert "stop" in printed

    def test_retries_transient_connectivity_failures_with_session_resume(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("ship it", encoding="utf-8")
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = AgentConfig(
            cmd="claude -p",
            output_flag="--output-format=stream-json",
            print_flag="--print",
            streaming_flag="--include-partial-messages",
            session_flag="--resume {}",
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE,
        )
        registry = _registry_factory(agent_config)

        bridge_starts: list[int] = []

        class FakeBridge:
            def __init__(self, marker: int) -> None:
                self.marker = marker

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return f"http://127.0.0.1:{12345 + self.marker}/mcp"

        def fake_start_mcp_server(*_args, **_kwargs):
            marker = len(bridge_starts)
            bridge_starts.append(marker)
            return FakeBridge(marker)

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        seen_session_ids: list[str | None] = []

        def fake_invoke_agent(config, prompt_file, *, options=None):
            del config, prompt_file
            seen_session_ids.append(None if options is None else options.session_id)
            if len(seen_session_ids) == 1:

                def _first_attempt():
                    yield '{"session_id":"claude-session-42"}'
                    raise AgentInvocationError("claude", 1, "connection refused")

                return _first_attempt()
            return iter(
                ['{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}']
            )

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_session_ids == [None, "claude-session-42"]
        assert bridge_starts == [0, 1]

    def test_retries_inactivity_failures_with_summary_prompt(
        self,
        monkeypatch,
        tmp_path: Path,
    ) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the change", encoding="utf-8")
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        seen_prompt_files: list[str] = []

        def fake_invoke_agent(config, prompt_file, *, options=None):
            del config, options
            seen_prompt_files.append(prompt_file)
            if len(seen_prompt_files) == 1:

                def _first_attempt():
                    yield '{"type":"text","content":"drafted the fix"}'
                    raise AgentInactivityTimeoutError("codex", 30, ["drafted the fix"])

                return _first_attempt()
            return iter(['{"type":"result","result":"finished"}'])

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_prompt_files[0] == str(prompt_file)
        assert seen_prompt_files[1] != str(prompt_file)
        retry_prompt = Path(seen_prompt_files[1]).read_text(encoding="utf-8")
        assert "inactivity timeout" in retry_prompt
        assert "drafted the fix" in retry_prompt


def test_determine_effect_invokes_commit_agent_when_agent_not_yet_invoked(
    tmp_path: Path,
) -> None:
    policy_bundle = MagicMock()
    phase_def = MagicMock()
    phase_def.role = "commit"
    phase_def.drain = "development_commit"
    policy_bundle.pipeline.phases.get.return_value = phase_def

    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=False))
    workspace_scope = WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])
    config = _config_with_agents(
        agent_chains={"commit_chain": ["commit-agent"]},
        agent_drains={"development_commit": "commit_chain"},
    )

    effect = runner_module._determine_effect_from_policy(
        state,
        policy_bundle,
        workspace_scope,
        config=config,
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.agent_name == "commit-agent"
    assert effect.phase == "development_commit"


def test_determine_effect_commits_after_agent_invoked(
    tmp_path: Path,
) -> None:
    policy_bundle = MagicMock()
    phase_def = MagicMock()
    phase_def.role = "commit"
    policy_bundle.pipeline.phases.get.return_value = phase_def

    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))
    workspace_scope = WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])

    effect = runner_module._determine_effect_from_policy(state, policy_bundle, workspace_scope)

    assert isinstance(effect, CommitEffect)
    assert str(tmp_path) in effect.message_file
    assert "commit_message.json" in effect.message_file


class TestPhaseEventAfterAgentRun:
    @pytest.mark.parametrize(
        ("phase", "event", "artifact_path", "payload", "expected_title", "expected_text"),
        [
            (
                "planning",
                PipelineEvent.AGENT_SUCCESS,
                ".agent/artifacts/plan.json",
                {
                    "type": "plan",
                    "content": {
                        "summary": {
                            "context": "Planning handoff rendered from runner.",
                            "scope_items": [
                                {"text": "One"},
                                {"text": "Two"},
                                {"text": "Three"},
                            ],
                        },
                        "steps": [
                            {
                                "number": 1,
                                "title": "Plan",
                                "content": "Show the full plan",
                            }
                        ],
                        "critical_files": {
                            "primary_files": [
                                {
                                    "path": "ralph/pipeline/runner.py",
                                    "action": "modify",
                                }
                            ]
                        },
                        "risks_mitigations": [
                            {
                                "risk": "Hidden plan",
                                "mitigation": "Render after phase",
                            }
                        ],
                        "verification_strategy": [
                            {
                                "method": "pytest",
                                "expected_outcome": "plan block visible",
                            }
                        ],
                    },
                },
                "PLAN",
                "Planning handoff rendered from runner.",
            ),
            (
                "development",
                PipelineEvent.AGENT_SUCCESS,
                ".agent/artifacts/development_result.json",
                {
                    "type": "development_result",
                    "content": {
                        "status": "completed",
                        "summary": "Development result rendered from runner.",
                    },
                },
                "DEVELOPMENT RESULT",
                "Development result rendered from runner.",
            ),
            (
                "development_analysis",
                PipelineEvent.ANALYSIS_LOOPBACK,
                ".agent/artifacts/development_analysis_decision.json",
                {
                    "type": "development_analysis_decision",
                    "content": {
                        "status": "request_changes",
                        "summary": "Analysis result rendered from runner.",
                    },
                },
                "ANALYSIS: development_analysis",
                "Analysis result rendered from runner.",
            ),
        ],
    )
    def test_renders_phase_artifact_handoff_after_phase_handler_returns(  # noqa: PLR0913
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        phase: str,
        event: PipelineEvent,
        artifact_path: str,
        payload: dict[str, object],
        expected_title: str,
        expected_text: str,
    ) -> None:
        registry = MagicMock()
        registry.from_config.return_value = MagicMock()
        monkeypatch.setattr(runner_module, "AgentRegistry", registry)
        monkeypatch.setattr(runner_module, "ChainManager", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(runner_module, "handle_phase", lambda _effect, _ctx: [event])

        artifact_file = tmp_path / artifact_path
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text(json.dumps(payload), encoding="utf-8")

        output = io.StringIO()
        console = Console(file=output, force_terminal=False, color_system=None, width=120)
        display = ParallelDisplay(make_display_context(console=console, env={}))
        policy_bundle = _load_default_policy_bundle()
        workspace = MagicMock()
        workspace.absolute_path.side_effect = lambda path: str(tmp_path / path)

        returned_event = runner_module._phase_event_after_agent_run(
            effect=InvokeAgentEffect(agent_name="claude", phase=phase, prompt_file=f"{phase}.md"),
            config=MagicMock(),
            policy_bundle=policy_bundle,
            workspace=workspace,
            workspace_scope=WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path]),
            display=display,
        )

        assert returned_event == event
        rendered = output.getvalue()
        assert expected_title in rendered
        assert expected_text in rendered


class TestExecuteCommitEffect:
    def test_returns_success_when_commit_succeeds(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_called_once_with(str(tmp_path))
        create_commit.assert_called_once_with(str(tmp_path), "fix: pipeline artifact message")
        assert not message_file.exists()
        assert not text_file.exists()

    def test_renders_commit_message_before_cleanup_when_display_is_available(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        output = io.StringIO()
        display = ParallelDisplay(
            make_display_context(
                console=Console(file=output, force_terminal=False, color_system=None, width=120),
                env={},
            )
        )

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
            display=display,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        assert "COMMIT MESSAGE" in output.getvalue()
        assert "fix: pipeline artifact message" in output.getvalue()
        assert not message_file.exists()
        assert not text_file.exists()

    def test_returns_failure_when_create_commit_raises(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        def fail_create(*_):
            raise RuntimeError("boom")

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            fail_create,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        assert message_file.exists()
        assert text_file.exists()

    def test_returns_failure_when_message_file_missing(self, tmp_path: Path) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(tmp_path / "missing.txt")),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        create_commit.assert_not_called()

    def test_skips_commit_when_worktree_has_no_changes(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: skip empty worktree", encoding="utf-8")
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: skip empty worktree"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: False)

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SKIPPED
        stage_all.assert_not_called()
        create_commit.assert_not_called()
        assert not message_file.exists()
        assert not text_file.exists()


class TestExecuteEffect:
    def test_save_checkpoint_returns_checkpoint_event(self) -> None:
        result = runner_module._execute_effect(
            SaveCheckpointEffect(), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.CHECKPOINT_SAVED

    def test_execute_effect_with_optional_display_only_passes_supported_kwargs(
        self, monkeypatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_execute_effect(effect, config, workspace_scope, *, display):
            captured["effect"] = effect
            captured["config"] = config
            captured["workspace_scope"] = workspace_scope
            captured["display"] = display
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(runner_module, "_execute_effect", fake_execute_effect)
        effect = InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="plan.md")
        config = MagicMock()
        workspace_scope = WorkspaceScope("/tmp/worktree")
        display = MagicMock()
        state = PipelineState(phase="planning")

        result = runner_module._execute_effect_with_optional_display(
            effect,
            config,
            workspace_scope,
            display=display,
            state=state,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured == {
            "effect": effect,
            "config": config,
            "workspace_scope": workspace_scope,
            "display": display,
        }

    def test_commit_effect_delegates_to_commit_handler(self, monkeypatch) -> None:
        captured: dict[str, bool] = {}

        def stub_commit(  # noqa: PLR0913
            effect, create_commit, stage_all, repo_root, display=None, *, verbosity=None
        ):
            captured["called"] = True
            captured["message_file"] = effect.message_file
            return PipelineEvent.COMMIT_SUCCESS

        monkeypatch.setattr(runner_module, "_execute_commit_effect", stub_commit)
        result = runner_module._execute_effect(
            CommitEffect(message_file="foo"), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        assert captured.get("called")
        assert captured.get("message_file") == "foo"

    def test_unknown_effect_returns_failure(self) -> None:
        result = runner_module._execute_effect(
            PreparePromptEffect(phase="planning", iteration=0),
            MagicMock(),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE


class TestRenderAgentActivityLine:
    def test_tool_use_includes_human_readable_input_summary(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="bash",
            metadata={
                "tool": "bash",
                "input": {
                    "command": "pytest -q",
                    "workdir": "/tmp/project",
                },
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "bash" in rendered.plain
        assert "command=pytest -q" in rendered.plain
        assert "workdir=/tmp/project" in rendered.plain
        assert "{" not in rendered.plain

    def test_non_text_event_summary_avoids_raw_json_dump(self) -> None:
        output = AgentOutputLine(
            type="item_plan_result",
            metadata={
                "status": "completed",
                "summary": "Plan submitted",
                "result": {"steps": 3},
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "status=completed" in rendered.plain
        assert "summary=Plan submitted" in rendered.plain
        assert "{" not in rendered.plain

    def test_tool_result_renders_content(self) -> None:
        output = AgentOutputLine(
            type="tool_result",
            content="{'matches': 3, 'path': 'src'}",
            metadata={
                "tool": "grep",
                "input": {"pattern": "TODO", "path": "src"},
                "result": {"matches": 3, "path": "src"},
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "result" in rendered.plain
        assert "{'matches': 3, 'path': 'src'}" in rendered.plain

    def test_claude_assistant_text_renders_without_extra_assistant_summary_line(self) -> None:
        parser = ClaudeParser()
        parsed = list(
            parser.parse(
                iter(
                    [
                        (
                            '{"type":"assistant","message":{"content":['
                            '{"type":"text","text":"Final response"}]}}'
                        )
                    ]
                )
            )
        )

        rendered = []
        for output in parsed:
            rendered_line = runner_module._render_agent_activity_line(output, "dev")
            if rendered_line is not None:
                rendered.append(rendered_line)

        assert [item.plain for item in rendered] == ["dev: Final response"]

    def test_tool_use_output_escapes_markup_like_input_before_console_render(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="Write",
            metadata={
                "input": {
                    "file_path": "/tmp/[unsafe].py",
                    "newText": "[/{color}]",
                }
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "claude")

        assert rendered is not None

        console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
        console.print(rendered)

    def test_analysis_prompt_session_drain_preserves_analysis_identity(self) -> None:
        assert (
            runner_module._prompt_session_drain_for_phase("development_analysis")
            is SessionDrain.DEVELOPMENT_ANALYSIS
        )
        assert (
            runner_module._prompt_session_drain_for_phase("review_analysis")
            is SessionDrain.REVIEW_ANALYSIS
        )

    def test_prompt_session_drain_uses_policy_drain_class_for_custom_analysis_phase(
        self,
    ) -> None:
        agents_policy = AgentsPolicy(
            agent_chains={"planning_analysis": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "planning_analysis": AgentDrainConfig(
                    chain="planning_analysis",
                    drain_class="analysis",
                )
            },
        )

        assert (
            runner_module._prompt_session_drain_for_phase(
                "planning_analysis", agents_policy=agents_policy
            )
            is SessionDrain.ANALYSIS
        )

    def test_text_truncation_for_long_content(self) -> None:
        long_content = "a" * 300
        output = AgentOutputLine(type="text", content=long_content)

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_TEXT_MAX

    def test_tool_input_truncation(self) -> None:
        long_value = "x" * 200
        output = AgentOutputLine(
            type="tool_use",
            content="read_file",
            metadata={"input": {"path": long_value}},
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain

    def test_error_format_with_symbol(self) -> None:
        output = AgentOutputLine(type="error", content="something broke")

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "✗" in rendered.plain
        assert "something broke" in rendered.plain

    def test_record_activity_uses_metadata_tool_for_tool_backed_errors(self) -> None:
        subscriber = MagicMock()
        parsed_line = AgentOutputLine(
            type="error",
            content="Git diff requires capability 'GitDiffRead': 'denied'",
            metadata={"tool": "git_diff"},
        )
        rendered = Text("opencode tool error: git_diff denied")

        runner_module._record_activity_on_subscriber(subscriber, parsed_line, rendered, "opencode")

        subscriber.record_activity.assert_called_once_with(
            unit_id="opencode",
            agent_name="opencode",
            line="opencode tool error: git_diff denied",
            tool_name="git_diff",
            path=None,
            workdir=None,
            command=None,
        )

    def test_tool_result_brief_for_very_long_content(self) -> None:
        long_result = "z" * 600
        output = AgentOutputLine(type="tool_result", content=long_result)

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_RESULT_BRIEF_MAX

    def test_metadata_summary_caps_total_length(self) -> None:
        metadata: dict[str, object] = {
            "status": "a" * 50,
            "summary": "b" * 50,
            "phase": "c" * 50,
        }
        result = runner_module._metadata_summary(metadata)
        assert len(result) <= _TRUNCATED_METADATA_MAX


class TestTruncateEdgeCases:
    """Tests for _truncate edge cases."""

    def test_truncate_shorter_than_max_unchanged(self) -> None:
        assert runner_module._truncate("hello", 10) == "hello"

    def test_truncate_exactly_at_max_unchanged(self) -> None:
        assert runner_module._truncate("hello", 5) == "hello"

    def test_truncate_longer_than_max_adds_ellipsis(self) -> None:
        result = runner_module._truncate("hello world", 5)
        assert result == "hello…"
        assert len(result) == _TRUNCATE_RESULT_LEN

    def test_truncate_max_length_zero_returns_unchanged(self) -> None:
        assert runner_module._truncate("hello", 0) == "hello"

    def test_truncate_max_length_one_returns_unchanged(self) -> None:
        assert runner_module._truncate("hello", 1) == "hello"

    def test_truncate_empty_string(self) -> None:
        assert runner_module._truncate("", 10) == ""


class TestAvailableWidth:
    """Tests for _available_width helper."""

    def test_available_width_minimum_floor(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(runner_module, "_terminal_width", lambda: 20)
        # With prefix_len=20, width would be 20-20-2 = -2, should floor to 40
        assert runner_module._available_width(20) == _AVAILABLE_WIDTH_FLOOR

    def test_available_width_normal_terminal(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(runner_module, "_terminal_width", lambda: 120)
        result = runner_module._available_width(10)
        expected = 120 - 10 - 2
        assert result == expected

    def test_available_width_narrow_terminal(self, monkeypatch: MonkeyPatch) -> None:
        monkeypatch.setattr(runner_module, "_terminal_width", lambda: 50)
        result = runner_module._available_width(5)
        expected = 50 - 5 - 2
        assert result == expected


class TestStartCommitCapture:
    def test_run_pipeline_writes_start_commit_on_first_invocation(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:
        from ralph.pipeline.cycle_baseline import write_cycle_baseline as _real_write  # noqa: PLC0415, I001

        written: list[tuple[str, str]] = []

        def _spy_write(workspace_root, sha, *, force: bool = False):
            written.append((str(workspace_root), sha))
            _real_write(workspace_root, sha, force=force)

        monkeypatch.setattr(runner_module, "write_cycle_baseline", _spy_write)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        expected_sha = GitRepo(tmp_git_repo).head.commit.hexsha
        assert written, ".agent/start_commit was not written during run()"
        assert written[0][1] == expected_sha, (
            f"Expected SHA {expected_sha!r}, got {written[0][1]!r}"
        )

    def test_run_pipeline_does_not_overwrite_existing_start_commit(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:
        # Pre-write a sentinel SHA so run() sees the file as already present.
        # We make a second commit so the current HEAD differs from the sentinel,
        # ensuring a buggy "always-write" implementation would be caught.
        from ralph.pipeline.cycle_baseline import write_cycle_baseline as _real_write  # noqa: PLC0415, I001

        repo = GitRepo(tmp_git_repo)
        sentinel_sha = repo.head.commit.hexsha

        extra_file = tmp_git_repo / "extra.txt"
        extra_file.write_text("extra")
        repo.index.add(["extra.txt"])
        repo.index.commit("second commit")

        agent_dir = tmp_git_repo / ".agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "start_commit").write_text(sentinel_sha + "\n")

        written: list[tuple[str, str]] = []

        def _spy_write(workspace_root, sha, *, force: bool = False):
            written.append((str(workspace_root), sha))
            _real_write(workspace_root, sha, force=force)

        monkeypatch.setattr(runner_module, "write_cycle_baseline", _spy_write)
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert not written, (
            "run() must not overwrite an existing .agent/start_commit"
        )


def test_run_returns_1_when_mcp_validation_fails_in_strict_mode(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Strict-mode upstream validation failure aborts the pipeline before policy load."""
    bad_server = UpstreamMcpServer(name="broken", transport="http", url="http://127.0.0.1:1/mcp")

    def fake_upstreams(_workspace_root: Path) -> tuple[UpstreamMcpServer, ...]:
        return (bad_server,)

    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr("ralph.mcp.transport.common.mcp_toml_as_upstreams", fake_upstreams)
    monkeypatch.setattr("ralph.mcp.upstream.validation.strict_mode_from_env", lambda *_: True)

    def fake_validator(_servers: object, *, strict: bool) -> object:
        del strict
        raise UpstreamValidationError("upstream MCP server 'broken' is unreachable")

    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", fake_validator)

    rc = runner_module.run(MagicMock(), initial_state=None)
    assert rc == 1


def test_run_continues_when_mcp_toml_has_no_servers(
    monkeypatch: MonkeyPatch, tmp_git_repo: Path
) -> None:
    """Validation must be a no-op when no custom MCP servers are configured."""
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_git_repo)
    )
    monkeypatch.setattr("ralph.mcp.transport.common.mcp_toml_as_upstreams", lambda _root: ())

    def fail_validator(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validator should not run when no upstreams configured")

    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", fail_validator)
    monkeypatch.setattr(
        runner_module,
        "_determine_effect_from_policy",
        lambda _state, _bundle, _scope: ExitSuccessEffect(),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
    _install_runner_display_context(monkeypatch)

    state = MagicMock()
    state.phase = "planning"
    rc = runner_module.run(MagicMock(), initial_state=state)
    assert rc == 0


class TestPhaseHandlerExceptionGuard:
    """Tests for the phase handler exception guard in runner.py.

    When runner.py wraps handle_phase calls in try/except Exception, a handler that
    raises RuntimeError should result in PhaseFailureEvent(recoverable=True) being
    emitted rather than the exception propagating.
    """

    def _make_context(self) -> PhaseContext:
        """Create a mock phase context for testing."""
        workspace = MagicMock()
        workspace.exists.return_value = False
        return PhaseContext.construct(
            workspace=workspace,
            registry=MagicMock(),
            chain_manager=MagicMock(),
            pipeline_policy=MagicMock(),
            agents_policy=MagicMock(),
            artifacts_policy=MagicMock(),
        )

    def test_keyboard_interrupt_propagates_not_swallowed(self) -> None:
        """KeyboardInterrupt must propagate, not be caught by the exception guard."""

        def ki_handler(effect: Effect, ctx: PhaseContext) -> list[Event]:
            del effect, ctx
            raise KeyboardInterrupt()

        ctx = self._make_context()
        original_handler = HANDLERS.get("development")
        try:
            HANDLERS["development"] = ki_handler

            effect = InvokeAgentEffect(
                agent_name="developer",
                phase="development",
                prompt_file="development.txt",
            )

            with pytest.raises(KeyboardInterrupt):
                handle_phase(effect, ctx)
        finally:
            if original_handler is not None:
                HANDLERS["development"] = original_handler
            else:
                HANDLERS.pop("development", None)

    def test_phase_handler_system_exit_becomes_recoverable_failure_event(
        self, tmp_path: Path
    ) -> None:
        def exiting_handler(effect: Effect, ctx: PhaseContext) -> list[Event]:
            del effect, ctx
            raise SystemExit("phase blew up")

        original_handler = HANDLERS.get("development")
        try:
            HANDLERS["development"] = exiting_handler
            effect = InvokeAgentEffect(
                agent_name="developer",
                phase="development",
                prompt_file="development.txt",
            )
            policy_bundle = MagicMock()
            event = runner_module._phase_event_after_agent_run(
                effect=effect,
                config=UnifiedConfig(),
                policy_bundle=policy_bundle,
                workspace=FsWorkspace(tmp_path),
                display_context=make_display_context(),
            )
        finally:
            if original_handler is not None:
                HANDLERS["development"] = original_handler
            else:
                HANDLERS.pop("development", None)

        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert event.recoverable is True
        assert "SystemExit" in event.reason
        assert "phase blew up" in event.reason

    def test_phase_failure_event_recoverable_routes_through_reducer_retry(
        self,
    ) -> None:
        """PhaseFailureEvent(recoverable=True) should route through reducer retry."""
        state = PipelineState(
            phase="development",
            phase_chains={"development": AgentChainState(agents=["claude"], current_index=0, retries=0)},  # noqa: E501
        )
        event = PhaseFailureEvent(
            phase="development",
            reason="Phase handler crashed: RuntimeError: boom",
            recoverable=True,
        )

        new_state, effects = reducer_reduce(state, event)

        # Should increment retries, not fail
        assert new_state.chain_for_phase("development").retries == 1
        assert new_state.phase == "development"
        assert effects == []

    def test_phase_failure_event_not_recoverable_transitions_to_failed(
        self,
    ) -> None:
        """PhaseFailureEvent(recoverable=False) should transition to "failed"."""
        state = PipelineState(phase="development")
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="Analysis decision: FAILURE",
            recoverable=False,
        )

        new_state, _effects = reducer_reduce(state, event, _load_default_policy_bundle().pipeline)

        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert "FAILURE" in new_state.last_error


def test_phase_start_banner_emitted_to_parallel_display_console(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Phase-start banner is emitted to the ParallelDisplay console, not only legacy console.

    After the runner fix, _show_phase_start_with_context is called unconditionally
    with _display_console(display). For ParallelDisplay, this returns display.console
    so banners appear in the display's console output regardless of display type.
    """
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
    registry = _registry_factory(MagicMock())

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:12345/mcp"

    monkeypatch.setattr(runner_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge())
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
    monkeypatch.setattr(
        runner_module,
        "materialize_system_prompt",
        lambda *, workspace_root, name: str(tmp_path / "SYS.md"),
    )

    buf = io.StringIO()
    display_console = Console(file=buf, force_terminal=False, highlight=False, width=120)
    display = ParallelDisplay(make_display_context(console=display_console, env={}))

    config = MagicMock()
    config.general.verbosity = 2
    config.agents = {}
    config.ccs = CcsConfig()
    config.ccs_aliases = {}

    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 3, "reviewer_pass": 1},
    )

    runner_module._execute_agent_effect(
        effect,
        config,
        runner_module._AgentExecutionDeps(
            invoke_agent=lambda *_args, **_kwargs: iter([]),
            agent_invocation_error=RuntimeError,
            agent_registry=registry,
        ),
        WorkspaceScope(tmp_path),
        display=display,
        state=state,
        policy_bundle=_load_default_policy_bundle(),
    )

    out = buf.getvalue()
    assert "Development" in out
    assert "iteration 1/3" in out


class TestCycleBaselineLifecycle:
    """Regression tests: cycle baseline is cleared at dev-cycle boundaries."""

    def test_run_clears_baseline_at_teardown_on_success(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:
        from ralph.pipeline.cycle_baseline import write_cycle_baseline  # noqa: PLC0415

        write_cycle_baseline(
            tmp_git_repo, GitRepo(tmp_git_repo).head.commit.hexsha
        )
        assert (tmp_git_repo / ".agent" / "start_commit").exists()

        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert not (tmp_git_repo / ".agent" / "start_commit").exists(), (
            "run() must clear .agent/start_commit at pipeline teardown"
        )

    def test_run_clears_baseline_at_teardown_on_failure(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:
        from ralph.pipeline.cycle_baseline import write_cycle_baseline  # noqa: PLC0415

        write_cycle_baseline(
            tmp_git_repo, GitRepo(tmp_git_repo).head.commit.hexsha
        )
        baseline_path = tmp_git_repo / ".agent" / "start_commit"
        assert baseline_path.exists()

        cleared: list[bool] = []

        def _spy_clear(workspace_root):
            cleared.append(True)
            baseline_path.unlink(missing_ok=True)

        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle, _scope: ExitSuccessEffect(),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(runner_module, "clear_cycle_baseline", _spy_clear)

        state = MagicMock()
        state.phase = "planning"

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert cleared, (
            "run() must call clear_cycle_baseline in its finally/teardown block"
        )

    def test_run_pipeline_step_clears_baseline_after_development_commit_success(
        self, monkeypatch: MonkeyPatch, tmp_git_repo: Path
    ) -> None:
        from ralph.pipeline.cycle_baseline import write_cycle_baseline  # noqa: PLC0415

        write_cycle_baseline(
            tmp_git_repo, GitRepo(tmp_git_repo).head.commit.hexsha
        )
        baseline_path = tmp_git_repo / ".agent" / "start_commit"
        assert baseline_path.exists()

        cleared: list[bool] = []

        def _spy_clear(workspace_root):
            cleared.append(True)
            baseline_path.unlink(missing_ok=True)

        commit_effect = CommitEffect(message_file="/dev/null")
        call_count = {"n": 0}

        def _fake_determine_effect(_state, _bundle, _scope):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return commit_effect
            return ExitSuccessEffect()

        state = MagicMock()
        state.phase = "development_commit"
        state.copy_with = MagicMock(return_value=state)
        state.session_preserve_retry_pending = False

        monkeypatch.setattr(
            runner_module, "_determine_effect_from_policy", _fake_determine_effect
        )
        monkeypatch.setattr(
            runner_module,
            "resolve_workspace_scope",
            lambda: WorkspaceScope(tmp_git_repo),
        )
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())
        _install_runner_display_context(monkeypatch)
        monkeypatch.setattr(
            runner_module,
            "_execute_commit_effect",
            lambda *_args, **_kwargs: PipelineEvent.COMMIT_SUCCESS,
        )
        monkeypatch.setattr(
            runner_module,
            "_materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module, "clear_cycle_baseline", _spy_clear)
        monkeypatch.setattr(
            runner_module,
            "reducer_reduce",
            lambda _state, _event, _policy: (state, []),
        )

        runner_module.run(MagicMock(), initial_state=state, verbosity=Verbosity.QUIET)

        assert cleared, (
            "clear_cycle_baseline must be called after development_commit COMMIT_SUCCESS"
        )


def _make_minimal_policy(
    phases: dict[str, PhaseDefinition], *, entry_phase: str = "done"
) -> PipelinePolicy:
    return PipelinePolicy(phases=phases, terminal_phase="done", entry_phase=entry_phase)


class TestPhaseContextRoleBasedDispatch:
    """Verify _phase_context uses phase roles not hardcoded phase names."""

    def _call(
        self,
        state: PipelineState,
        previous_phase: str,
        policy: PipelinePolicy,
    ) -> dict[str, object]:
        return _phase_context(state, previous_phase, policy)

    def test_execution_role_shows_iteration_context(self) -> None:
        # Policy must have a commit phase so _find_commit_counter_from_phase can derive the
        # counter name from the transition chain: build -> build_commit (increments 'iteration').
        policy = _make_minimal_policy(
            {
                "build": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="build_commit"),
                ),
                "build_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="iteration"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(
            phase="build", outer_progress={"iteration": 2}, budget_caps={"iteration": 5}
        )
        ctx = self._call(state, "planning", policy)
        assert ctx.get("iteration") == "3/5"

    def test_review_role_shows_pass_context(self) -> None:
        # Policy must have a commit phase so _find_commit_counter_from_phase can derive the
        # counter name from the transition chain: qa -> qa_commit (increments 'reviewer_pass').
        policy = _make_minimal_policy(
            {
                "qa": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="issues_found",
                    transitions=PhaseTransition(on_success="qa_commit"),
                ),
                "qa_commit": PhaseDefinition(
                    drain="review_commit",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="reviewer_pass"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(
            phase="qa", outer_progress={"reviewer_pass": 1}, budget_caps={"reviewer_pass": 3}
        )
        ctx = self._call(state, "planning", policy)
        assert ctx.get("reviewer_pass") == "2/3"

    def test_analysis_to_commit_shows_approved_decision(self) -> None:
        policy = _make_minimal_policy(
            {
                "gate": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="seal"),
                    decisions={},
                    loop_policy=None,
                ),
                "seal": PhaseDefinition(
                    drain="development",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="build_pass"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(phase="seal")
        ctx = self._call(state, "gate", policy)
        assert ctx.get("decision") == "approved"

    def test_analysis_to_execution_shows_needs_changes_decision(self) -> None:
        policy = _make_minimal_policy(
            {
                "gate": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="seal", on_loopback="build"),
                    decisions={},
                    loop_policy=None,
                ),
                "build": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="gate"),
                ),
                "seal": PhaseDefinition(
                    drain="development",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="build_pass"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(phase="build")
        ctx = self._call(state, "gate", policy)
        assert ctx.get("decision") == "needs changes"

    def test_commit_role_previous_shows_counter_budget(self) -> None:
        policy = _make_minimal_policy(
            {
                "seal": PhaseDefinition(
                    drain="development",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="build_pass"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "build": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(
            phase="build",
            budget_remaining={"build_pass": 2},
        )
        ctx = self._call(state, "seal", policy)
        assert ctx.get("build_pass_budget") == "2 remaining"

    def test_unknown_phase_returns_empty_context(self) -> None:
        policy = PipelinePolicy.model_construct(phases={})
        state = PipelineState(phase="nonexistent")
        ctx = self._call(state, "also_nonexistent", policy)
        assert ctx == {}

    def test_execution_role_not_triggered_by_analysis_role_phase(self) -> None:
        policy = _make_minimal_policy(
            {
                "gate": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="done"),
                    decisions={},
                    loop_policy=None,
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(
            phase="gate", outer_progress={"iteration": 2}, budget_caps={"iteration": 5}
        )
        ctx = self._call(state, "planning", policy)
        assert "iteration" not in ctx

    def test_commit_role_with_none_counter_omits_budget(self) -> None:
        policy = _make_minimal_policy(
            {
                "seal": PhaseDefinition(
                    drain="development",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter=None),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "build": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            }
        )
        state = PipelineState(phase="build")
        ctx = self._call(state, "seal", policy)
        assert not any(k.endswith("_budget") for k in ctx)

    def test_analysis_uses_loop_counters_default_max_when_loop_caps_absent(self) -> None:
        """When loop_caps is absent, _phase_context should use loop_counters.default_max."""
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="planning_analysis_iteration",
                    ),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            },
        )
        # State WITHOUT loop_caps set - cap should come from loop_counters.default_max
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 2},  # 3rd iteration (0-indexed)
        )
        ctx = self._call(state, "planning_analysis", policy)
        # Should show 3/3 using loop_counters.default_max=3
        assert ctx.get("Planning Analysis") == "3/3"
        # Should indicate final since analysis_cur (2) >= max_iter - 1 (3 - 1 = 2)
        assert ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_loop_caps_takes_precedence_over_loop_counters(self) -> None:
        """state.loop_caps should take precedence over policy.loop_counters.default_max."""
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            },
        )
        # State WITH loop_caps set to 5 - should override loop_counters default_max=3
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 4},  # 5th iteration
            loop_caps={"planning_analysis_iteration": 5},
        )
        ctx = self._call(state, "planning_analysis", policy)
        assert ctx.get("Planning Analysis") == "5/5"
        assert ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_counter_renders_correctly_without_off_by_one(self) -> None:
        """Analysis counter should render as analysis_cur+1/max_iter without off-by-one errors."""
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=5),
            },
        )
        # First iteration: analysis_cur=0, should show 1/5, not final
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 0},
        )
        ctx = self._call(state, "planning_analysis", policy)
        assert ctx.get("Planning Analysis") == "1/5"
        assert ctx.get("analysis_status") is None

        # Last iteration: analysis_cur=4, should show 5/5 and be final
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 4},
        )
        ctx = self._call(state, "planning_analysis", policy)
        assert ctx.get("Planning Analysis") == "5/5"
        assert ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_counter_marks_single_allowed_run_as_final(self) -> None:
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=1),
            },
        )
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 0},
        )
        ctx = self._call(state, "planning_analysis", policy)
        assert ctx.get("Planning Analysis") == "1/1"
        assert ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_counter_marks_only_second_of_two_runs_as_final(self) -> None:
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=2),
            },
        )
        first_run = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 0},
        )
        first_ctx = self._call(first_run, "planning_analysis", policy)
        assert first_ctx.get("Planning Analysis") == "1/2"
        assert first_ctx.get("analysis_status") is None

        second_run = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 1},
        )
        second_ctx = self._call(second_run, "planning_analysis", policy)
        assert second_ctx.get("Planning Analysis") == "2/2"
        assert second_ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_counter_clamps_correctly_at_exact_cap_boundary(self) -> None:
        """When stored analysis_cur equals the cap, display should clamp to cap/max, not exceed it.

        This reproduces the bug where a post-reducer capped state with
        loop_iterations['planning_analysis_iteration'] == 3 and cap 3
        incorrectly rendered as '4/3' instead of '3/3'.
        """
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            },
        )
        # Capped state: analysis_iteration=3 equals cap=3 (exhausted after 3 completions)
        # Should display 3/3 NOT 4/3
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 3},
        )
        ctx = self._call(state, "planning_analysis", policy)
        assert ctx.get("Planning Analysis") == "3/3"
        assert ctx.get("analysis_status") == "final, skipping next"

    def test_analysis_to_execution_transition_shows_both_counters(self) -> None:
        """Analysis -> execution transition must show BOTH outer iteration AND analysis counter.

        This verifies the fix for the regression where _phase_context only emitted
        the analysis counter when previous_role='analysis', dropping the outer iteration
        counter that should also be visible in the banner alongside it.
        """
        # Policy needs a commit phase in the transition chain so _find_commit_counter_from_phase
        # can derive the counter name for the execution phase
        policy = PipelinePolicy(
            phases={
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                    transitions=PhaseTransition(on_success="planning"),
                    decisions={},
                ),
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="planning_commit"),
                ),
                "planning_commit": PhaseDefinition(
                    drain="planning_commit",
                    role="commit",
                    commit_policy=PhaseCommitPolicy(increments_counter="iteration"),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="planning",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            },
        )
        # State has both outer iteration progress (for 'planning' execution phase)
        # and loop iteration (for 'planning_analysis' analysis phase)
        state = PipelineState(
            phase="planning",
            outer_progress={"iteration": 0},  # 1st iteration (0-indexed)
            budget_caps={"iteration": 5},
            loop_iterations={"planning_analysis_iteration": 2},  # 3rd analysis (final)
        )
        ctx = self._call(state, "planning_analysis", policy)
        # Must show analysis counter
        assert ctx.get("Planning Analysis") == "3/3"
        assert ctx.get("analysis_status") == "final, skipping next"
        # Must ALSO show outer iteration counter (this was the bug - only analysis was shown)
        assert ctx.get("iteration") == "1/5"
        assert ctx.get("decision") == "needs changes"


class TestSkippedExhaustedAnalysisInfo:
    def test_detects_planning_analysis_skip(self) -> None:
        policy = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="planning_analysis"),
                ),
                "planning_analysis": PhaseDefinition(
                    drain="planning_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="development", on_loopback="planning"),
                    decisions={},
                    loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "planning_analysis_iteration": LoopCounterConfig(default_max=3),
            },
            entry_phase="planning",
            terminal_phase="done",
        )
        state = PipelineState(
            phase="development",
            loop_iterations={"planning_analysis_iteration": 3},
            loop_caps={"planning_analysis_iteration": 3},
        )

        skipped = runner_module._skipped_exhausted_analysis_info(
            "planning", "development", state, policy
        )

        assert skipped == (
            "planning_analysis",
            "Planning Analysis cap reached, skipping",
        )

    def test_detects_development_analysis_skip(self) -> None:
        policy = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit", on_loopback="development"
                    ),
                    decisions={},
                    loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "development_analysis_iteration": LoopCounterConfig(default_max=3),
            },
            entry_phase="development",
            terminal_phase="done",
        )
        state = PipelineState(
            phase="development_commit",
            loop_iterations={"development_analysis_iteration": 3},
            loop_caps={"development_analysis_iteration": 3},
        )

        skipped = runner_module._skipped_exhausted_analysis_info(
            "development", "development_commit", state, policy
        )

        assert skipped == (
            "development_analysis",
            "Development Analysis cap reached, skipping",
        )

    def test_detects_review_analysis_skip(self) -> None:
        policy = PipelinePolicy(
            phases={
                "fix": PhaseDefinition(
                    drain="fix",
                    role="execution",
                    transitions=PhaseTransition(on_success="review_analysis"),
                ),
                "review_analysis": PhaseDefinition(
                    drain="review_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="review_commit", on_loopback="fix"),
                    decisions={},
                    loop_policy=PhaseLoopPolicy(iteration_state_field="review_analysis_iteration"),
                ),
                "review_commit": PhaseDefinition(
                    drain="review_commit",
                    role="commit",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="review",
                    role="terminal",
                    transitions=PhaseTransition(on_success="done"),
                    terminal_outcome="success",
                ),
            },
            loop_counters={
                "review_analysis_iteration": LoopCounterConfig(default_max=2),
            },
            entry_phase="fix",
            terminal_phase="done",
        )
        state = PipelineState(
            phase="review_commit",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 2},
        )

        skipped = runner_module._skipped_exhausted_analysis_info(
            "fix", "review_commit", state, policy
        )

        assert skipped == (
            "review_analysis",
            "Review Analysis cap reached, skipping",
        )


def test_handle_inline_effect_routes_to_planning_when_plan_handoff_absent(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: missing plan handoff during recovery must route to planning, not crash.

    Mirrors the reported failure where developer_iteration.jinja raised
    'requires an existing plan handoff' during failed_terminal recovery,
    classified as ambiguous and retried indefinitely. After the fix, the runner
    catches MissingPlanHandoffError and routes the state back to the entry phase
    (planning) instead of crashing as an ambiguous 'Pipeline step crashed'.
    """
    bundle = _load_default_policy_bundle()
    state = PipelineState(
        phase=bundle.pipeline.recovery.failed_route,
        previous_phase="development",
        recovery_epoch=1,
    )

    monkeypatch.setattr(
        runner_module,
        "_materialize_prepared_prompt",
        MagicMock(
            side_effect=MissingPlanHandoffError(
                "Template 'developer_iteration.jinja' requires an existing plan handoff"
                " at .agent/PLAN.md"
            )
        ),
    )
    monkeypatch.setattr(runner_module, "ckpt", MagicMock())

    result = runner_module._handle_inline_effect(
        effect=PreparePromptEffect(
            phase="development",
            previous_phase="development",
            drain="development",
        ),
        state=state,
        pipeline_policy=bundle.pipeline,
        artifacts_policy=bundle.artifacts,
        agents_policy=bundle.agents,
        workspace_scope=WorkspaceScope(str(tmp_path)),
    )

    assert isinstance(result, PipelineState)
    assert result.phase == bundle.pipeline.entry_phase
    assert result.previous_phase == state.phase
    assert result.recovery_epoch == state.recovery_epoch + 1


def test_handle_inline_effect_propagates_plan_handoff_error_outside_recovery(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-recovery phases must not silently reroute on missing plan handoff.

    When prompt preparation raises MissingPlanHandoffError during an ordinary
    execution phase (not the failed_route recovery path), the exception must
    propagate rather than being converted into an automatic reroute to planning.
    """
    bundle = _load_default_policy_bundle()
    state = PipelineState(
        phase="development",
        previous_phase="planning",
        recovery_epoch=0,
    )

    monkeypatch.setattr(
        runner_module,
        "_materialize_prepared_prompt",
        MagicMock(
            side_effect=MissingPlanHandoffError(
                "Template 'developer_iteration.jinja' requires an existing plan handoff"
                " at .agent/PLAN.md"
            )
        ),
    )
    monkeypatch.setattr(runner_module, "ckpt", MagicMock())

    with pytest.raises(MissingPlanHandoffError):
        runner_module._handle_inline_effect(
            effect=PreparePromptEffect(
                phase="development",
                previous_phase="planning",
                drain="development",
            ),
            state=state,
            pipeline_policy=bundle.pipeline,
            artifacts_policy=bundle.artifacts,
            agents_policy=bundle.agents,
            workspace_scope=WorkspaceScope(str(tmp_path)),
        )
