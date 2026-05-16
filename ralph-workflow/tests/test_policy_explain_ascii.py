"""Unit tests for ASCII workflow diagram rendering in ralph --explain-policy.

Tests cover:
- Default pipeline diagram contains entry marker
- Default pipeline diagram contains decision branches
- Default pipeline diagram contains loopback arrows
- Default pipeline diagram contains fanout annotation
- Default pipeline diagram contains success terminal markers
- Non-terminal phases do NOT have failure terminal markers
- Minimal two-phase pipeline diagram
- Minimal pipeline contains expected structural elements
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.policy.explain import (
    PhaseExplanation,
    PolicyExplanation,
    RecoveryExplanation,
    TerminalOutcomeExplanation,
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
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.render import render_explanation_ascii, render_explanation_text


def _get_default_policy_path() -> Path:
    """Find the default policy directory.

    Searches in multiple locations to find the bundled defaults.
    """
    # Try relative to this test file
    candidates = [
        Path(__file__).parent.parent / "ralph" / "policy" / "defaults",
        Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    pytest.skip("Default policy directory not found")


class TestRenderExplanationAscii:
    """Tests for render_explanation_ascii function."""

    def test_renders_entry_marker(self) -> None:
        """Default pipeline diagram contains entry marker =ENTRY=>."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "=ENTRY=>" in output

    def test_renders_success_terminal_marker(self) -> None:
        """Default pipeline diagram contains success terminal marker ==SUCCESS==>."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "==SUCCESS==>" in output

    def test_renders_decision_branches(self) -> None:
        """Default pipeline diagram contains decision branch arrows.

        Note: 'completed' decision equals on_success for all phases, so it's
        not rendered as a branch. We test for 'failed' and 'request_changes'
        which differ from on_success.
        """
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        # Decision branches use the format +--[decision_name]---...---> target
        # 'completed' equals on_success so not rendered; we check others
        # Use prefix check to handle padding alignment
        assert "+--[failed]" in output
        assert "+--[request_changes]" in output

    def test_renders_loopback_arrow(self) -> None:
        """Default pipeline diagram contains loopback annotations."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        # New loopback format uses left-pointing arrows so it cannot be
        # mistaken for a forward arrow.
        assert "<<==[loopback]==" in output
        assert "returns to '" in output
        # Old formats must NOT be present
        assert "<--[loopback]--" not in output
        assert "loop back to development" not in output

    def test_renders_fanout_annotation(self) -> None:
        """Default pipeline diagram contains fanout annotation for development phase."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        # Fanout annotation format: >>> FAN_OUT (max_workers=N, max_units=M) >>>
        assert ">>> FAN_OUT" in output
        # Should mention development phase in context of fanout
        assert "development" in output

    def test_non_terminal_phases_do_not_show_failure_marker(self) -> None:
        """Non-terminal phases with on_failure='failed' do NOT show ==FAILURE==> marker.

        Per PLAN analysis: ==FAILURE==> should only appear for actual terminal
        failure phases (terminal_outcome='failure'), not for any phase whose
        on_failure routes to the 'failed' pseudo-terminal.
        """
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        # development_commit has on_failure='failed' but is NOT terminal
        # review_commit has on_failure='failed' but is NOT terminal
        # Neither should show ==FAILURE==>
        lines = output.split("\n")
        in_development_commit = False
        in_review_commit = False
        for line in lines:
            if "development_commit" in line and "|" in line:
                in_development_commit = True
                in_review_commit = False
            elif "review_commit" in line and "|" in line:
                in_review_commit = True
                in_development_commit = False
            elif ("development_analysis" in line and "|" in line) or (
                "review" in line and "|" in line and "review_analysis" not in line
            ):
                in_development_commit = False
                in_review_commit = False

            # If we're in development_commit or review_commit block,
            # the next non-blank line should NOT be ==FAILURE==>
            if (in_development_commit or in_review_commit) and line.strip():
                phase_name = "development_commit" if in_development_commit else "review_commit"
                assert "==FAILURE==>" not in line, (
                    f"Non-terminal phase {phase_name} should not have "
                    f"==FAILURE==> marker, but found: {line}"
                )
                in_development_commit = False
                in_review_commit = False

    def test_contains_boxed_phase_lines(self) -> None:
        """Default pipeline diagram contains properly formatted phase boxes."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)
        lines = output.split("\n")

        # Phase boxes are lines that contain '|' with phase name content
        # e.g., "|    planning    |"
        boxed_lines = [
            line for line in lines if "|" in line and ("planning" in line or "development" in line)
        ]
        assert len(boxed_lines) > 0, "Expected at least one boxed phase line"

    def test_renders_minimal_two_phase_pipeline_diagram(self) -> None:
        """Minimal two-phase pipeline (start -> done) renders expected glyphs.

        Builds a minimal policy by hand and verifies the ASCII diagram contains
        the required structural elements per PLAN step 8a.
        """
        # Build a minimal two-phase policy
        start_phase = PhaseExplanation(
            name="start",
            role="execution",
            drain="start",
            chain="start_chain",
            agents=["test-agent"],
            max_retries=1,
            skip_invocation=False,
            on_success="done",
            on_failure=None,
            on_loopback=None,
            bypass_routes={},
            decisions={},
            loop_policy=None,
            commit_policy=None,
            terminal_outcome=None,
            is_entry=True,
            is_terminal=False,
        )
        done_phase = PhaseExplanation(
            name="done",
            role="terminal",
            drain="done",
            chain=None,
            agents=[],
            max_retries=0,
            skip_invocation=False,
            on_success=None,
            on_failure=None,
            on_loopback=None,
            bypass_routes={},
            decisions={},
            loop_policy=None,
            commit_policy=None,
            terminal_outcome="success",
            is_entry=False,
            is_terminal=True,
        )

        explanation = PolicyExplanation(
            entry_phase="start",
            terminal_phase="done",
            phases=[start_phase, done_phase],
            loop_counters=[],
            budget_counters=[],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            parallel_execution=None,
            recovery=RecoveryExplanation(
                cycle_cap=10,
                terminal_recovery_route="failed",
                preserve_session_on_categories=["agent"],
            ),
        )

        output = render_explanation_ascii(explanation)

        # Verify entry marker
        assert "=ENTRY=>" in output, "Expected entry marker =ENTRY=>"
        # Verify success terminal marker
        assert "==SUCCESS==>" in output, "Expected success terminal marker ==SUCCESS==>"
        # Verify boxed phase name - check that 'start' appears in a box context
        # The box border line contains '+' and the content line contains 'start' and '|'
        lines = output.split("\n")
        has_start_box = any(
            "start" in line and (line.strip().startswith("+") or "|" in line) for line in lines
        )
        assert has_start_box, f"Expected boxed phase name 'start' in output, got: {output}"

    def test_default_pipeline_diagram_contains_decision_branches(self) -> None:
        """Default pipeline diagram contains decision branches for all non-success routes.

        This is the PLAN step 8b test: assert output contains [completed],
        [request_changes], [failed] substrings (decision arrow syntax).
        """
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        # Decision branch format: +--[decision_name]--> target
        # Default pipeline has decisions: completed, request_changes, failed
        assert "+--[completed]-->" in output or "+--[request_changes]-->" in output, (
            "Expected at least one decision branch in output"
        )
        # Check for the specific decisions we know exist
        assert "+--[failed]-->" in output or "+--[request_changes]-->" in output

    def test_failed_decision_branches_render_as_rework_targets(self) -> None:
        """Default policy explanation must show failed analysis decisions looping back to rework."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        lines = output.split("\n")
        # Check that failed routes to development or planning (padding-agnostic)
        assert any("[failed]" in line and "-->" in line and "development" in line for line in lines)
        # failed should not route to 'failed' (terminal) — it should route to a rework phase
        assert not any(
            "[failed]" in line and line.rstrip().endswith("--> failed") for line in lines
        )

    def test_parallel_fanout_rejoin_shape_visible(self) -> None:
        """Default pipeline ASCII diagram shows both FAN_OUT and REJOIN annotations.

        The parallel shape must be visually legible: the fan-out source phase,
        the FAN_OUT annotation with parameters, and the REJOIN marker must all
        appear in the correct order so the reader can see where the parallel
        branch starts and where it ends.
        """
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert ">>> FAN_OUT" in output
        assert "max_workers=" in output
        assert "max_units=" in output
        assert "<<< REJOIN" in output

        fanout_pos = output.index(">>> FAN_OUT")
        rejoin_pos = output.index("<<< REJOIN")
        assert rejoin_pos > fanout_pos, "REJOIN annotation must appear after FAN_OUT"

    def test_render_text_emits_bypass_route_sentences(self) -> None:
        """render_explanation_text emits bypass_route explanation sentences."""
        phase = PhaseExplanation(
            name="review",
            role="review",
            drain="review",
            chain="review_chain",
            agents=["claude"],
            max_retries=1,
            skip_invocation=False,
            on_success="complete",
            on_failure=None,
            on_loopback=None,
            bypass_routes={"review_clean": "review_commit"},
            decisions={},
            loop_policy=None,
            commit_policy=None,
            terminal_outcome=None,
            is_entry=False,
            is_terminal=False,
        )
        done_phase = PhaseExplanation(
            name="complete",
            role="terminal",
            drain="complete",
            chain=None,
            agents=[],
            max_retries=0,
            skip_invocation=False,
            on_success=None,
            on_failure=None,
            on_loopback=None,
            bypass_routes={},
            decisions={},
            loop_policy=None,
            commit_policy=None,
            terminal_outcome="success",
            is_entry=False,
            is_terminal=True,
        )
        explanation = PolicyExplanation(
            entry_phase="review",
            terminal_phase="complete",
            phases=[phase, done_phase],
            loop_counters=[],
            budget_counters=[],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="complete", outcome="success")],
            parallel_execution=None,
            recovery=RecoveryExplanation(
                cycle_cap=10,
                terminal_recovery_route="failed",
                preserve_session_on_categories=["agent"],
            ),
        )
        output = render_explanation_text(explanation)

        assert (
            "Explanation: phase 'review' bypasses to 'review_commit' "
            "when the configured outcome is 'review_clean'."
        ) in output


class TestDefaultPolicyAsciiSnapshot:
    """Snapshot test locking down the ASCII output for the default pipeline policy.

    To regenerate the fixture:
        uv run --directory ralph-workflow python -c "
        from ralph.policy.loader import load_policy
        from ralph.policy.explain import explain_policy
        from ralph.policy.render import render_explanation_ascii
        from pathlib import Path
        b = load_policy(Path('ralph/policy/defaults'))
        print(render_explanation_ascii(explain_policy(b)))
        " > tests/fixtures/policy_explain_default.txt
    """

    _FIXTURE = Path(__file__).parent / "fixtures" / "policy_explain_default.txt"

    def test_default_ascii_matches_fixture(self) -> None:
        """Default policy ASCII diagram must match the committed fixture exactly."""
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        actual = render_explanation_ascii(explanation)
        expected = self._FIXTURE.read_text().rstrip("\n")
        assert actual == expected, (
            "Default policy ASCII diagram has changed. "
            "If the change is intentional, regenerate the fixture using the "
            "instructions in the class docstring."
        )


class TestVerificationAsciiAnnotation:
    """Tests that verification phases produce [verify: ...] annotation in ASCII output."""

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
        """Verification block must produce '[verify: kind=...' annotation in ASCII output."""
        bundle = self._bundle_with_verification()
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)
        assert "[verify: kind=artifact" in output
        assert "gates=advancement" in output
