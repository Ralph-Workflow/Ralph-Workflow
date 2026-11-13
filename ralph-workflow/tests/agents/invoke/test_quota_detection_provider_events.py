from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    ProcessLineReader,
    ProcessReaderCtx,
    QuotaExhaustedError,
    is_provider_quota_output_line,
    raise_if_quota_exhausted,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

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
