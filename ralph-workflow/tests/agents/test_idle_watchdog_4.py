from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    ChannelName,
    IdleWatchdog,
    TimeoutPolicy,
    WorkspaceChangeKind,
)
from ralph.agents.invoke import CompletionCheckOptions, check_process_result
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._workspace_change_classifier import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeClassifier,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from pathlib import Path


IDLE_TIMEOUT = 0.1
DRAIN_WINDOW = 0.0
MAX_WAITING = 10.0
ACTIVITY_TTL = 30.0


def _make_watchdog(
    *,
    idle_timeout: float = IDLE_TIMEOUT,
    drain_window: float = DRAIN_WINDOW,
    max_waiting: float = MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
        suspect_waiting_on_child_seconds=suspect,
        max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
        activity_evidence_ttl_seconds=activity_ttl,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _default_classifier() -> WorkspaceChangeClassifier:
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


class _FakeHandle:
    def __init__(self, returncode: int, pid: int = 42) -> None:
        self.returncode = returncode
        self.pid = pid
        self.stderr = _FakeStderr("boom")


class _FakeStderr:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self, size: int | None = None) -> str:
        if size is None or size < 0:
            return self._text
        return self._text[:size]


class _CompletionEnforcingStrategy:
    def classify_exit(
        self,
        handle: object,
        signals: object,
        liveness_probe: object | None = None,
    ) -> AgentExecutionState:
        del handle, signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return True

    def supports_incomplete_exit_reprompt(self) -> bool:
        return False


def testcheck_process_result_nonzero_exit_calls_teardown_subtree(tmp_path: Path) -> None:
    handle = _FakeHandle(returncode=1, pid=1234)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=None,
        )

    mock_teardown.assert_called_once_with(1234, issuer="invoke:completion:test-agent")
    assert teardown_subtree is not None


def testcheck_process_result_missing_completion_evidence_calls_teardown_subtree(
    tmp_path: Path,
) -> None:
    handle = _FakeHandle(returncode=0, pid=5678)
    options = CompletionCheckOptions(
        execution_strategy=_CompletionEnforcingStrategy(),
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=None),
    )

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree") as mock_teardown,
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=options,
        )

    mock_teardown.assert_called_once_with(5678, issuer="invoke:completion:test-agent")


def testcheck_process_result_error_path_does_not_mutate_clock() -> None:
    clock = FakeClock(start=0.0)
    handle = _FakeHandle(returncode=1, pid=9999)

    with (
        patch("ralph.agents.invoke._completion.teardown_subtree"),
        pytest.raises(AgentInvocationError),
    ):
        check_process_result(
            handle,
            "test-agent",
            parsed_output=[],
            check_options=None,
            _clock=clock,
        )

    assert clock.monotonic() == 0.0


def test_record_mcp_tool_call_does_not_mutate_last_activity() -> None:
    wd, clock = _make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd.last_activity
    now = clock.monotonic()
    wd.record_mcp_tool_call(now=now)
    assert wd.last_activity == baseline
    assert wd.last_mcp_tool_call_at == now


def test_record_subagent_work_does_not_mutate_last_activity() -> None:
    wd, clock = _make_watchdog()
    wd.record_activity()
    clock.advance(1.0)
    baseline = wd.last_activity
    now = clock.monotonic()
    wd.record_subagent_work(now=now)
    assert wd.last_activity == baseline
    assert wd.last_subagent_progress_at == now


def test_record_workspace_event_weight_zero_does_not_advance_channel() -> None:
    wd, _ = _make_watchdog()
    wd.record_workspace_event(kind=WorkspaceChangeKind.OTHER, weight=0.0)
    assert wd.workspace_kind_counts == {}
    summary = wd.last_evidence_summary(0.0)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at is None


def test_record_workspace_event_source_weight_advances_channel() -> None:
    wd, clock = _make_watchdog()
    now = clock.monotonic()
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0, now=now)
    assert wd.workspace_kind_counts == {"source": 1}
    assert wd.last_workspace_event_at == now
    summary = wd.last_evidence_summary(now)
    workspace_summary = summary.channels[-1]
    assert workspace_summary.channel_name == ChannelName.WORKSPACE
    assert workspace_summary.last_at == now
    assert workspace_summary.can_defer is True


