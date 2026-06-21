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
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.execution_state._factory import _make_agy_strategy
from ralph.agents.execution_state._helpers import _classify_generic_child_signal
from ralph.config.enums import AgentTransport
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

        signal = _classify_generic_child_signal('{"type":"child_progress","msg":"thinking"}')
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
        """Codex-style JSON with a child-scoped event name
        ``{"event":"child_progress","data":"thinking"}`` is classified
        as CHILD_PROGRESS (the explicit child-scoped event name
        carries the child scope).
        """
        signal = _classify_generic_child_signal('{"event":"child_progress","data":"thinking"}')
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_does_not_classify_bare_progress_event(self) -> None:
        """A bare ``event="progress"`` JSON line is NOT classified as child activity.

        The analysis-feedback fix: bare provider-level ``progress``
        events are generic provider wire frames (they appear in
        parent-level stdout from Codex / Generic / Agy / Nanocoder
        transports) and do NOT prove a subagent is active. The
        generic classifier recognises ONLY events with explicit
        child- or subagent-scope (``child_progress``,
        ``subagent_progress``, or any ``child_`` / ``subagent_``
        prefix). A bare ``{"event":"progress"}`` line is treated as
        ordinary parent-level output.
        """
        signal = _classify_generic_child_signal('{"event":"progress","data":"thinking"}')
        assert signal is None, (
            f"bare progress event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_tool_call_event(self) -> None:
        """A bare ``type="tool_call"`` JSON line is NOT classified as child activity.

        The analysis-feedback fix: bare ``{"type":"tool_call"}`` frames
        are Gemini / Generic provider wire events (see
        ``tests/test_parsers_1.py::test_gemini_parser_tool_call_emitted_separately``
        and ``tests/test_parsers_1.py::test_generic_*``), NOT child
        activity. Treating bare ``tool_call`` as child activity was
        the false-positive deferral that masked the short
        NO_OUTPUT_AT_START kill -- the watchdog would refresh
        ``record_subagent_work`` for any ``tool_call`` emitted by the
        parent agent and never fire the short kill.
        """
        signal = _classify_generic_child_signal(
            '{"type":"tool_call","name":"bash","args":{"cmd":"ls"}}'
        )
        assert signal is None, (
            f"bare tool_call event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_heartbeat_event(self) -> None:
        """A bare ``type="heartbeat"`` JSON line is NOT classified as child activity.

        The analysis-feedback fix: bare ``{"type":"heartbeat"}`` frames
        are Generic / Codex provider wire events (see
        ``tests/test_parsers_1.py::test_generic_heartbeat_type_is_suppressed``
        and ``tests/test_parsers_1.py::test_codex_heartbeat_is_suppressed``),
        NOT child activity. Treating bare ``heartbeat`` as child
        activity was the false-positive deferral that masked the
        short NO_OUTPUT_AT_START kill.
        """
        signal = _classify_generic_child_signal('{"type":"heartbeat","ts":1234567890}')
        assert signal is None, (
            f"bare heartbeat event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_bare_alive_event(self) -> None:
        """A bare ``event="alive"`` JSON line is NOT classified as child activity.

        The analysis-feedback fix: bare ``{"event":"alive"}`` frames
        are generic provider wire events, NOT child activity.
        """
        signal = _classify_generic_child_signal('{"event":"alive","ts":1234567890}')
        assert signal is None, (
            f"bare alive event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_does_not_classify_task_progress_event(self) -> None:
        """A bare ``event="task_progress"`` JSON line is NOT classified as child activity.

        The analysis-feedback fix: bare ``task_progress`` is a generic
        provider event, NOT child activity.
        """
        signal = _classify_generic_child_signal('{"event":"task_progress","data":"thinking"}')
        assert signal is None, (
            f"bare task_progress event MUST NOT classify as child activity"
            f" per analysis-feedback fix, got {signal!r}"
        )

    def test_classify_generic_child_signal_matches_subagent_scoped_prefix(self) -> None:
        """A ``type="subagent_progress_anything"`` JSON line IS
        classified as CHILD_PROGRESS because the ``subagent_``
        prefix is the explicit child-scope signal.
        """
        signal = _classify_generic_child_signal(
            '{"type":"subagent_progress_phase1","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_child_scoped_prefix(self) -> None:
        """A ``type="child_progress_anything"`` JSON line IS
        classified as CHILD_PROGRESS because the ``child_``
        prefix is the explicit child-scope signal.
        """
        signal = _classify_generic_child_signal(
            '{"type":"child_progress_phase1","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_matches_subagent_heartbeat_prefix(self) -> None:
        """A ``type="subagent_heartbeat_extra"`` JSON line IS
        classified as CHILD_PROGRESS because the ``subagent_``
        prefix is the explicit child-scope signal (the
        ``child_`` / ``subagent_`` prefix family defaults to
        CHILD_PROGRESS unless the value is in the explicit
        heartbeat set).
        """
        signal = _classify_generic_child_signal(
            '{"type":"subagent_heartbeat_extra","data":"thinking"}'
        )
        assert signal is not None
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_classify_generic_child_signal_returns_none_for_terminal_event(self) -> None:
        """A ``type="child_complete"`` JSON line is a terminal event
        and is NOT classified (terminal signals do not invoke the
        sink, same contract as OpenCode).
        """
        signal = _classify_generic_child_signal('{"type":"child_complete","child_id":"abc"}')
        assert signal is None

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
# Analysis-feedback regression tests.
#
# The pre-fix classifier had two correctness gaps that are pinned here:
#
# 1. _classify_generic_child_signal_from_json treated ANY
#    ``type``/``event`` value starting with ``child_`` / ``subagent_``
#    as CHILD_PROGRESS, except for a tiny terminal allowlist. So
#    ``{"type":"subagent_failed"}`` and
#    ``{"event":"subagent_cancelled"}`` were forwarded by
#    ``BaseExecutionStrategy.observe_line`` into the subagent sink and
#    refreshed ``record_subagent_work`` for genuine terminal/error
#    events, masking idle / stuck conditions.
#
# 2. _classify_generic_child_signal_from_text used bare substring
#    markers ``subagent: `` and ``child: ``, so ordinary prose like
#    ``User wrote a YAML snippet: subagent: worker`` or
#    ``Documentation says child: value`` classified as
#    CHILD_PROGRESS, again refreshing ``record_subagent_work`` for
#    parent-level output and masking idle / stuck conditions.
#
# These tests pin the no-false-positive contract.
# ---------------------------------------------------------------------------


class TestClassifyGenericChildSignalAnalysisFeedbackRegressions:
    """Analysis-feedback regression tests for terminal events and prose lines."""

    def test_subagent_failed_json_returns_none(self) -> None:
        """``type="subagent_failed"`` is a terminal event and must return None.

        Pre-fix, the generic classifier treated every ``subagent_`` /
        ``child_``-prefixed event as CHILD_PROGRESS except for a tiny
        allowlist, so ``subagent_failed`` was incorrectly forwarded
        into the subagent sink and refreshed ``record_subagent_work``
        for a failure event.
        """

        signal = _classify_generic_child_signal('{"type":"subagent_failed","child_id":"abc"}')
        assert signal is None, (
            f"subagent_failed MUST classify to None (terminal event, not progress), got {signal!r}"
        )

    def test_subagent_cancelled_json_returns_none(self) -> None:
        """``event="subagent_cancelled"`` is a terminal event and must return None.

        Pre-fix, the generic classifier treated every ``subagent_`` /
        ``child_``-prefixed event as CHILD_PROGRESS except for a tiny
        allowlist, so ``subagent_cancelled`` was incorrectly forwarded
        into the subagent sink.
        """

        signal = _classify_generic_child_signal('{"event":"subagent_cancelled","child_id":"abc"}')
        assert signal is None, (
            f"subagent_cancelled MUST classify to None (terminal event,"
            f" not progress), got {signal!r}"
        )

    def test_subagent_terminal_json_returns_none(self) -> None:
        """``type="subagent_terminal"`` is a terminal event and must return None."""

        signal = _classify_generic_child_signal('{"type":"subagent_terminal","child_id":"abc"}')
        assert signal is None, (
            f"subagent_terminal MUST classify to None (terminal event), got {signal!r}"
        )

    def test_child_failed_json_returns_none(self) -> None:
        """``event="child_failed"`` is a terminal event and must return None."""

        signal = _classify_generic_child_signal('{"event":"child_failed","child_id":"abc"}')
        assert signal is None, (
            f"child_failed MUST classify to None (terminal event), got {signal!r}"
        )

    def test_child_cancelled_json_returns_none(self) -> None:
        """``type="child_cancelled"`` is a terminal event and must return None."""

        signal = _classify_generic_child_signal('{"type":"child_cancelled","child_id":"abc"}')
        assert signal is None, (
            f"child_cancelled MUST classify to None (terminal event), got {signal!r}"
        )

    def test_yaml_prose_line_returns_none(self) -> None:
        """Ordinary prose containing ``subagent: `` as a substring must NOT classify.

        Pre-fix, the text classifier matched the bare substring
        ``subagent: `` anywhere in the line, so a YAML schema
        description like ``User wrote a YAML snippet: subagent: worker``
        classified as CHILD_PROGRESS and refreshed
        ``record_subagent_work`` for unrelated parent-level output.
        """

        signal = _classify_generic_child_signal("User wrote a YAML snippet: subagent: worker")
        assert signal is None, (
            f"YAML prose line MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_documentation_prose_line_returns_none(self) -> None:
        """Ordinary prose containing ``child: `` as a substring must NOT classify.

        Pre-fix, the text classifier matched the bare substring
        ``child: `` anywhere in the line, so a documentation sentence
        like ``Documentation says child: value`` classified as
        CHILD_PROGRESS.
        """

        signal = _classify_generic_child_signal("Documentation says child: value")
        assert signal is None, (
            f"Documentation prose line MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_subagent_colon_after_phrase_returns_none(self) -> None:
        """Phrase-like prose like ``about the subagent: overview`` must NOT classify."""

        signal = _classify_generic_child_signal("Let me talk about the subagent: overview of work")
        assert signal is None, (
            f"Phrase-like prose MUST classify to None (no leading"
            f" child-status marker), got {signal!r}"
        )

    def test_explicit_child_marker_still_classifies(self) -> None:
        """The explicit child marker ``[child] do something`` STILL classifies.

        Pin: the marker tightening for prose lines must NOT regress
        the intended classification of explicit child-status lines.
        """

        signal = _classify_generic_child_signal("[child] do something")
        assert signal is not None, "Explicit child marker MUST still classify as CHILD_PROGRESS"
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_subagent_progress_json_still_classifies(self) -> None:
        """The intended ``subagent_progress`` JSON event STILL classifies.

        Pin: the terminal-event tightening for JSON must NOT regress
        the intended classification of an explicit child-scoped
        progress event.
        """

        signal = _classify_generic_child_signal('{"type":"subagent_progress","data":"thinking"}')
        assert signal is not None, (
            "Explicit subagent_progress JSON MUST still classify as CHILD_PROGRESS"
        )
        assert signal.kind == AgentActivityKind.CHILD_PROGRESS

    def test_subagent_heartbeat_json_still_classifies(self) -> None:
        """The intended ``subagent_heartbeat`` JSON event STILL classifies
        as CHILD_HEARTBEAT.
        """

        signal = _classify_generic_child_signal('{"type":"subagent_heartbeat","ts":1234567890}')
        assert signal is not None, "Explicit subagent_heartbeat JSON MUST still classify"
        assert signal.kind == AgentActivityKind.CHILD_HEARTBEAT


class TestObserveLineDoesNotInvokeSinkForTerminalOrProse:
    """End-to-end ``BaseExecutionStrategy.observe_line`` regressions for
    terminal events and prose lines.

    These tests verify the wiring in ``BaseExecutionStrategy.observe_line``
    does NOT invoke the subagent sink for the prose / terminal cases
    that would otherwise refresh ``record_subagent_work`` for parent-level
    output.
    """

    def test_observe_line_does_not_invoke_sink_for_subagent_failed(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A terminal ``subagent_failed`` JSON line must NOT invoke the sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"subagent_failed","child_id":"abc"}')

        assert len(subagent_sink_spy) == 0, (
            f"terminal subagent_failed MUST NOT invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_subagent_cancelled(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A terminal ``subagent_cancelled`` JSON line must NOT invoke the sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"subagent_cancelled","child_id":"abc"}')

        assert len(subagent_sink_spy) == 0, (
            f"terminal subagent_cancelled MUST NOT invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_yaml_prose(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A YAML-prose line containing ``subagent: `` must NOT invoke the sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line("User wrote a YAML snippet: subagent: worker")

        assert len(subagent_sink_spy) == 0, (
            f"YAML prose MUST NOT invoke the sink (no leading"
            f" child-status marker), got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_does_not_invoke_sink_for_documentation_prose(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A documentation-prose line containing ``child: `` must NOT invoke the sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line("Documentation says child: value")

        assert len(subagent_sink_spy) == 0, (
            f"Documentation prose MUST NOT invoke the sink (no leading"
            f" child-status marker), got {len(subagent_sink_spy)} invocations"
        )

    def test_observe_line_still_invokes_sink_for_explicit_child_marker(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """Pin: explicit child marker MUST still invoke the sink."""

        strategy = BaseExecutionStrategy()
        strategy.observe_line("[child] do something")

        assert len(subagent_sink_spy) == 1, (
            f"Explicit child marker MUST still invoke the sink,"
            f" got {len(subagent_sink_spy)} invocations"
        )


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

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A bare ``{"type":"heartbeat"}`` line does NOT invoke the subagent sink.

        The analysis-feedback fix: bare ``heartbeat`` is a generic
        provider wire event, NOT child activity. Pre-fix, the base
        observe_line forwarded bare heartbeats into the subagent sink
        via ``_classify_generic_child_signal`` because
        ``_GENERIC_CHILD_HEARTBEAT_KIND`` contained ``heartbeat``.
        """
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"heartbeat","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare heartbeat line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_tool_call(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A bare ``{"type":"tool_call"}`` line does NOT invoke the subagent sink.

        The analysis-feedback fix: bare ``tool_call`` is a generic
        provider wire event (Gemini / Generic), NOT child activity.
        """
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"type":"tool_call","name":"bash","args":{"cmd":"ls"}}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare tool_call line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_alive(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A bare ``{"event":"alive"}`` line does NOT invoke the subagent sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"alive","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare alive line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_progress(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A bare ``{"event":"progress"}`` line does NOT invoke the subagent sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare progress line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
        )

    def test_base_execution_strategy_observe_line_does_not_invoke_sink_on_bare_task_progress(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """A bare ``{"event":"task_progress"}`` line does NOT invoke the subagent sink."""
        strategy = BaseExecutionStrategy()
        strategy.observe_line('{"event":"task_progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"expected zero sink invocations for bare task_progress line"
            f" per analysis-feedback fix, got {len(subagent_sink_spy)}"
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

    PLAN AC-09 explicitly calls out Codex/Generic/Agy/Nanocoder as
    inheriting the same path automatically. This class adds direct
    inheritance + wiring assertions for every transport named in
    PLAN AC-09 so the cross-transport contract is observable.
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

    def test_generic_execution_strategy_does_not_override_observe_line(self) -> None:
        """GenericExecutionStrategy inherits BaseExecutionStrategy.observe_line.

        The Codex, Nanocoder, and Agy fallback strategies all delegate
        to ``GenericExecutionStrategy`` (or a subclass of it), so
        confirming ``GenericExecutionStrategy`` does NOT override
        ``observe_line`` is the single-source-of-truth check that
        pins PLAN AC-09's "Codex / Generic / Agy / Nanocoder all
        inherit this behavior automatically" contract.
        """
        assert "observe_line" not in GenericExecutionStrategy.__dict__, (
            "GenericExecutionStrategy MUST NOT override observe_line; the base"
            " implementation applies the cross-transport generic child-signal"
            " classifier automatically. Codex / Nanocoder / Agy-fallback"
            " all inherit from GenericExecutionStrategy."
        )

    def test_generic_execution_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """GenericExecutionStrategy.observe_line on a child-progress line
        invokes the sink exactly once (inherited from base).
        """
        strategy = GenericExecutionStrategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Generic strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_agy_strategy_inherits_base_observe_line(self) -> None:
        """The AgyExecutionStrategy (CompletionEnforcingStrategy + GenericExecutionStrategy)
        does NOT override ``observe_line`` so the base implementation applies.

        The Agy factory (``_make_agy_strategy``) creates a subclass that
        inherits from ``CompletionEnforcingStrategy`` and
        ``GenericExecutionStrategy``; neither parent overrides
        ``observe_line``, so the Agy strategy must use the base
        implementation too. We assert this by instantiating the
        factory and confirming the resulting class does NOT
        override ``observe_line``.
        """
        agy_instance = _make_agy_strategy()
        agy_cls = type(agy_instance)
        assert "observe_line" not in agy_cls.__dict__, (
            f"AgyExecutionStrategy ({agy_cls.__name__}) MUST NOT override"
            f" observe_line; the base implementation applies the cross-transport"
            f" generic child-signal classifier automatically."
        )
        # Confirm the inheritance chain is correct.
        assert issubclass(agy_cls, GenericExecutionStrategy), (
            f"AgyExecutionStrategy ({agy_cls.__name__}) MUST inherit from"
            f" GenericExecutionStrategy; got MRO: {agy_cls.__mro__}"
        )

    def test_agy_strategy_inherits_generic_child_signal_path(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """AgyExecutionStrategy.observe_line on a child-progress line
        invokes the sink exactly once (inherited from base).
        """
        strategy = _make_agy_strategy()
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Agy strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_returns_base_observer(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.CODEX)`` returns a
        ``GenericExecutionStrategy`` whose ``observe_line`` invokes the
        subagent sink exactly once for a child-progress line.

        The Codex transport does NOT have a dedicated strategy class
        in the codebase -- it delegates to ``GenericExecutionStrategy``
        via the catalog (``_DEFAULT_STRATEGIES`` table in
        ``ralph.agents.catalog``). This test pins the AC-09
        contract for the Codex transport end-to-end: the
        ``strategy_for_transport`` factory returns a strategy that
        feeds the watchdog's per-channel evidence surface.
        """
        strategy = strategy_for_transport(AgentTransport.CODEX)
        assert isinstance(strategy, GenericExecutionStrategy), (
            f"Codex transport must use GenericExecutionStrategy (the catalog"
            f" default), got {type(strategy).__name__}"
        )
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Codex strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_event_name(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.CODEX)`` does NOT
        classify bare Codex ``event=progress`` as a child signal.

        The analysis-feedback fix: bare ``{"event":"progress",...}``
        frames are generic provider wire events, NOT child activity.
        Treating them as child activity was a false-positive deferral
        that masked the short NO_OUTPUT_AT_START kill.
        """
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"event":"progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex event=progress MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_invokes_sink_on_child_scoped_event(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.CODEX)`` DOES classify
        a child-scoped ``event=child_progress`` as CHILD_PROGRESS so
        the watchdog sees Codex subagent activity.
        """
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"event":"child_progress","data":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"Codex strategy MUST invoke the sink for child-scoped"
            f" event=child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_heartbeat(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.CODEX)`` does NOT
        classify bare ``type=heartbeat`` as child activity.

        The analysis-feedback fix: bare ``{"type":"heartbeat"}`` is a
        generic provider wire event, NOT child activity.
        """
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"type":"heartbeat","ts":1234567890}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex type=heartbeat MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_codex_strategy_factory_does_not_invoke_sink_on_bare_tool_call(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.CODEX)`` does NOT
        classify bare ``type=tool_call`` as child activity.

        The analysis-feedback fix: bare ``{"type":"tool_call"}`` is a
        generic provider wire event (Gemini / Generic), NOT child
        activity.
        """
        strategy = strategy_for_transport(AgentTransport.CODEX)
        strategy.observe_line('{"type":"tool_call","name":"bash"}')

        assert len(subagent_sink_spy) == 0, (
            f"bare Codex type=tool_call MUST NOT invoke the sink"
            f" per analysis-feedback fix (no-false-positive deferral),"
            f" got {len(subagent_sink_spy)} invocations"
        )

    def test_nanocoder_strategy_factory_returns_base_observer(
        self, subagent_sink_spy: list[str]
    ) -> None:
        """``strategy_for_transport(AgentTransport.NANOCODER)`` returns a
        ``GenericExecutionStrategy`` whose ``observe_line`` invokes the
        subagent sink exactly once for a child-progress line.

        PLAN AC-09 explicitly names Nanocoder as one of the
        transports that inherits the new base behaviour. The
        Nanocoder transport falls through to ``GenericExecutionStrategy``
        via the catalog's transport-keyed strategy dispatch
        (``_STRATEGY_DISPATCH``).
        """
        strategy = strategy_for_transport(AgentTransport.NANOCODER)
        assert isinstance(strategy, GenericExecutionStrategy), (
            f"Nanocoder transport must use GenericExecutionStrategy (the catalog"
            f" default), got {type(strategy).__name__}"
        )
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Nanocoder strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
        )

    def test_agy_strategy_factory_returns_base_observer(self, subagent_sink_spy: list[str]) -> None:
        """``strategy_for_transport(AgentTransport.AGY)`` returns an
        AgyExecutionStrategy whose ``observe_line`` invokes the
        subagent sink exactly once for a child-progress line.
        """
        strategy = strategy_for_transport(AgentTransport.AGY)
        strategy.observe_line('{"type":"child_progress","msg":"thinking"}')

        assert len(subagent_sink_spy) == 1, (
            f"expected exactly one sink invocation for Agy strategy on"
            f" child_progress line, got {len(subagent_sink_spy)} invocations"
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
