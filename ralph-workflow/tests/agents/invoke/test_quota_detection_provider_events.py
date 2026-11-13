from __future__ import annotations

import json
import os
import pty
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    AttemptEnded,
    ProcessLineReader,
    ProcessReaderCtx,
    PtyLineReader,
    QuotaExhaustedError,
    is_provider_quota_output_line,
    raise_if_quota_exhausted,
)
from ralph.agents.invoke._quota_detection import (
    is_quota_kill_evidence,
    provider_quota_line_evidence,
    provider_quota_log_detail_matches,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    CompletionObserved,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_WORDS = "Your account has credits exhausted for this period."

_TOOL_AND_REASONING_LINES = (
    json.dumps({"error": None, "content": _QUOTA_WORDS}),
    '{"error":null,"content":"' + _QUOTA_WORDS + '"}',
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "read_multiple_files",
                "status": "completed",
                "result": {"content": [{"type": "text", "text": _QUOTA_WORDS}]},
            },
        }
    ),
    json.dumps(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "content": "RESOURCE_EXHAUSTED (code 429)"}]
            },
        }
    ),
    json.dumps({"type": "tool_result", "error": "quota exceeded in fixture", "output": "x"}),
    json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": _QUOTA_WORDS}}),
    json.dumps(
        {"type": "tool_use", "name": "bash", "input": {"command": "echo rate limit reached"}}
    ),
)

_PROVIDER_ERROR_LINES = (
    json.dumps({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"}),
    json.dumps({"error": {"message": "Individual quota reached"}}),
    json.dumps({"type": "result", "error": "rate limit reached"}),
    json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "error", "message": "You exceeded your current quota"},
        }
    ),
    "RESOURCE_EXHAUSTED (code 429)",
    "RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted",
)


@pytest.mark.parametrize("line", _TOOL_AND_REASONING_LINES)
def test_tool_and_reasoning_content_is_never_a_provider_quota_line(line: str) -> None:
    assert not is_provider_quota_output_line(line)


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_provider_error_events_and_plain_provider_failures_are_quota_lines(line: str) -> None:
    assert is_provider_quota_output_line(line)


@pytest.mark.parametrize("line", _TOOL_AND_REASONING_LINES)
def test_completion_time_check_ignores_tool_and_reasoning_content(line: str) -> None:
    raise_if_quota_exhausted("codex", "", [line], include_output=True)


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_completion_time_check_still_catches_provider_errors(line: str) -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted("codex", "", [line], include_output=True)


class _FiniteStdout:
    def __init__(self, lines: tuple[str, ...]) -> None:
        self._lines = iter(lines)

    def __iter__(self) -> _FiniteStdout:
        return self

    def __next__(self) -> str:
        return next(self._lines)


class _Handle:
    stdin = None
    pid: int | None = None
    returncode: int | None = None

    def __init__(self, stdout: object, stderr: object = None) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.terminate_calls: list[float] = []

    def poll(self) -> int | None:
        return 0

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        self.terminate_calls.append(grace_period_s)


def _reader(tmp_path: Path, handle: _Handle) -> ProcessLineReader:
    return ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
            workspace_path=tmp_path,
        ),
        FakeClock(start=0.0),
    )


def test_incident_3_healthy_session_quoting_quota_words_is_not_killed(tmp_path: Path) -> None:
    handle = _Handle(_FiniteStdout(tuple(line + "\n" for line in _TOOL_AND_REASONING_LINES)))
    reader = _reader(tmp_path, handle)

    emitted = list(reader.read_lines())

    assert len(emitted) == len(_TOOL_AND_REASONING_LINES)
    assert handle.terminate_calls == []


def test_provider_error_event_on_stdout_terminates_the_session(tmp_path: Path) -> None:
    handle = _Handle(_FiniteStdout((_PROVIDER_ERROR_LINES[0] + "\n",)))
    reader = _reader(tmp_path, handle)

    with pytest.raises(QuotaExhaustedError):
        list(reader.read_lines())

    assert handle.terminate_calls == [0.5]


