"""Unit tests for the orchestrator module.

Tests cover:
- determine_next_effect returns InvokeAgentEffect when agent not yet invoked
- analysis loopback routes back to development phase
- analysis success routes to development_commit
- development_commit with budget=0 routes to review
- unknown phase raises PhaseHandlerNotFoundError
"""

from __future__ import annotations

from typing import Any

import pytest

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_FAILED,
)
from ralph.pipeline.effects import (
    ExitFailureEffect,
    ExitSuccessEffect,
    PreparePromptEffect,
)
from ralph.pipeline.orchestrator import (
    PhaseHandlerNotFoundError,
    determine_next_effect,
    resolve_next_phase,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)


def _make_minimal_agents_policy() -> AgentsPolicy:
    """Create a minimal agents policy for testing."""
    return AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(
                agents=["claude", "opencode"], max_retries=3
            ),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "review": AgentChainConfig(agents=["claude"], max_retries=3),
            "fix": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_commit": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "review": AgentDrainConfig(chain="review"),
            "fix": AgentDrainConfig(chain="fix"),
            "review_commit": AgentDrainConfig(chain="review_commit"),
        },
    )


def _make_minimal_pipeline_policy() -> PipelinePolicy:
    """Create a minimal pipeline policy for testing."""
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                embeds_analysis=True,
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                requires_commit=True,
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure="failed",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                embeds_analysis=True,
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback="fix",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review"),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                requires_commit=True,
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )


def _make_state(phase: str = "planning", **overrides: Any) -> PipelineState:
    """Create a pipeline state for testing."""
    return PipelineState(phase=phase, **overrides)


class TestDetermineNextEffect:
    """Tests for determine_next_effect function."""

    def test_complete_phase_returns_exit_success(self) -> None:
        """Test that PHASE_COMPLETE returns ExitSuccessEffect."""
        state = _make_state(phase=PHASE_COMPLETE)
        agents = _make_minimal_agents_policy()
        pipeline = _make_minimal_pipeline_policy()

        effect = determine_next_effect(state, pipeline, agents)

        assert isinstance(effect, ExitSuccessEffect)

    def test_failed_phase_returns_exit_failure(self) -> None:
        """Test that PHASE_FAILED returns ExitFailureEffect."""
        state = _make_state(phase=PHASE_FAILED, last_error="Test error")
        agents = _make_minimal_agents_policy()
        pipeline = _make_minimal_pipeline_policy()

        effect = determine_next_effect(state, pipeline, agents)

        assert isinstance(effect, ExitFailureEffect)
        assert effect.reason == "Test error"

    def test_unknown_phase_raises_handler_not_found(self) -> None:
        """Test that an unknown phase raises PhaseHandlerNotFoundError."""
        state = _make_state(phase="nonexistent_phase")
        agents = _make_minimal_agents_policy()
        pipeline = _make_minimal_pipeline_policy()

        with pytest.raises(PhaseHandlerNotFoundError) as exc_info:
            determine_next_effect(state, pipeline, agents)

        assert "nonexistent_phase" in str(exc_info.value)

    def test_planning_phase_returns_prepare_prompt(self) -> None:
        """Test that planning phase returns PreparePromptEffect on first invocation."""
        state = _make_state(phase="planning")
        agents = _make_minimal_agents_policy()
        pipeline = _make_minimal_pipeline_policy()

        effect = determine_next_effect(state, pipeline, agents)

        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "planning"


class TestAnalysisRouting:
    """Tests for analysis loopback and success routing."""

    def test_development_on_loopback_routes_to_development(self) -> None:
        """Test that development phase on loopback routes back to development."""
        next_phase = resolve_next_phase(
            current_phase="development",
            signal="loopback",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "development"

    def test_development_on_success_routes_to_development_commit(self) -> None:
        """Test that development phase on success routes to development_commit."""
        next_phase = resolve_next_phase(
            current_phase="development",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "development_commit"

    def test_review_on_loopback_routes_to_fix(self) -> None:
        """Test that review phase on loopback routes to fix."""
        next_phase = resolve_next_phase(
            current_phase="review",
            signal="loopback",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "fix"

    def test_review_on_success_routes_to_review_commit(self) -> None:
        """Test that review phase on success routes to review_commit."""
        next_phase = resolve_next_phase(
            current_phase="review",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "review_commit"

    def test_fix_on_success_routes_to_review(self) -> None:
        """Test that fix phase on success routes back to review."""
        next_phase = resolve_next_phase(
            current_phase="fix",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "review"


class TestCommitBudgetRouting:
    """Tests for commit-gated phase routing based on budget."""

    def test_development_commit_with_budget_routes_to_review(self) -> None:
        """Test that development_commit with budget > 0 routes to review."""
        next_phase = resolve_next_phase(
            current_phase="development_commit",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "review"

    def test_review_commit_on_success_routes_to_complete(self) -> None:
        """Test that review_commit on success routes to complete."""
        next_phase = resolve_next_phase(
            current_phase="review_commit",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "complete"


class TestResolveNextPhase:
    """Tests for resolve_next_phase function."""

    def test_unknown_phase_raises_value_error(self) -> None:
        """Test that resolving transition from unknown phase raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            resolve_next_phase(
                current_phase="nonexistent",
                signal="success",
                pipeline=_make_minimal_pipeline_policy(),
            )

    def test_unknown_signal_raises_value_error(self) -> None:
        """Test that unknown signal raises ValueError."""
        with pytest.raises(ValueError, match="Unknown signal"):
            resolve_next_phase(
                current_phase="development",
                signal="unknown_signal",
                pipeline=_make_minimal_pipeline_policy(),
            )

    def test_missing_transition_raises_value_error(self) -> None:
        """Test that missing transition target raises ValueError."""
        # Create a pipeline where development has no on_failure transition
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                        # No on_failure defined
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        with pytest.raises(ValueError, match="on_failure.*not.*defined"):
            resolve_next_phase(
                current_phase="development",
                signal="failure",
                pipeline=pipeline,
            )

    def test_terminal_transition_returns_terminal_state(self) -> None:
        """Test that transition to 'complete' or 'failed' is returned as-is."""
        next_phase = resolve_next_phase(
            current_phase="review_commit",
            signal="success",
            pipeline=_make_minimal_pipeline_policy(),
        )
        assert next_phase == "complete"
