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
from ralph.policy.render import render_explanation_ascii


def _get_default_policy_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "ralph" / "policy" / "defaults",
        Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Default policy directory not found in any candidate location")


class TestVerificationAsciiAnnotation:
    def _bundle_with_verification(self) -> PolicyBundle:

        return PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["claude"])},
                agent_drains={
                    "verify_drain": AgentDrainConfig(chain="c"),
                    "complete": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=PipelinePolicy(
                phases={
                    "verify": PhaseDefinition(
                        drain="verify_drain",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="artifact",
                            gate_for="advancement",
                            on_failure_route=None,
                        ),
                        transitions=PhaseTransition(on_success="complete"),
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

    def test_ascii_includes_verify_annotation(self) -> None:
        bundle = self._bundle_with_verification()
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)
        assert "[verify: kind=artifact" in output
        assert "gates=advancement" in output
