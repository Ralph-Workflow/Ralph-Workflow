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


def _make_phase(  # noqa: PLR0913
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


class TestAsciiWorkflowFallbackContract:
    """Workflow fallback arrow: +--[workflow_fallback]--> target."""

    def _fallback_explanation(self) -> PolicyExplanation:
        from ralph.policy.explain import PhaseExplanation, PolicyExplanation  # noqa: PLC0415

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
            terminal_outcomes=[
                TerminalOutcomeExplanation(phase="done", outcome="success")
            ],
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
        from ralph.policy.explain import PhaseExplanation, PolicyExplanation  # noqa: PLC0415

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
            line for line in lines
            if line.startswith("    +--[") and "-->" in line
            and "workflow_fallback" not in line
        ]
        assert len(decision_lines) >= 2  # noqa: PLR2004
        # Find the position of '-->' in each line
        arrow_positions = [line.index("-->") for line in decision_lines]
        assert len(set(arrow_positions)) == 1, (
            f"Decision arrows should be aligned but got positions: {arrow_positions}\n"
            + "\n".join(decision_lines)
        )
