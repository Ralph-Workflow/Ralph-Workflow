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
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
)
from ralph.policy.render import render_explanation_sentences, render_explanation_text

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


class TestExplanationSentencesProductOutcomeD:
    """Tests for the three Product Outcome D sentence types."""

    def _bundle_with_verification_and_commit(
        self, on_failure_route: str = "crashed"
    ) -> PolicyBundle:

        return PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["claude"])},
                agent_drains={
                    "verify_drain": AgentDrainConfig(chain="c"),
                    "commit_drain": AgentDrainConfig(chain="c"),
                    "complete": AgentDrainConfig(chain="c"),
                    "crashed": AgentDrainConfig(chain="c"),
                    "entry_drain": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=PipelinePolicy(
                phases={
                    "entry": PhaseDefinition(
                        drain="entry_drain",
                        role="execution",
                        transitions=PhaseTransition(on_success="verify"),
                    ),
                    "verify": PhaseDefinition(
                        drain="verify_drain",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="artifact",
                            gate_for="advancement",
                            on_failure_route=on_failure_route,
                        ),
                        transitions=PhaseTransition(on_success="commit_phase"),
                    ),
                    "commit_phase": PhaseDefinition(
                        drain="commit_drain",
                        role="commit",
                        transitions=PhaseTransition(
                            on_success="complete",
                            on_failure="crashed",
                        ),
                        commit_policy=PhaseCommitPolicy(
                            increments_counter="cycles",
                            loop_resets=[],
                        ),
                    ),
                    "crashed": PhaseDefinition(
                        drain="crashed",
                        role="terminal",
                        terminal_outcome="failure",
                        transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        role="terminal",
                        terminal_outcome="success",
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                },
                entry_phase="entry",
                terminal_phase="complete",
                budget_counters={
                    "cycles": BudgetCounterConfig(
                        tracks_budget=True, description="cycle counter", default_max=5
                    ),
                },
                post_commit_routes=[
                    PostCommitRoute(
                        when=PostCommitRouteWhen(phase="commit_phase", budget_state="remaining"),
                        target="entry",
                    ),
                    PostCommitRoute(
                        when=PostCommitRouteWhen(phase="commit_phase", budget_state="exhausted"),
                        target="complete",
                    ),
                    PostCommitRoute(
                        when=PostCommitRouteWhen(phase="commit_phase", budget_state="no_review"),
                        target="complete",
                    ),
                ],
            ),
            artifacts=ArtifactsPolicy(),
        )

    def test_verification_failure_route_sentence_emitted(self) -> None:

        bundle = self._bundle_with_verification_and_commit()
        result = explain_policy(bundle)
        verify = next(p for p in result.phases if p.name == "verify")
        sentences = render_explanation_sentences(verify)
        combined = " ".join(sentences)
        assert "fails verification" in combined
        assert "crashed" in combined

    def test_parallel_rejection_sentence_for_non_terminal_phase(self) -> None:

        bundle = self._bundle_with_verification_and_commit()
        result = explain_policy(bundle)
        entry = next(p for p in result.phases if p.name == "entry")
        assert not entry.has_parallelization
        sentences = render_explanation_sentences(entry)
        combined = " ".join(sentences)
        assert "parallel execution is rejected" in combined

    def test_no_parallel_rejection_for_phase_with_parallelization(self) -> None:

        bundle = load_policy(_DEFAULT_POLICY_DIR)
        result = explain_policy(bundle)
        development = next(p for p in result.phases if p.name == "development")
        assert development.has_parallelization
        sentences = render_explanation_sentences(development)
        combined = " ".join(sentences)
        assert "parallel execution is rejected" not in combined

    def test_post_commit_route_sentences_emitted(self) -> None:

        bundle = self._bundle_with_verification_and_commit()
        result = explain_policy(bundle)
        commit = next(p for p in result.phases if p.name == "commit_phase")
        assert commit.post_commit_routes_info
        sentences = render_explanation_sentences(commit)
        combined = " ".join(sentences)
        assert "after commit phase" in combined
        assert "budget_state" in combined

    def test_default_policy_emits_parallel_rejection_sentences(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        result = explain_policy(bundle)
        text = render_explanation_text(result)
        assert "Explanation: parallel execution is rejected" in text

    def test_default_policy_emits_post_commit_route_sentences(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        result = explain_policy(bundle)
        text = render_explanation_text(result)
        assert "Explanation: after commit phase" in text