def test_non_json_stderr_quota_text_still_terminates_the_session(tmp_path: Path) -> None:
    handle = _Handle(_FiniteStdout(()), stderr=iter(("RESOURCE_EXHAUSTED (code 429)\n",)))
    reader = _reader(tmp_path, handle)

    with pytest.raises(QuotaExhaustedError):
        list(reader.read_lines())


_TOOL_QUOTA_ERROR_LINES = (
    json.dumps({"type": "function_call", "name": "bash", "error": "RESOURCE_EXHAUSTED (code 429)"}),
    json.dumps(
        {
            "type": "mcp_tool_call",
            "tool": "read_multiple_files",
            "error": "RESOURCE_EXHAUSTED (code 429)",
        }
    ),
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "function_call",
                "name": "bash",
                "error": "RESOURCE_EXHAUSTED (code 429)",
            },
        }
    ),
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "read_multiple_files",
                "error": "RESOURCE_EXHAUSTED (code 429)",
            },
        }
    ),
)

_PROVIDER_SUBSCRIPTION_STDERR_LINES = (
    "RESOURCE_EXHAUSTED (code 429)",
    "You've hit your session limit",
    "RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted",
)


@pytest.mark.parametrize("line", _TOOL_QUOTA_ERROR_LINES)
def test_tool_call_quota_error_line_is_never_kill_evidence(line: str) -> None:
    assert not is_quota_kill_evidence(line)


@pytest.mark.parametrize("line", _TOOL_QUOTA_ERROR_LINES)
@pytest.mark.parametrize("include_output", [True, False])
def test_stderr_tool_call_quota_text_never_raises(line: str, include_output: bool) -> None:
    raise_if_quota_exhausted("codex", line, None, include_output=include_output)


@pytest.mark.parametrize("line", _TOOL_QUOTA_ERROR_LINES)
@pytest.mark.parametrize("include_output", [True, False])
def test_parsed_output_tool_call_quota_text_never_raises(line: str, include_output: bool) -> None:
    raise_if_quota_exhausted("codex", "", [line], include_output=include_output)


@pytest.mark.parametrize("line", _PROVIDER_SUBSCRIPTION_STDERR_LINES)
def test_provider_subscription_stderr_still_raises(line: str) -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted("codex", line, None, include_output=False)


_STRUCTURED_NON_PROVIDER_ENVELOPE_LINES = (
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "aggregated_output": _QUOTA_WORDS,
                "exit_code": 0,
            },
        }
    ),
    json.dumps({"error": None, "content": _QUOTA_WORDS}),
)


@pytest.mark.parametrize("line", _STRUCTURED_NON_PROVIDER_ENVELOPE_LINES)
def test_structured_non_provider_envelope_is_never_kill_evidence(line: str) -> None:
    assert not is_quota_kill_evidence(line)


@pytest.mark.parametrize("line", _STRUCTURED_NON_PROVIDER_ENVELOPE_LINES)
@pytest.mark.parametrize("include_output", [True, False])
def test_structured_non_provider_envelope_never_raises(line: str, include_output: bool) -> None:
    raise_if_quota_exhausted("codex", line, None, include_output=include_output)
    raise_if_quota_exhausted("codex", "", [line], include_output=include_output)


_MALFORMED_PROVIDER_STDERR_LINES = (
    '{"type": "error_message", "message": "quota exceeded: resource exhausted"}',
    '{"error": "RESOURCE_EXHAUSTED (code 429)',
    '["RESOURCE_EXHAUSTED (code 429)"]',
)


@pytest.mark.parametrize("line", _MALFORMED_PROVIDER_STDERR_LINES)
def test_malformed_provider_stderr_still_raises(line: str) -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted("codex", line, None, include_output=False)


_USER_MESSAGE_EVENT_LINES = (
    json.dumps(
        {"type": "message_start", "message": {"role": "user", "content": "credits exhausted"}}
    ),
    json.dumps(
        {"type": "message_end", "message": {"role": "user", "content": "credits exhausted"}}
    ),
)


@pytest.mark.parametrize("line", _USER_MESSAGE_EVENT_LINES)
@pytest.mark.parametrize("include_output", [True, False])
def test_user_message_event_never_raises_on_stderr_or_parsed_output(
    line: str, include_output: bool
) -> None:
    raise_if_quota_exhausted("codex", line, None, include_output=include_output)
    raise_if_quota_exhausted("codex", "", [line], include_output=include_output)