def test_record_subagent_work_description_strips_control_characters() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hello\nworld\rmore\tchars\x00\x01")
    assert "\n" not in (wd.last_subagent_progress_description or "")
    assert "\r" not in (wd.last_subagent_progress_description or "")
    assert "\t" not in (wd.last_subagent_progress_description or "")
    assert "\x00" not in (wd.last_subagent_progress_description or "")
    assert "\x01" not in (wd.last_subagent_progress_description or "")
    assert wd.last_subagent_progress_description == "helloworldmorechars"


def test_record_subagent_work_description_strips_ansi_escapes() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="\x1b[31mhello\x1b[0m world")
    stored = wd.last_subagent_progress_description
    assert stored is not None
    assert "\x1b" not in stored
    assert "hello" in stored
    assert "world" in stored


def test_record_subagent_work_description_redacts_tool_arguments() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='tool {"arguments": "secret_payload_value"}')
    stored = wd.last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_sensitive_paths() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="reading /etc/passwd then /proc/self/maps")
    stored = wd.last_subagent_progress_description or ""
    assert "/etc/" not in stored
    assert "/proc/" not in stored
    assert stored.count("<redacted>") >= 2


def test_record_subagent_work_description_redacts_bearer_token() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: Authorization: Bearer abc123token")
    stored = wd.last_subagent_progress_description or ""
    assert "Bearer" not in stored


def test_record_subagent_work_description_redacts_lowercase_bearer_token() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: authorization: bearer SECRET123")
    stored = wd.last_subagent_progress_description or ""
    assert "SECRET123" not in stored, (
        f"lowercase bearer token 'SECRET123' must NOT leak, got: {stored!r}"
    )
    assert "bearer" not in stored, f"lowercase 'bearer' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_uppercase_bearer_token() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: AUTHORIZATION: BEARER UPPERSECRET")
    stored = wd.last_subagent_progress_description or ""
    assert "UPPERSECRET" not in stored, (
        f"uppercase bearer token 'UPPERSECRET' must NOT leak, got: {stored!r}"
    )
    assert "BEARER" not in stored, f"uppercase 'BEARER' marker must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_bearer_token() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="hdr: AuThOrIzAtIoN: BeArEr MiXeDcAsE")
    stored = wd.last_subagent_progress_description or ""
    assert "MiXeDcAsE" not in stored, (
        f"mixed-case bearer token 'MiXeDcAsE' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_private_key_marker() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="key fragment -----BEGIN RSA PRIVATE KEY----- data")
    stored = wd.last_subagent_progress_description or ""
    assert "PRIVATE KEY" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_truncates_to_200_chars() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="a" * 500)
    stored = wd.last_subagent_progress_description or ""
    assert len(stored) == 200


def test_record_subagent_work_description_only_whitespace_stores_empty() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description="   \n\n  \t\t  ")
    stored = wd.last_subagent_progress_description
    assert stored == ""


def test_record_subagent_work_description_none_leaves_field_none() -> None:
    wd, _clock = _make_watchdog()
    prior = wd.last_subagent_progress_description
    wd.record_subagent_work(description=None)
    assert wd.last_subagent_progress_description == prior


def test_record_subagent_work_description_does_not_mutate_last_activity() -> None:
    wd, _clock = _make_watchdog()
    wd.record_activity()
    _clock.advance(1.0)
    baseline = wd.last_activity
    wd.record_subagent_work(description="anything goes here")
    assert wd.last_activity == baseline


