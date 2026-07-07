"""Integration tests: production tool-use observations reach RepetitionTracker.mark_tool_call.

The companion tests in ``tests/test_repetition_tracker.py`` cover the
tracker logic in isolation (mark_tool_call fingerprints, tripped()
returns True after N identical tool calls, etc.).  These tests pin
the production REACHABILITY: a real parsed tool-use line observed by
the line readers MUST feed the tool-call circuit breaker so the
watchdog can fire ``REPEATED_IDENTICAL_TOOL_CALL``.

The analysis-feedback contract (AC-06 + how_to_fix item #3):

* ``_process_reader._record_line_activity`` and
  ``_pty_line_reader._handle_queued_line`` MUST extract a
  ``(tool_name, tool_args)`` pair from a TOOL_USE
  ``AgentActivitySignal`` and call
  ``watchdog.record_tool_call_activity(tool_name, tool_args)``.
* The extraction MUST tolerate the canonical envelope shapes:
  - ``{"type": "tool_use", "name": "...", "input": {...}}``
  - ``{"type": "stream_event", "event": {"type": "content_block_start",
    "content_block": {"type": "tool_use", "name": "...", "input": {...}}}}``
    (Claude content_block_start wrapped in stream_event)
* The extraction MUST be best-effort: non-JSON or unknown envelopes
  are silently skipped so the breaker sees only stable
  (name, args) fingerprints.

Without this layer the tool-call repetition dimension is
unreachable in real runs and ``REPEATED_IDENTICAL_TOOL_CALL`` can
never fire from production traffic -- exactly the gap the
analysis feedback surfaced.
"""

from __future__ import annotations

import json
import os
from types import MethodType, SimpleNamespace

import pytest

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._idle_stream_timeout_error import _IdleStreamTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke._process_reader import (
    _extract_tool_call_from_activity_signal,
    _ProcessLineReader,
)
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

# ---------------------------------------------------------------------------
# (1) Helper-layer: _extract_tool_call_from_activity_signal accepts the
#     canonical envelope shapes.
# ---------------------------------------------------------------------------


def test_extract_tool_call_from_plain_tool_use_envelope() -> None:
    """A canonical ``{"type": "tool_use", "name": ..., "input": ...}``
    envelope yields the expected ``(tool_name, tool_args)`` pair.
    """
    line = json.dumps(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


def test_extract_tool_call_from_claude_content_block_start_envelope() -> None:
    """A Claude ``{"type": "stream_event", "event":
    {"type": "content_block_start", "content_block":
    {"type": "tool_use", "name": "Read", "input": {...}}}}`` envelope
    unwraps to the canonical ``(tool_name, tool_args)`` pair.
    """
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/tmp/example.txt"},
                },
            },
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Read"
    assert tool_args == {"file_path": "/tmp/example.txt"}


def test_extract_tool_call_from_arguments_field() -> None:
    """Some transports use ``arguments`` instead of ``input``; the
    helper accepts either.
    """
    line = json.dumps(
        {
            "type": "tool_use",
            "name": "Write",
            "arguments": {"content": "hello"},
        }
    )
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Write"
    assert tool_args == {"content": "hello"}


def test_extract_tool_call_returns_none_for_non_tool_use_envelope() -> None:
    """A non-tool-use envelope (e.g. ``{"type": "text", ...}``) MUST
    return ``None`` so the breaker is NOT fed for irrelevant lines.
    """
    line = json.dumps({"type": "text", "text": "hello"})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is None


def test_extract_tool_call_returns_none_for_invalid_json() -> None:
    """Invalid JSON MUST return ``None`` (no exception)."""
    assert _extract_tool_call_from_activity_signal("not json {{{") is None
    assert _extract_tool_call_from_activity_signal("") is None


def test_extract_tool_call_returns_unknown_for_missing_name() -> None:
    """A tool-use envelope without a ``name`` field MUST fall back to
    ``"unknown"`` so the fingerprint is always well-formed.
    """
    line = json.dumps({"type": "tool_use", "input": {"foo": "bar"}})
    result = _extract_tool_call_from_activity_signal(line)
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "unknown"
    assert tool_args == {"foo": "bar"}