def test_include_output_gates_bare_phrase_but_not_explicit_provider_failure() -> None:
    raise_if_quota_exhausted("codex", "", ["quota exceeded in agent echo"], include_output=False)
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted("codex", "", ["quota exceeded in agent echo"], include_output=True)
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted(
            "codex",
            "",
            ["RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted"],
            include_output=False,
        )


def test_tool_call_line_on_stderr_never_terminates_the_session(tmp_path: Path) -> None:
    handle = _Handle(
        _FiniteStdout(()),
        stderr=iter(line + "\n" for line in _TOOL_QUOTA_ERROR_LINES),
    )
    reader = _reader(tmp_path, handle)

    emitted = list(reader.read_lines())

    assert emitted == []
    assert handle.terminate_calls == []


_TOOL_RESULT_QUOTA_LINES = (
    json.dumps({"type": "tool_result", "error": "RESOURCE_EXHAUSTED (code 429)", "output": "x"}),
    json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "tool_result", "content": _QUOTA_WORDS},
        }
    ),
)


@pytest.mark.parametrize("line", _TOOL_RESULT_QUOTA_LINES)
def test_tool_result_channel_line_with_quota_words_never_terminates_stdout(
    tmp_path: Path, line: str
) -> None:
    handle = _Handle(_FiniteStdout((line + "\n",)))
    reader = _reader(tmp_path, handle)

    emitted = list(reader.read_lines())

    assert emitted == [line + "\n"]
    assert handle.terminate_calls == []


@pytest.mark.parametrize("line", _TOOL_RESULT_QUOTA_LINES)
def test_tool_result_channel_line_with_quota_words_never_terminates_stderr(
    tmp_path: Path, line: str
) -> None:
    handle = _Handle(_FiniteStdout(()), stderr=iter((line + "\n",)))
    reader = _reader(tmp_path, handle)

    emitted = list(reader.read_lines())

    assert emitted == []
    assert handle.terminate_calls == []


_PROVIDER_LOG_TAILS = (
    "RESOURCE_EXHAUSTED (code 429)",
    " You've hit your session limit\n",
    "INFO startup\nRetriableError: [resource_exhausted] Error; quota or rate limit is exhausted\n",
)


@pytest.mark.parametrize("detail", _PROVIDER_LOG_TAILS)
def test_provider_channel_log_detail_still_matches(detail: str) -> None:
    assert provider_quota_log_detail_matches(detail)


_NON_PROVIDER_LOG_TAILS = (
    json.dumps({"type": "tool_result", "error": "RESOURCE_EXHAUSTED (code 429)", "output": "x"}),
    json.dumps({"type": "tool_result", "content": _QUOTA_WORDS}),
    json.dumps({"type": "reasoning", "text": _QUOTA_WORDS}),
    json.dumps({"type": "subagent", "text": _QUOTA_WORDS}),
    json.dumps({"type": "thinking", "text": _QUOTA_WORDS}),
    "some unrelated plain text mentioning quota exceeded mid-sentence",
)


@pytest.mark.parametrize("detail", _NON_PROVIDER_LOG_TAILS)
def test_non_provider_channel_log_detail_never_matches(detail: str) -> None:
    assert not provider_quota_log_detail_matches(detail)


_NON_PROVIDER_PAYLOAD_LINES = (
    _TOOL_AND_REASONING_LINES
    + _TOOL_QUOTA_ERROR_LINES
    + _TOOL_RESULT_QUOTA_LINES
    + _STRUCTURED_NON_PROVIDER_ENVELOPE_LINES
)


@pytest.mark.parametrize("line", _NON_PROVIDER_PAYLOAD_LINES)
def test_typed_channel_evidence_never_comes_from_non_provider_payloads(line: str) -> None:
    assert provider_quota_line_evidence(line) is None


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_typed_channel_evidence_accepts_provider_error_events_and_plain_failures(
    line: str,
) -> None:
    evidence = provider_quota_line_evidence(line)
    assert evidence is not None
    assert evidence.text == line