def test_record_subagent_work_description_redacts_well_formed_escaped_quote() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "tail" not in stored, f"escaped-quote suffix 'tail' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_inner_quote() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments":"secret"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "tail" not in stored, f"malformed-JSON suffix 'tail' must be redacted, got: {stored!r}"
    assert "secret" not in stored, (
        f"malformed-JSON prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_args_inner_quote() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"args":"secret"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "tail" not in stored, (
        f"malformed-JSON args suffix 'tail' must be redacted, got: {stored!r}"
    )
    assert "secret" not in stored, (
        f"malformed-JSON args prefix 'secret' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_escaped_prompt_content() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"prompt": "say \\"hi\\" please"}')
    stored = wd.last_subagent_progress_description or ""
    assert "please" not in stored, (
        f"escaped-quote suffix 'please' must be redacted, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_escaped_file_path() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"file_path": "/etc/secret\\"name"}')
    stored = wd.last_subagent_progress_description or ""
    assert "name" not in stored, (
        f"escaped-quote file_path suffix 'name' must be redacted, got: {stored!r}"
    )
    assert "/etc/" not in stored, f"sensitive path /etc/ must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_bearer_token_with_quotes() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='Authorization: Bearer abc"def')
    stored = wd.last_subagent_progress_description or ""
    assert "def" not in stored, f"bearer token suffix 'def' must be redacted, got: {stored!r}"
    assert "Bearer" not in stored, f"bearer token prefix 'Bearer' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_input_field_with_quotes() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"input": "echo \\"hello\\" world"}')
    stored = wd.last_subagent_progress_description or ""
    assert "world" not in stored, f"input field suffix 'world' must be redacted, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_repeated_escaped_quotes() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"content": "say \\"hi\\" then \\"bye\\" now"}')
    stored = wd.last_subagent_progress_description or ""
    for forbidden in ("hi", "bye", "now"):
        assert forbidden not in stored, (
            f"escaped-quote value {forbidden!r} must be redacted, got: {stored!r}"
        )
    assert "<redacted>" in stored


def test_record_subagent_work_description_reproducer_no_leaked_suffix() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments":"secret\\"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "tail" not in stored, (
        f"analysis-feedback reproducer: 'tail' suffix leaked, got: {stored!r}"
    )
    assert "<redacted>" in stored
    assert "{" not in stored or "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_object_arguments() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments": {"command": "rm -rf /", "token": "abc"}}')
    stored = wd.last_subagent_progress_description or ""
    assert "rm -rf /" not in stored, f"nested 'command' value must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' key must NOT leak, got: {stored!r}"
    assert "abc" not in stored, f"nested 'abc' value must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_list_arguments() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"arguments": ["rm -rf /", "secret"]}')
    stored = wd.last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_object_input() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"input": {"echo": "hello", "user": "admin"}}')
    stored = wd.last_subagent_progress_description or ""
    assert "hello" not in stored
    assert "admin" not in stored
    assert "echo" not in stored
    assert "user" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_array_content() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"content": [{"text": "secret message"}]}')
    stored = wd.last_subagent_progress_description or ""
    assert "secret message" not in stored
    assert "text" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_prompt() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"prompt": {"role": "system", "content": "do the thing"}}')
    stored = wd.last_subagent_progress_description or ""
    assert "do the thing" not in stored
    assert "role" not in stored
    assert "system" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_nested_file_path() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"file_path": {"path": "/etc/passwd", "name": "shadow"}}')
    stored = wd.last_subagent_progress_description or ""
    assert "/etc/passwd" not in stored
    assert "shadow" not in stored
    assert "name" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_reproducer_nested_token() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description='{"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
    )
    stored = wd.last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_embedded_json_after_prefix() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"prompt": "hello, world"}')
    stored = wd.last_subagent_progress_description or ""
    assert "world" not in stored, f"comma-bearing value 'world' must NOT leak, got: {stored!r}"
    assert "hello" not in stored, (
        f"comma-bearing value prefix 'hello' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored, f"<redacted> marker must appear, got: {stored!r}"


def test_record_subagent_work_description_redacts_embedded_json_with_comma() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "abc,def", "x":1}')
    stored = wd.last_subagent_progress_description or ""
    assert "abc" not in stored, f"comma-bearing value 'abc' must NOT leak, got: {stored!r}"
    assert "def" not in stored, f"comma-bearing value 'def' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_embedded_nested_object() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=(
            'prefix {"name": "tool", "arguments": {"command": "echo secret", "token": "abc123"}}'
        )
    )
    stored = wd.last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' must NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' must NOT leak, got: {stored!r}"
    assert "secret" not in stored, f"nested 'secret' value must NOT leak, got: {stored!r}"
    assert "tool" in stored, f"non-sensitive 'name' key should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_multiple_embedded_fragments() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=('prefix {"arguments": "first"} middle {"arguments": "second"}')
    )
    stored = wd.last_subagent_progress_description or ""
    assert "first" not in stored, f"first fragment 'first' must NOT leak, got: {stored!r}"
    assert "second" not in stored, f"second fragment 'second' must NOT leak, got: {stored!r}"
    assert stored.count("<redacted>") == 2, f"both fragments must be redacted, got: {stored!r}"


