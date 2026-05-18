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


class TestAsciiLoopbackContract:
    """Loopback markers: <<==[loopback]==, [LOOPBACK: counter=..., max=...], >> RE-ENTRY."""

    def _loopback_explanation(self) -> PolicyExplanation:
        loop_policy = LoopPolicyExplanation(
            max_iterations=3,
            iteration_state_field="my_counter",
            loopback_review_outcome=None,
        )
        analysis = _make_phase(
            "analysis",
            role="analysis",
            on_success="done",
            on_loopback="analysis",
            decisions={"completed": "done", "needs_work": "rework"},
            loop_policy=loop_policy,
            is_entry=True,
        )
        rework = _make_phase("rework", on_success="analysis")
        done = _make_phase(
            "done",
            role="terminal",
            terminal_outcome="success",
            is_terminal=True,
        )
        return PolicyExplanation(
            entry_phase="analysis",
            terminal_phase="done",
            phases=[analysis, rework, done],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            recovery=_minimal_recovery(),
        )

    def test_loopback_arrow_present(self) -> None:
        """<<==[loopback]== appears when on_loopback differs from on_success."""
        output = render_explanation_ascii(self._loopback_explanation())
        assert "<<==[loopback]==" in output

    def test_loopback_shows_target(self) -> None:
        """Loopback annotation shows the return target."""
        output = render_explanation_ascii(self._loopback_explanation())
        assert "returns to 'analysis'" in output

    def test_loopback_counter_annotation_present(self) -> None:
        """[LOOPBACK: counter=..., max=...] annotation appears when loop_policy is set."""
        output = render_explanation_ascii(self._loopback_explanation())
        assert "[LOOPBACK: counter=my_counter, max=3]" in output

    def test_reentry_banner_present(self) -> None:
        """>> RE-ENTRY at target appears below the loopback annotation."""
        output = render_explanation_ascii(self._loopback_explanation())
        assert ">> RE-ENTRY at analysis" in output

    def test_loopback_is_left_pointing(self) -> None:
        """Loopback arrow uses left-pointing syntax, not forward arrows."""
        output = render_explanation_ascii(self._loopback_explanation())
        # Must use left-pointing arrows
        assert "<<==[loopback]==" in output
        # Must NOT use the old right-pointing formats
        assert "<--[loopback]--" not in output
