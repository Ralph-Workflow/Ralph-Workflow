"""Tests for the policy explanation feature (ralph/policy/explain.py and render.py)."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.explain import (
    explain_policy,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
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


class TestVerificationExplainRendering:
    """Tests for explain_policy and render_explanation_text with verification-role phases."""

    def _bundle_with_verification(
        self, kind: str = "artifact", on_failure_route: str | None = "crashed"
    ) -> PolicyBundle:

        return PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["claude"])},
                agent_drains={
                    "verify_drain": AgentDrainConfig(chain="c"),
                    "complete": AgentDrainConfig(chain="c"),
                    "crashed": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=PipelinePolicy(
                phases={
                    "verify": PhaseDefinition(
                        drain="verify_drain",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind=kind,
                            gate_for="advancement",
                            on_failure_route=on_failure_route,
                        ),
                        transitions=PhaseTransition(on_success="complete"),
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
                entry_phase="verify",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(),
        )

    def test_verification_block_appears_in_phase_explanation(self) -> None:
        bundle = self._bundle_with_verification()
        result = explain_policy(bundle)
        verify = next(p for p in result.phases if p.name == "verify")
        assert verify.verification is not None
        assert verify.verification.kind == "artifact"
        assert verify.verification.gate_for == "advancement"
        assert verify.verification.on_failure_route == "crashed"

    def test_verification_text_render_includes_kind_and_gate(self) -> None:
        bundle = self._bundle_with_verification()
        result = explain_policy(bundle)
        text = render_explanation_text(result)
        assert "kind=artifact" in text
        assert "gates=advancement" in text

    def test_verification_explanation_sentence_includes_failure_route(self) -> None:

        bundle = self._bundle_with_verification(on_failure_route="crashed")
        result = explain_policy(bundle)
        verify = next(p for p in result.phases if p.name == "verify")
        sentences = render_explanation_sentences(verify)
        combined = " ".join(sentences)
        assert "crashed" in combined
