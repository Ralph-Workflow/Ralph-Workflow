"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    BudgetCounterConfig,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


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


def _policy_bundle_with_loop_counter(counter_name: str, default_max: int) -> PolicyBundle:
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

        state = runner_module.create_initial_state(
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

        state = runner_module.create_initial_state(
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

        state = runner_module.create_initial_state(
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

        state = runner_module.create_initial_state(
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

        state = runner_module.create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )

        assert state.chain_for_phase("development_analysis").agents == ["config-analysis-agent"]

    def test_creates_state_with_correct_development_budget(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module.create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"iteration": DEVELOPER_ITERATIONS},
        )
        assert state.get_budget_remaining("iteration") == DEVELOPER_ITERATIONS

    def test_creates_state_with_correct_review_budget(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module.create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"reviewer_pass": REVIEWER_PASSES},
        )
        assert state.get_budget_remaining("reviewer_pass") == REVIEWER_PASSES

    def test_creates_state_with_zero_review_budget_when_r_zero(self) -> None:
        config = MagicMock()
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module.create_initial_state(
            config,
            pipeline_policy=_load_default_policy_bundle().pipeline,
            counter_overrides={"reviewer_pass": 0},
        )
        assert state.get_budget_remaining("reviewer_pass") == 0