def test_extract_tool_call_from_claude_prefixed_plain_text() -> None:
    """A plain-text ``claude tool: <name>`` line classified as TOOL_USE
    by ClaudeExecutionStrategy MUST yield a stable fingerprint.

    Plain-text tool-use lines carry no arguments, so the helper
    returns an empty ``args`` dict.  Without this path the
    repetition breaker cannot fire on repeated identical plain-text
    tool invocations.
    """
    result = _extract_tool_call_from_activity_signal("claude tool: Bash")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Bash"
    assert tool_args == {}


def test_extract_tool_call_from_plain_tool_prefix() -> None:
    """A plain-text ``[plain] tool: <name>`` line MUST also yield a
    stable fingerprint when it reaches the helper.

    This mirrors the GenericParser convention so the tool-call
    circuit breaker stays reachable for any transport that surfaces
    plain-text tool-use markers.
    """
    result = _extract_tool_call_from_activity_signal("[plain] tool: Read")
    assert result is not None
    tool_name, tool_args = result
    assert tool_name == "Read"
    assert tool_args == {}


def test_extract_tool_call_returns_none_for_plain_text_without_tool_marker() -> None:
    """A non-tool plain-text line MUST NOT produce a fingerprint."""
    assert _extract_tool_call_from_activity_signal("random log line") is None


# ---------------------------------------------------------------------------
# (2) Production seam: a parsed tool-use line observed by the line
#     readers MUST feed the watchdog's tool-call circuit breaker.
# ---------------------------------------------------------------------------


class _RecordingWatchdog:
    """Fake watchdog that records every ``record_tool_call_activity``
    call so the test can assert the production line reader invokes
    the breaker with the expected (tool_name, tool_args) pair.
    """

    def __init__(self) -> None:
        self.tool_call_observations: list[tuple[str, object]] = []
        self.activity_records: list[str] = []
        self.lifecycle_records: int = 0
        self.error_records: list[str] = []
        self._verdict = WatchdogVerdict.CONTINUE

    def record_tool_call_activity(self, tool_name: str, tool_args: object) -> None:
        self.tool_call_observations.append((tool_name, tool_args))

    def record_activity(self) -> None:
        self.activity_records.append("activity")

    def record_tool_use_activity(self) -> None:
        self.activity_records.append("tool_use")

    def record_lifecycle_activity(self) -> None:
        self.lifecycle_records += 1

    def record_error_activity(self, message: str) -> None:
        self.error_records.append(message)

    def record_tool_result_activity(self) -> None:
        self.activity_records.append("tool_result")

    def evaluate(self, *, classify_quiet: object) -> object:
        del classify_quiet
        return self._verdict


class _ToolUseStrategy:
    """Strategy whose ``classify_activity_line`` always returns a
    TOOL_USE ``AgentActivitySignal`` so the production line reader
    routes the line through the tool-call breaker.
    """

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


class _JunkToolUseStrategy:
    """Tool-use strategy with invalid JSON raw payload.

    Used to verify the production line reader silently skips
    unrecognised envelopes rather than crashing or feeding the
    breaker with garbage fingerprints.
    """

    def __init__(self) -> None:
        self._raw = "not-json-{{{"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        del line
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=self._raw)

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, liveness_probe: object) -> None:
        del handle, liveness_probe


def _build_pty_reader_with_strategy(strategy: object) -> PtyLineReader:
    """Construct a PtyLineReader with the given strategy.

    The reader's construction signature is broad (it takes
    ``_AgentRunCtx``); we build a minimal SimpleNamespace that the
    reader touches at the ``_handle_queued_line`` call site.  The
    master_fd is a real ``/dev/null`` fd because the reader
    constructor calls ``os.dup(master_fd)`` for the input writer;
    a sentinel ``-1`` triggers an OSError on construction.
    """
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = SimpleNamespace(
        master_fd=master_fd,
        poll=lambda: None,
        terminate=lambda grace_period_s=None: None,
    )
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=strategy,
        liveness_probe=None,
        waiting_listener=None,
    )
    try:
        reader = PtyLineReader(handle, "claude", ctx, FakeClock(start=0.0), extras=None)
    finally:
        # The reader duped the fd; close the original.
        os.close(master_fd)
    return reader


