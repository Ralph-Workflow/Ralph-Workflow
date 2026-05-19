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


class TestAsciiSpineContract:
    """Spine markers: =ENTRY=>, ==SUCCESS==>, ==FAILURE==>, Legend: block, ASCII-only."""

    def _linear_explanation(self) -> PolicyExplanation:
        work = _make_phase("work", on_success="done", is_entry=True)
        fail_exit = _make_phase(
            "fail_exit",
            role="terminal",
            terminal_outcome="failure",
            is_terminal=True,
        )
        done = _make_phase(
            "done",
            role="terminal",
            terminal_outcome="success",
            is_terminal=True,
        )
        return PolicyExplanation(
            entry_phase="work",
            terminal_phase="done",
            phases=[work, fail_exit, done],
            terminal_outcomes=[
                TerminalOutcomeExplanation(phase="done", outcome="success"),
                TerminalOutcomeExplanation(phase="fail_exit", outcome="failure"),
            ],
            recovery=_minimal_recovery(),
        )

    def test_entry_marker_present(self) -> None:
        """=ENTRY=> appears above the entry phase box."""
        output = render_explanation_ascii(self._linear_explanation())
        assert "=ENTRY=>" in output

    def test_success_terminal_marker_present(self) -> None:
        """==SUCCESS==> appears for the success terminal phase."""
        output = render_explanation_ascii(self._linear_explanation())
        assert "==SUCCESS==>" in output

    def test_failure_terminal_marker_present(self) -> None:
        """==FAILURE==> appears for the failure terminal phase."""
        output = render_explanation_ascii(self._linear_explanation())
        assert "==FAILURE==>" in output

    def test_legend_block_present(self) -> None:
        """Legend: block appears at the end of the output."""
        output = render_explanation_ascii(self._linear_explanation())
        assert "Legend:" in output

    def test_legend_contains_all_required_entries(self) -> None:
        """Legend: block documents all required glyph types."""
        output = render_explanation_ascii(self._linear_explanation())
        assert "=ENTRY=>" in output
        assert "==SUCCESS==>" in output
        assert "==FAILURE==>" in output
        assert "+--[decision]-->" in output
        assert "<<==[loopback]==" in output
        assert ">>> FAN_OUT" in output
        assert "<<< REJOIN" in output

    def test_ascii_only_no_unicode(self) -> None:
        """Output must not contain any Unicode box-drawing or non-ASCII characters."""
        output = render_explanation_ascii(self._linear_explanation())
        for i, char in enumerate(output):
            assert ord(char) <= _ASCII_MAX, (
                f"Non-ASCII character U+{ord(char):04X} at position {i} in output. "
                "render_explanation_ascii() must produce pure ASCII only."
            )