def test_record_subagent_work_description_handles_malformed_inner_quote_after_prefix() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='prefix {"arguments": "secret"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "secret" not in stored, f"malformed-JSON 'secret' must NOT leak, got: {stored!r}"
    assert "tail" not in stored, f"malformed-JSON 'tail' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_args_payload() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description='{"type":"tool_call","name":"bash","args":{"command":"rm -rf /","token":"abc"}}'
    )
    stored = wd.last_subagent_progress_description or ""
    assert "rm -rf /" not in stored, (
        f"nested 'command' value 'rm -rf /' must NOT leak, got: {stored!r}"
    )
    assert "abc" not in stored, f"nested 'token' value 'abc' must NOT leak, got: {stored!r}"
    assert "command" not in stored, f"nested 'command' KEY must NOT leak, got: {stored!r}"
    assert "token" not in stored, f"nested 'token' KEY must NOT leak, got: {stored!r}"
    assert "tool_call" in stored, (
        f"non-sensitive 'type' value 'tool_call' should survive, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_scalar_args_value() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"name":"bash","args":"secret_payload_value"}')
    stored = wd.last_subagent_progress_description or ""
    assert "secret_payload_value" not in stored, (
        f"scalar 'args' value 'secret_payload_value' must NOT leak, got: {stored!r}"
    )
    assert "bash" in stored, f"non-sensitive 'name' value 'bash' should survive, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_list_args_value() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"args": ["rm -rf /", "secret"]}')
    stored = wd.last_subagent_progress_description or ""
    assert "rm -rf /" not in stored
    assert "secret" not in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_tool_call_line_with_nested_args() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(
        description=(
            '{"type":"tool_call","name":"bash","args":{"command":"echo secret","token":"abc123"}}'
        )
    )
    stored = wd.last_subagent_progress_description or ""
    assert "echo secret" not in stored, (
        f"nested 'command' value 'echo secret' MUST NOT leak, got: {stored!r}"
    )
    assert "abc123" not in stored, f"nested 'token' value 'abc123' MUST NOT leak, got: {stored!r}"
    assert "tool_call" in stored
    assert "bash" in stored
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_prompt_key() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Prompt": "SECRET-upper"}')
    stored = wd.last_subagent_progress_description or ""
    assert "SECRET-upper" not in stored, (
        f"mixed-case 'Prompt' value 'SECRET-upper' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_arguments_key() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Arguments": {"token": "SECRET-mixed"}}')
    stored = wd.last_subagent_progress_description or ""
    assert "SECRET-mixed" not in stored, (
        f"mixed-case 'Arguments' value 'SECRET-mixed' must NOT leak, got: {stored!r}"
    )
    assert "token" not in stored, f"nested sibling 'token' must NOT leak, got: {stored!r}"
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_input_key() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Input": "SECRET-input"}')
    stored = wd.last_subagent_progress_description or ""
    assert "SECRET-input" not in stored, (
        f"mixed-case 'Input' value 'SECRET-input' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_mixed_case_content_key() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Content": "SECRET-content"}')
    stored = wd.last_subagent_progress_description or ""
    assert "SECRET-content" not in stored, (
        f"mixed-case 'Content' value 'SECRET-content' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored


def test_record_subagent_work_description_redacts_malformed_mixed_case_arguments() -> None:
    wd, _clock = _make_watchdog()
    wd.record_subagent_work(description='{"Arguments": "secret"tail"}')
    stored = wd.last_subagent_progress_description or ""
    assert "secret" not in stored, (
        f"malformed mixed-case 'Arguments' prefix 'secret' must NOT leak, got: {stored!r}"
    )
    assert "tail" not in stored, (
        f"malformed mixed-case 'Arguments' suffix 'tail' must NOT leak, got: {stored!r}"
    )
    assert "<redacted>" in stored