def test_pty_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
    """A parsed TOOL_USE line on the PTY reader MUST reach
    ``watchdog.record_tool_call_activity`` with the canonical
    ``(tool_name, tool_args)`` pair extracted from the envelope.
    """
    raw = json.dumps(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
    )
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    watchdog = _RecordingWatchdog()

    list(reader._handle_queued_line(raw + "\n", watchdog))

    assert len(watchdog.tool_call_observations) == 1, (
        f"Expected exactly one tool-call observation; got"
        f" {watchdog.tool_call_observations}"
    )
    tool_name, tool_args = watchdog.tool_call_observations[0]
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


def test_pty_line_reader_routes_repeated_tool_use_to_trip_breaker() -> None:
    """The PTY reader MUST route repeated identical tool calls
    through the breaker so an identical-tool-call wedge is detected.

    The test exercises the production ``_handle_queued_line`` path
    with a recording watchdog that tracks the fingerprint -- the
    production ``RepetitionTracker.tripped()`` will fire on
    identical (tool_name, tool_args) pairs observed >= window_count
    times.  Without this path the production breaker dimension is
    unreachable in real runs.
    """
    raw = json.dumps(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
    )
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        list(reader._handle_queued_line(raw + "\n", watchdog))

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 tool-call observations; got"
        f" {watchdog.tool_call_observations}"
    )
    fingerprints = {
        (name, json.dumps(args, sort_keys=True))
        for name, args in watchdog.tool_call_observations
    }
    assert len(fingerprints) == 1, (
        f"Expected identical fingerprints for repeated identical tool calls;"
        f" got {fingerprints}"
    )


def test_pty_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
    """PTY tool-use activity must not clear the identical-tool-call breaker."""
    raw = json.dumps(
        {"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}}
    )
    reader = _build_pty_reader_with_strategy(_ToolUseStrategy(raw))
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(2):
        list(reader._handle_queued_line(raw + "\n", watchdog))
        clock.advance(1.0)

    with pytest.raises(_IdleStreamTimeoutError) as exc_info:
        list(reader._handle_queued_line(raw + "\n", watchdog))

    assert exc_info.value.reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL
    assert "exec tool call args={\"cmd\": \"long silent command\"}" in str(
        exc_info.value
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


def test_pty_line_reader_silently_skips_unrecognised_envelopes() -> None:
    """A non-JSON tool-use envelope MUST NOT crash the line reader
    AND MUST NOT feed the breaker with garbage fingerprints.

    The helper returns ``None`` for unknown envelopes; the
    production line reader only calls
    ``watchdog.record_tool_call_activity`` when the helper returns
    a valid (tool_name, tool_args) pair.
    """
    reader = _build_pty_reader_with_strategy(_JunkToolUseStrategy())
    watchdog = _RecordingWatchdog()

    list(reader._handle_queued_line("not-json-{{{\n", watchdog))

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )


# ---------------------------------------------------------------------------
# (3) Subprocess reader seam: the _ProcessLineReader._record_line_activity
#     path MUST also route TOOL_USE activity to the breaker.
# ---------------------------------------------------------------------------


def test_process_line_reader_routes_tool_use_to_record_tool_call_activity() -> None:
    """A parsed TOOL_USE line on the subprocess reader MUST reach
    ``watchdog.record_tool_call_activity`` with the canonical
    ``(tool_name, tool_args)`` pair extracted from the envelope.

    This test exercises the production ``_record_line_activity``
    method (lines 588-613 in ``_process_reader.py``) without
    spinning up a real subprocess.  We bind the unbound method to a
    minimal reader-like object so the only production code in the
    call path is the activity classification and routing.
    """
    raw = json.dumps(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
    )
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    # Bind the production method to the minimal reader-like object.
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    bound_method(watchdog, raw)

    assert len(watchdog.tool_call_observations) == 1, (
        f"Expected exactly one tool-call observation from subprocess reader;"
        f" got {watchdog.tool_call_observations}"
    )
    tool_name, tool_args = watchdog.tool_call_observations[0]
    assert tool_name == "Bash"
    assert tool_args == {"command": "ls"}


def test_process_line_reader_routes_repeated_tool_use_to_breaker() -> None:
    """The subprocess reader MUST route repeated identical tool calls
    through the breaker so an identical-tool-call wedge is detected.

    Exercises the production ``_record_line_activity`` path with a
    recording watchdog.  Identical (tool_name, tool_args) pairs
    observed multiple times must be recorded so the production
    ``RepetitionTracker.tripped()`` can fire.
    """
    raw = json.dumps(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
    )
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        bound_method(watchdog, raw)

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 tool-call observations from subprocess reader;"
        f" got {watchdog.tool_call_observations}"
    )
    fingerprints = {
        (name, json.dumps(args, sort_keys=True))
        for name, args in watchdog.tool_call_observations
    }
    assert len(fingerprints) == 1, (
        f"Expected identical fingerprints for repeated identical tool calls;"
        f" got {fingerprints}"
    )


