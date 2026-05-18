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


class TestAsciiWorkflowFallbackContract:
    """Workflow fallback arrow: +--[workflow_fallback]--> target."""

    def _fallback_explanation(self) -> PolicyExplanation:

        work = PhaseExplanation(
            name="work",
            role="execution",
            drain="work",
            chain="work_chain",
            agents=["claude"],
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
            workflow_fallback=("fallback_phase", "agents exhausted"),
        )
        done = _make_phase(
            "done",
            role="terminal",
            terminal_outcome="success",
            is_terminal=True,
        )
        fallback_phase = _make_phase(
            "fallback_phase",
            on_success="done",
        )
        return PolicyExplanation(
            entry_phase="work",
            terminal_phase="done",
            phases=[work, done, fallback_phase],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            recovery=_minimal_recovery(),
        )

    def test_workflow_fallback_arrow_present(self) -> None:
        """Phase with workflow_fallback renders +--[workflow_fallback]--> target."""
        output = render_explanation_ascii(self._fallback_explanation())
        assert "+--[workflow_fallback]-->" in output

    def test_workflow_fallback_shows_target(self) -> None:
        """Workflow fallback arrow includes the target phase name."""
        output = render_explanation_ascii(self._fallback_explanation())
        assert "+--[workflow_fallback]--> fallback_phase" in output

    def test_workflow_fallback_shows_note(self) -> None:
        """Workflow fallback arrow includes the optional note when set."""
        output = render_explanation_ascii(self._fallback_explanation())
        assert "(agents exhausted)" in output

    def test_workflow_fallback_without_note(self) -> None:
        """Workflow fallback arrow without note renders correctly (no parentheses)."""

        work = PhaseExplanation(
            name="work",
            role="execution",
            drain="work",
            chain="work_chain",
            agents=["claude"],
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
            workflow_fallback=("fallback_phase", None),
        )
        done = _make_phase("done", role="terminal", terminal_outcome="success", is_terminal=True)
        exp = PolicyExplanation(
            entry_phase="work",
            terminal_phase="done",
            phases=[work, done],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            recovery=_minimal_recovery(),
        )
        output = render_explanation_ascii(exp)
        assert "+--[workflow_fallback]--> fallback_phase" in output
        assert "(" not in output.split("+--[workflow_fallback]-->")[1].split("\n")[0]
