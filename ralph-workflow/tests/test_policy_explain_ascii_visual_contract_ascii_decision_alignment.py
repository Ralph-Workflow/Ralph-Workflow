"""Lock-down tests for render_explanation_ascii() ASCII marker contracts.

These tests verify that the required ASCII visual markers are present in the
rendered output for three structural patterns:
  - spine (=ENTRY=>, ==SUCCESS==>, ==FAILURE==>, Legend: block)
  - loopback (<<==[loopback]==, [LOOPBACK: ...], >> RE-ENTRY at ...)
  - fan-out / rejoin (>>> FAN_OUT ..., <<< REJOIN)

Tests use synthetic PolicyExplanation / PolicyBundle instances so they do not
depend on the bundled default pipeline.toml.
"""

from __future__ import annotations

from ralph.policy.explain import (
    LoopPolicyExplanation,
    PhaseExplanation,
    PolicyExplanation,
    RecoveryExplanation,
    TerminalOutcomeExplanation,
)
from ralph.policy.render import render_explanation_ascii

_ASCII_MAX = 127


def _make_phase(
    name: str,
    *,
    role: str = "execution",
    on_success: str | None = None,
    on_failure: str | None = None,
    on_loopback: str | None = None,
    decisions: dict[str, str] | None = None,
    loop_policy: LoopPolicyExplanation | None = None,
    terminal_outcome: str | None = None,
    is_entry: bool = False,
    is_terminal: bool = False,
) -> PhaseExplanation:
    return PhaseExplanation(
        name=name,
        role=role,
        drain=name,
        chain=f"{name}_chain" if not is_terminal else None,
        agents=["claude"] if not is_terminal else [],
        max_retries=1,
        skip_invocation=False,
        on_success=on_success,
        on_failure=on_failure,
        on_loopback=on_loopback,
        bypass_routes={},
        decisions=decisions or {},
        loop_policy=loop_policy,
        commit_policy=None,
        terminal_outcome=terminal_outcome,
        is_entry=is_entry,
        is_terminal=is_terminal,
    )


def _minimal_recovery() -> RecoveryExplanation:
    return RecoveryExplanation(
        cycle_cap=10,
        terminal_recovery_route="done",
        preserve_session_on_categories=["agent"],
    )


class TestAsciiDecisionAlignment:
    """Decision branches are padded/aligned to the longest decision label."""

    def _decision_explanation(self) -> PolicyExplanation:
        analysis = _make_phase(
            "analysis",
            role="analysis",
            on_success="done",
            on_loopback="rework",
            decisions={"completed": "done", "request_changes": "rework", "failed": "rework"},
            is_entry=True,
        )
        rework = _make_phase("rework", on_success="analysis")
        done = _make_phase("done", role="terminal", terminal_outcome="success", is_terminal=True)
        return PolicyExplanation(
            entry_phase="analysis",
            terminal_phase="done",
            phases=[analysis, rework, done],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            recovery=_minimal_recovery(),
        )

    def test_decisions_are_aligned(self) -> None:
        """All decision arrows end with --> at the same column."""
        output = render_explanation_ascii(self._decision_explanation())
        lines = output.split("\n")
        # Diagram decision lines are indented with 4 spaces; legend lines have 2-space indent
        decision_lines = [
            line
            for line in lines
            if line.startswith("    +--[") and "-->" in line and "workflow_fallback" not in line
        ]
        assert len(decision_lines) >= 2
        # Find the position of '-->' in each line
        arrow_positions = [line.index("-->") for line in decision_lines]
        assert len(set(arrow_positions)) == 1, (
            f"Decision arrows should be aligned but got positions: {arrow_positions}\n"
            + "\n".join(decision_lines)
        )
