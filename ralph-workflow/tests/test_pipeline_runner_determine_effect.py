"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    FanOutEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.config.models import UnifiedConfig


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


def _legacy_fan_out_policy_bundle() -> PolicyBundle:
    """Bundle that opts into the legacy ``ralph_fan_out`` dispatch mode.

    The bundled default in ``ralph/policy/defaults/pipeline.toml`` sets
    ``dispatch_mode = 'agent_subagents'`` on the development phase, so
    the existing routing tests that assert ``FanOutEffect`` must opt
    into the legacy path explicitly on their policy fixture.
    """
    bundle = _load_default_policy_bundle()
    dev_phase = bundle.pipeline.phases["development"]
    assert dev_phase.parallelization is not None
    legacy_parallelization = dev_phase.parallelization.model_copy(
        update={"dispatch_mode": "ralph_fan_out"}
    )
    legacy_dev_phase = dev_phase.model_copy(update={"parallelization": legacy_parallelization})
    legacy_phases = dict(bundle.pipeline.phases)
    legacy_phases["development"] = legacy_dev_phase
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": legacy_phases})}
    )


def _policy_bundle_with_loop_counter_max(counter_name: str, default_max: int) -> PolicyBundle:
    bundle = _load_default_policy_bundle()
    loop_counters = dict(bundle.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


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
    ctx = make_display_context(
        console=console,
        force_width=width,
    )
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return console


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> UnifiedConfig:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return cast("UnifiedConfig", config)


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.md").write_text(
        f"---\ntype: plan\nschema_version: 1\nintent_verb: modify\n---\n## Summary\n{context}\n",
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan.draft.md").write_text(
        f"---\ntype: plan\nschema_version: 1\nintent_verb: modify\n---\n## Summary\n{context}\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_resolve_display_defaults_to_legacy_console_display() -> None:
    display = runner_module.resolve_display(None, make_display_context())

    assert isinstance(display, runner_module.ParallelDisplay)


def test_materialize_agent_prompt_if_needed_rewrites_existing_prompt_on_fresh_planning_entry(
    tmp_path: Path,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Create a fresh plan")
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING EDIT MODE. Revise the existing execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase=None)
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "You are in PLANNING MODE" in rendered
    assert "PLANNING EDIT MODE" not in rendered


def test_materialize_agent_prompt_if_needed_rewrites_stale_planning_prompt_on_analysis_loopback(
    tmp_path: Path,
) -> None:
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Revise the plan")
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\nExisting plan\n",
    )
    workspace.write(
        ".agent/PLANNING_ANALYSIS_DECISION.md",
        "---\ntype: planning_analysis_decision\nstatus: request_changes\n---\n"
        "## Summary\n- [S1] Need revisions\n"
        "## What Came Up Short\n- [W1] issue\n"
        "## How To Fix\n- [F1] fix it\n",
    )
    workspace.write(
        ".agent/tmp/planning_prompt.md",
        "You are in PLANNING MODE. Create a detailed, structured execution plan.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase="planning_analysis")
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/planning_prompt.md")
    assert "PLANNING EDIT MODE" in rendered
    assert "You are in PLANNING MODE" not in rendered


@pytest.mark.parametrize("analysis_iteration", [2, 3, 4])
def test_materialize_agent_prompt_if_needed_rewrites_stale_development_prompt_on_analysis_loopback(
    tmp_path: Path,
    analysis_iteration: int,
) -> None:
    policy_bundle = _policy_bundle_with_loop_counter_max("development_analysis_iteration", 5)
    workspace = FsWorkspace(tmp_path)
    workspace.write(
        "PROMPT.md",
        f"Continue development after analysis iteration {analysis_iteration}",
    )
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n1. Continue implementing the feature\n",
    )
    workspace.write(
        ".agent/tmp/development_prompt.md",
        "You are in IMPLEMENTATION MODE. Execute the plan and make progress.",
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
        chain_name="development",
    )
    state = PipelineState(
        phase="development",
        previous_phase="development_analysis",
        loop_iterations={"development_analysis_iteration": analysis_iteration - 1},
    )
    registry = MagicMock()
    registry.get.return_value = None

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
    )

    rendered = workspace.read(".agent/tmp/development_prompt.md")
    assert "continuing a DEVELOPMENT iteration" in rendered
    assert "You are in IMPLEMENTATION MODE" not in rendered


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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "development"

    def test_failed_phase_recovery_replaces_terminal_drain_with_target_phase_drain(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(
            phase="failed_terminal",
            previous_phase="planning",
            last_error="Something went wrong",
            current_drain="failed_terminal",
        )

        effect = runner_module.determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "planning"
        assert effect.drain == "planning"

    def test_unknown_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="unknown_phase")

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree"), config=config
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_agent_prompt_materialization_rewrites_existing_planning_prompt(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace = FsWorkspace(tmp_path)
        (tmp_path / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
        (tmp_path / "PROMPT.md").write_text("Revise the plan", encoding="utf-8")
        (tmp_path / ".agent" / "artifacts" / "plan.md").write_text("{}", encoding="utf-8")
        (tmp_path / ".agent" / "PLAN.md").write_text("existing plan", encoding="utf-8")
        (tmp_path / ".agent" / "tmp" / "planning_prompt.md").write_text(
            "prepared edit prompt",
            encoding="utf-8",
        )
        registry = MagicMock()
        registry.get.return_value = None

        runner_module.materialize_agent_prompt_if_needed(
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
                drain="planning",
            ),
            PipelineState(phase="planning", previous_phase=None),
            workspace,
            bundle,
            registry,
        )

        rendered = (tmp_path / ".agent" / "tmp" / "planning_prompt.md").read_text(encoding="utf-8")
        assert "You are in PLANNING MODE" in rendered
        assert "prepared edit prompt" not in rendered
        assert (tmp_path / ".agent" / "artifacts" / "plan.md").exists() is False
        assert (tmp_path / ".agent" / "PLAN.md").exists() is False

    def test_development_phase_with_work_units_uses_fan_out_effect(self) -> None:
        bundle = _legacy_fan_out_policy_bundle()
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

        effect = runner_module.determine_effect_from_policy(state, bundle, config=config)

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

        effect = runner_module.determine_effect_from_policy(state, bundle, config=config)

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

        effect = runner_module.determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, FanOutEffect)
        assert effect.work_units[0].unit_id == "unit-a"

    def test_commit_phase_with_requires_commit_uses_commit_effect(self, tmp_path: Path) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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

        effect = runner_module.determine_effect_from_policy(
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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning", current_drain="planning")

        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )

        updated = runner_module.handle_inline_effect(
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
