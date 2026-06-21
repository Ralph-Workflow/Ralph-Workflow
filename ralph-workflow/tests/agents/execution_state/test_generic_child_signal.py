"""Tests for cross-transport generic child-signal classifier.

The watchdog's per-channel evidence surface stays fresh only when the
underlying execution strategy's ``observe_line`` invokes the
subagent activity sink on child-progress signals. The OpenCodeExecutionStrategy
already does this via the ``_classify_opencode_child_signal`` classifier
in ``_helpers.py``, but Claude/Codex/Generic/Agy/Nanocoder strategies
never override ``observe_line`` so they do NOT invoke the sink.

This module tests the new ``_classify_generic_child_signal`` classifier
(in ``_helpers.py``) and the ``BaseExecutionStrategy.observe_line`` override
that calls it. Together, these make every transport's ``observe_line``
honour the watchdog's subagent activity channel.

All tests use the ``set_subagent_sink`` / ``reset_subagent_sink`` contextvar
spy pattern from the OpenCodeExecutionStrategy so the sink invocation count
is observable without mocking the entire module.
"""

from __future__ import annotations

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import (
    BaseExecutionStrategy,
    ClaudeExecutionStrategy,
    ClaudeInteractiveExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.execution_state._helpers import _classify_generic_child_signal
from ralph.mcp.server._activity_sink import (
    reset_subagent_sink,
    set_subagent_sink,
)


@pytest.fixture
def subagent_sink_spy() -> list[str]:
    """Install a subagent sink that records every invocation line.

    Returns the list; the fixture's teardown resets the contextvar so
    tests do not leak the sink into each other.
    """
    recorded: list[str] = []

    def _record(line: str) -> None:
        recorded.append(line)

    token = set_subagent_sink(_record)
    try:
        yield recorded
    finally:
        reset_subagent_sink(token)


# ---------------------------------------------------------------------------
# Classifier unit tests
# ---------------------------------------------------------------------------


class TestClassifyGenericChildSignal:
    """Unit tests for ``_classify_generic_child_signal`` in ``_helpers.py``."""

    def test_classify_generic_child_signal_matches_opencode_json(self) -> None:
        """OpenCode-style JSON ``{"type":"child_progress","msg":"thinking"}``
        is classified as CHILD_PROGRESS.
        """

        signal = _classify_generic_child_signal(
            '{"type":"child_progress","msg":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_claude_prefixed(self) -> None:
        """Claude-style plain-text ``[child] do something`` is classified
        as CHILD_PROGRESS.
        """

        signal = _classify_generic_child_signal("[child] do something")
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_subagent_heartbeat(self) -> None:
        """Plain-text line containing 'subagent heartbeat' (case-insensitive)
        is classified as CHILD_HEARTBEAT.
        """

        signal = _classify_generic_child_signal("Subagent heartbeat at t=30s")
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_HEARTBEAT

    def test_classify_generic_child_signal_matches_codex_json_event(self) -> None:
        """Codex-style JSON ``{"event":"progress","data":"thinking"}``
        is classified as CHILD_PROGRESS.
        """

        signal = _classify_generic_child_signal(
            '{"event":"progress","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_returns_none_for_unrelated_line(self) -> None:
        """Regular stdout without markers returns None."""

        signal = _classify_generic_child_signal("hello world from the agent")
        assert signal is None

    def test_classify_generic_child_signal_returns_none_for_empty_line(self) -> None:
        """Empty / whitespace-only lines return None."""

        assert _classify_generic_child_signal("") is None
        assert _classify_generic_child_signal("   ") is None
        assert _classify_generic_child_signal("\n") is None


# ---------------------------------------------------------------------------
# BaseExecutionStrategy.observe_line wiring tests
# ---------------------------------------------------------------------------


class TestBaseExecutionStrategyObserveLineWiring:
    """BaseExecutionStrategy.observe_line must invoke the subagent sink on child signals."""

    def test_base_execution_strategy_observe_line_invokes_subagent_sink_on_child_signal(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A child-progress line routed through BaseExecutionStrategy.observe_line
        invokes the active subagent sink exactly once.
        """
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for child_progress line,"
            f" got {len(subagent_sink_spy)} invocations"
        )
        assert subagent_sink_spy[0] == '{"type":"child_progress","msg":"thinking"}'

    def test_base_execution_strategy_observe_line_does_not_invoke_subagent_sink_on_blank_line(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A blank line does NOT invoke the subagent sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line("   ")

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for blank line,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_base_execution_strategy_observe_line_invoke_sink_on_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A heartbeat line routed through BaseExecutionStrategy.observe_line
        invokes the active subagent sink once with the original line.
        """
        strategy = BaseExecutionStrategy()
        strategy.observe_line("Subagent heartbeat at t=42s")

        assert len(subagent_sink_spy) == 1
        assert subagent_sink_spy[0] == "Subagent heartbeat at t=42s"


# ---------------------------------------------------------------------------
# Inheritance tests: Claude/Codex/Generic/Agy/Nanocoder use the base
# observe_line so they automatically invoke the sink.
# ---------------------------------------------------------------------------


class TestStrategyInheritanceUsesBaseChildSignalPath:
    """ClaudeExecutionStrategy and ClaudeInteractiveExecutionStrategy do NOT
    override ``observe_line`` -- the new base behavior applies automatically.
    """

    def test_claude_execution_strategy_does_not_override_observe_line(self) -> None:
        """ClaudeExecutionStrategy inherits BaseExecutionStrategy.observe_line."""
        assert "observe_line" not in ClaudeExecutionStrategy.__dict__, (
            "ClaudeExecutionStrategy MUST NOT override observe_line; the base"
            " implementation applies the cross-transport generic child-signal"
            " classifier automatically."
        )

    def test_claude_interactive_execution_strategy_does_not_override_observe_line(
        self,
    ) -> None:
        """ClaudeInteractiveExecutionStrategy inherits BaseExecutionStrategy.observe_line."""
        assert "observe_line" not in ClaudeInteractiveExecutionStrategy.__dict__, (
            "ClaudeInteractiveExecutionStrategy MUST NOT override observe_line; the"
            " base implementation applies the cross-transport generic child-signal"
            " classifier automatically."
        )

    def test_claude_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """ClaudeExecutionStrategy.observe_line on a child-progress JSON line
        invokes the sink exactly once (inherited from base).
        """
        strategy = ClaudeExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Claude strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_claude_interactive_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """ClaudeInteractiveExecutionStrategy.observe_line on a child-progress
        JSON line invokes the sink exactly once (inherited from base).
        """
        strategy = ClaudeInteractiveExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for ClaudeInteractive strategy"
            f" on child_progress line, got {len(subagent_sink_spy)} invocations"
        )


# ---------------------------------------------------------------------------
# OpenCode strategy does NOT double-invoke the sink
# ---------------------------------------------------------------------------


class TestOpenCodeStrategyDoesNotDoubleInvokeSink:
    """OpenCodeExecutionStrategy overrides observe_line entirely so the base
    implementation is NOT also called. The sink is invoked exactly once per line.
    """

    def test_opencode_strategy_overrides_observe_line(self) -> None:
        """OpenCodeExecutionStrategy overrides observe_line (does not rely on base)."""
        assert "observe_line" in OpenCodeExecutionStrategy.__dict__, (
            "OpenCodeExecutionStrategy MUST override observe_line so the base"
            " generic classifier is NOT also called (would double-invoke the sink)."
        )

    def test_opencode_strategy_does_not_double_invoke_sink(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """OpenCodeExecutionStrategy.observe_line invokes the sink exactly once
        for a child_progress line, not twice.
        """
        strategy = OpenCodeExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for OpenCode strategy on"
            f" child_progress line (NOT two -- base + override),"
            f" got {len(subagent_sink_spy)} invocations"
        )
