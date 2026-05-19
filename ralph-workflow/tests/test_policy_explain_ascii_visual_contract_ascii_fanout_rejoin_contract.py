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
    ParallelExplanation,
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


class TestAsciiFanoutRejoinContract:
    """Fan-out / rejoin markers: >>> FAN_OUT (max_workers=N, max_units=M) >>>, <<< REJOIN."""

    def _fanout_explanation(self) -> PolicyExplanation:
        work = _make_phase("work", on_success="done", is_entry=True)
        done = _make_phase(
            "done",
            role="terminal",
            terminal_outcome="success",
            is_terminal=True,
        )
        parallel = ParallelExplanation(
            phase="work",
            max_parallel_workers=4,
            max_work_units=20,
            require_allowed_directories=True,
        )
        return PolicyExplanation(
            entry_phase="work",
            terminal_phase="done",
            phases=[work, done],
            terminal_outcomes=[TerminalOutcomeExplanation(phase="done", outcome="success")],
            parallel_execution=parallel,
            recovery=_minimal_recovery(),
        )

    def test_fanout_annotation_present(self) -> None:
        """>>> FAN_OUT (max_workers=N, max_units=M) >>> appears for the parallel phase."""
        output = render_explanation_ascii(self._fanout_explanation())
        assert ">>> FAN_OUT" in output

    def test_fanout_shows_worker_count(self) -> None:
        """Fan-out annotation includes max_workers count."""
        output = render_explanation_ascii(self._fanout_explanation())
        assert "max_workers=4" in output

    def test_fanout_shows_work_unit_count(self) -> None:
        """Fan-out annotation includes max_units count."""
        output = render_explanation_ascii(self._fanout_explanation())
        assert "max_units=20" in output

    def test_rejoin_marker_present(self) -> None:
        """<<< REJOIN appears after the fan-out phase box."""
        output = render_explanation_ascii(self._fanout_explanation())
        assert "<<< REJOIN" in output

    def test_fanout_before_rejoin_ordering(self) -> None:
        """>>> FAN_OUT annotation appears before <<< REJOIN in the output."""
        output = render_explanation_ascii(self._fanout_explanation())
        fanout_pos = output.index(">>> FAN_OUT")
        rejoin_pos = output.index("<<< REJOIN")
        assert fanout_pos < rejoin_pos, (
            ">>> FAN_OUT must appear before <<< REJOIN in the ASCII output"
        )

    def test_rejoin_has_divider_line(self) -> None:
        """<<< REJOIN is preceded by a divider line +================+ for visual bracketing."""
        output = render_explanation_ascii(self._fanout_explanation())
        assert "+================+" in output
        # Divider must appear before the REJOIN marker
        divider_pos = output.index("+================+")
        rejoin_pos = output.index("<<< REJOIN")
        assert divider_pos < rejoin_pos, (
            "+================+ divider must appear before <<< REJOIN marker"
        )