def test_process_line_reader_repeated_tool_use_trips_real_watchdog() -> None:
    """Production reader activity must not clear the identical-tool-call breaker."""
    raw = json.dumps(
        {"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}}
    )
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(3):
        bound_method(watchdog, raw)
        clock.advance(1.0)

    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)

    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL


def test_repeated_tool_call_timeout_diagnostic_identifies_command() -> None:
    """Repeated-tool timeout messages name the repeated tool and command preview."""
    raw = json.dumps(
        {"type": "tool_use", "name": "exec", "input": {"cmd": "long silent command"}}
    )
    strategy = _ToolUseStrategy(raw)
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            repeated_error_consecutive_threshold=3,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )

    for _ in range(3):
        bound_method(watchdog, raw)
        clock.advance(1.0)

    diagnostic = watchdog.repetition_diagnostic()

    assert diagnostic["tool_name"] == "exec"
    assert diagnostic["tool_args_preview"] == '{"cmd": "long silent command"}'


def test_repeated_tool_call_timeout_messages_identify_command() -> None:
    """Timeout exceptions include the repeated tool args preview."""
    diagnostic = {
        "tool_name": "exec",
        "tool_args_preview": '{"cmd": "long silent command"}',
    }

    stream_error = _IdleStreamTimeoutError(
        300.0,
        WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
        diagnostic=diagnostic,
    )
    invocation_error = AgentInactivityTimeoutError(
        "codex",
        300.0,
        [],
        InactivityTimeoutOpts(
            reason=WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL,
            diagnostic=diagnostic,
        ),
    )

    assert "exec tool call args={\"cmd\": \"long silent command\"}" in str(stream_error)
    assert "exec tool call args={\"cmd\": \"long silent command\"}" in str(
        invocation_error
    )


def test_process_line_reader_routes_plain_text_tool_use_to_breaker() -> None:
    """A plain-text ``claude tool: <name>`` TOOL_USE line on the
    subprocess reader MUST feed the tool-call circuit breaker.

    This is the analysis-feedback reachability gap: the helper only
    understood JSON envelopes, so repeated identical plain-text
    tool-use markers (classified as TOOL_USE elsewhere) were silently
    ignored and ``REPEATED_IDENTICAL_TOOL_CALL`` could not fire.
    """
    strategy = _ToolUseStrategy("claude tool: Bash")
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    for _ in range(3):
        bound_method(watchdog, "claude tool: Bash")

    assert len(watchdog.tool_call_observations) == 3, (
        f"Expected 3 plain-text tool-call observations; got"
        f" {watchdog.tool_call_observations}"
    )
    assert all(name == "Bash" and args == {} for name, args in watchdog.tool_call_observations), (
        f"Expected (Bash, {{}}) fingerprints; got {watchdog.tool_call_observations}"
    )


def test_process_line_reader_silently_skips_unrecognised_tool_envelopes() -> None:
    """A non-JSON tool-use envelope on the subprocess reader MUST NOT
    crash the line reader AND MUST NOT feed the breaker with garbage
    fingerprints.
    """
    strategy = _JunkToolUseStrategy()
    reader_like = SimpleNamespace(
        _strategy=strategy,
        _last_activity_kind="",
        _last_activity_meaningful=[False],
    )
    bound_method = MethodType(_ProcessLineReader._record_line_activity, reader_like)
    watchdog = _RecordingWatchdog()

    bound_method(watchdog, "not-json-{{{\n")

    assert watchdog.tool_call_observations == [], (
        f"Expected NO tool-call observations for invalid JSON; got"
        f" {watchdog.tool_call_observations}"
    )
