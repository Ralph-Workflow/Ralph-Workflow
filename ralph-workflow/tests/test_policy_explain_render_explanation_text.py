"""Tests for the policy explanation feature (ralph/policy/explain.py and render.py)."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.explain import (
    explain_policy,
)
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
from ralph.policy.render import render_explanation_text

_DEFAULT_POLICY_DIR = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"

_CHECK_LOOP_MAX = 3


def _minimal_bundle(phases: dict[str, PhaseDefinition] | None = None) -> PolicyBundle:
    """Build a minimal PolicyBundle for testing."""
    if phases is None:
        phases = {
            "work": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        }
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={"main_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "planning": AgentDrainConfig(chain="main_chain"),
                "complete": AgentDrainConfig(chain="main_chain"),
            },
        ),
        pipeline=PipelinePolicy(
            phases=phases,
            entry_phase="work",
            terminal_phase="done",
        ),
        artifacts=ArtifactsPolicy(),
    )


class TestRenderExplanationText:
    def test_output_is_string(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_entry_phase_appears(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "work" in text
        assert "Entry phase" in text

    def test_terminal_phase_appears(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "done" in text
        assert "Terminal phase" in text or "TERMINAL" in text

    def test_default_policy_renders_phase_names(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        for name in ["planning", "development"]:
            assert name in text, f"Phase '{name}' not found in rendered output"

    def test_answers_what_happens_after_success(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "On success" in text

    def test_answers_when_retry_vs_fallback(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "retry" in text.lower() or "retries" in text.lower()

    def test_answers_when_commit_required(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "commit" in text.lower()

    def test_answers_when_parallel_execution_allowed(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "parallel" in text.lower()

    def test_recovery_policy_appears(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "RECOVERY" in text or "recovery" in text.lower()

    def test_loop_counters_section(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        if exp.loop_counters:
            assert "LOOP COUNTERS" in text

    def test_budget_counters_section(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        if exp.budget_counters:
            assert "BUDGET COUNTERS" in text

    def test_default_policy_renders_block_and_lifecycle_sections(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)

        assert "Entry block" in text
        assert "developer_iteration" in text
        assert "AUTHORED BLOCKS" in text
        assert "LIFECYCLE COMPLETION" in text
        assert "development_final_commit" in text
