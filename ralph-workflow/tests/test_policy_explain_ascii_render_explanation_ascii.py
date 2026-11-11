from __future__ import annotations

from pathlib import Path

from ralph.policy.explain import (
    PhaseExplanation,
    PolicyExplanation,
    RecoveryExplanation,
    TerminalOutcomeExplanation,
    explain_policy,
)
from ralph.policy.loader import load_policy
from ralph.policy.render import render_explanation_ascii, render_explanation_text


def _get_default_policy_path() -> Path:
    candidates = [
        Path(__file__).parent.parent / "ralph" / "policy" / "defaults",
        Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("Default policy directory not found in any candidate location")


class TestRenderExplanationAscii:
    def test_renders_entry_marker(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "=ENTRY=>" in output

    def test_renders_success_terminal_marker(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "==SUCCESS==>" in output

    def test_renders_decision_branches(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "+--[failed]" in output
        assert "+--[request_changes]" in output

    def test_renders_loopback_arrow(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "<<==[loopback]==" in output
        assert "returns to '" in output
        assert "<--[loopback]--" not in output
        assert "loop back to development" not in output

    def test_renders_fanout_annotation(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert ">>> FAN_OUT" in output
        assert "development" in output

    def test_non_terminal_phases_do_not_show_failure_marker(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

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

            if (in_development_commit or in_review_commit) and line.strip():
                phase_name = "development_commit" if in_development_commit else "review_commit"
                assert "==FAILURE==>" not in line, (
                    f"Non-terminal phase {phase_name} should not have "
                    f"==FAILURE==> marker, but found: {line}"
                )
                in_development_commit = False
                in_review_commit = False

    def test_contains_boxed_phase_lines(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)
        lines = output.split("\n")

        boxed_lines = [
            line for line in lines if "|" in line and ("planning" in line or "development" in line)
        ]
        assert len(boxed_lines) > 0, "Expected at least one boxed phase line"

    def test_renders_minimal_two_phase_pipeline_diagram(self) -> None:
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

        assert "=ENTRY=>" in output, "Expected entry marker =ENTRY=>"
        assert "==SUCCESS==>" in output, "Expected success terminal marker ==SUCCESS==>"
        lines = output.split("\n")
        has_start_box = any(
            "start" in line and (line.strip().startswith("+") or "|" in line) for line in lines
        )
        assert has_start_box, f"Expected boxed phase name 'start' in output, got: {output}"

    def test_default_pipeline_diagram_contains_decision_branches(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert "+--[completed]-->" in output or "+--[request_changes]-->" in output, (
            "Expected at least one decision branch in output"
        )
        assert "+--[failed]-->" in output or "+--[request_changes]-->" in output

    def test_failed_decision_branches_render_configured_target(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        lines = output.split("\n")
        assert any("[failed]" in line and "-->" in line and "planning" in line for line in lines)
        assert (
            bundle.pipeline.phases["development_analysis"].decisions["failed"].target
            == "development"
        )
        assert "failed_terminal" in output
        assert not any(
            "[failed]" in line and "-->" in line and "failed_terminal" in line for line in lines
        ), (
            "Analysis [failed] branches must route to explicit rework, "
            "not directly to failed_terminal"
        )

    def test_parallel_fanout_rejoin_shape_visible(self) -> None:
        policy_dir = _get_default_policy_path()
        bundle = load_policy(policy_dir)
        explanation = explain_policy(bundle)
        output = render_explanation_ascii(explanation)

        assert ">>> FAN_OUT" in output
        assert "max_workers=" in output
        assert "waves=queued" in output
        assert "max_units=" not in output
        text = render_explanation_text(explanation)
        assert "waves" in text
        assert "Paths" in text
        assert "Max work units:" not in text
        assert "Require allowed_directories:" not in text
        assert "<<< REJOIN" in output

        fanout_pos = output.index(">>> FAN_OUT")
        rejoin_pos = output.index("<<< REJOIN")
        assert rejoin_pos > fanout_pos, "REJOIN annotation must appear after FAN_OUT"

    def test_render_text_emits_bypass_route_sentences(self) -> None:
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