def test_typed_channel_evidence_falls_back_to_channel_of_for_unparsed_lines() -> None:
    assert provider_quota_line_evidence("RESOURCE_EXHAUSTED (code 429)") is not None
    assert (
        provider_quota_line_evidence(
            "some unrelated plain text mentioning quota exceeded mid-sentence"
        )
        is None
    )


def test_json_provider_error_event_on_stderr_terminates_the_session(tmp_path: Path) -> None:
    handle = _Handle(_FiniteStdout(()), stderr=iter((_PROVIDER_ERROR_LINES[0] + "\n",)))
    reader = _reader(tmp_path, handle)

    with pytest.raises(QuotaExhaustedError):
        list(reader.read_lines())

    assert handle.terminate_calls == [0.5]


def _noop_teardown(pid: int, *, issuer: object) -> None:
    del pid, issuer


class _PtyHandle:
    pid: None = None

    def __init__(self, master_fd: int, *, polls_before_death: int = 5) -> None:
        self.master_fd = master_fd
        self._polls_before_death = polls_before_death
        self._polls = 0
        self.terminate_issuers: list[ProcessTerminationIssuer] = []

    def poll(self) -> int | None:
        self._polls += 1
        return -15 if self._polls >= self._polls_before_death else None

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s
        issuer = kwargs.get("issuer")
        assert isinstance(issuer, ProcessTerminationIssuer)
        self.terminate_issuers.append(issuer)

    def record_terminal_reason(self, reason: str) -> None:
        del reason


def _pty_reader(
    tmp_path: Path,
    handle: _PtyHandle,
    supervisor: AttemptSupervisor,
) -> PtyLineReader:
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
    )
    return PtyLineReader(
        handle, "claude", ctx, FakeClock(start=0.0), extras=None, supervisor=supervisor
    )


def test_pty_provider_error_event_terminates_the_session(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _PtyHandle(master_fd, polls_before_death=1_000_000)
    supervisor = AttemptSupervisor(handle)
    reader = _pty_reader(tmp_path, handle, supervisor)
    try:
        os.write(slave_fd, (_PROVIDER_ERROR_LINES[0] + "\n").encode("utf-8"))

        with pytest.raises(QuotaExhaustedError) as caught:
            list(reader.read_lines())

        assert isinstance(caught.value, AttemptEnded)
        assert handle.terminate_issuers == [ProcessTerminationIssuer.QUOTA_DETECTOR]
        assert supervisor.recorder.termination().issuer is TerminationIssuer.QUOTA_DETECTOR
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_pty_tool_and_narrative_payloads_do_not_terminate_and_the_session_continues(
    tmp_path: Path,
) -> None:
    payload_lines = [line + "\n" for line in _TOOL_RESULT_QUOTA_LINES]
    payload_lines.append(_TOOL_AND_REASONING_LINES[2] + "\n")
    provider_line = _PROVIDER_ERROR_LINES[0] + "\n"
    master_fd, slave_fd = pty.openpty()
    handle = _PtyHandle(master_fd, polls_before_death=1_000_000)
    supervisor = AttemptSupervisor(handle)
    reader = _pty_reader(tmp_path, handle, supervisor)
    try:
        for line in (*payload_lines, provider_line):
            os.write(slave_fd, line.encode("utf-8"))

        emitted: list[str] = []
        with pytest.raises(QuotaExhaustedError):
            emitted.extend(delivered for delivered in reader.read_lines())

        assert emitted == payload_lines
        assert handle.terminate_issuers == [ProcessTerminationIssuer.QUOTA_DETECTOR]
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_pty_tool_result_payload_after_completion_never_terminates(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _PtyHandle(master_fd)
    supervisor = AttemptSupervisor(handle)
    reader = _pty_reader(tmp_path, handle, supervisor)
    try:
        outcome = supervisor.act(CompletionObserved(), teardown=_noop_teardown)
        assert outcome is not None
        facts_before = supervisor.recorder.termination()
        terminate_before = list(handle.terminate_issuers)

        line = _TOOL_RESULT_QUOTA_LINES[0] + "\n"
        os.write(slave_fd, line.encode("utf-8"))

        assert list(reader.read_lines()) == [line]
        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == terminate_before
    finally:
        os.close(slave_fd)
        os.close(master_fd)
