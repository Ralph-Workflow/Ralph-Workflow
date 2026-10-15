"""Black-box tests for bounded stderr capture in ``_completion.py``.

wt-024 memory-perf AC-05: the non-zero-exit stderr capture is an
unbounded full read on the subprocess stderr pipe. A crashing agent
spewing many MB to stderr OOMs the parent. This test asserts the new
bounded helper caps the read at ``_MAX_STDERR_CAPTURE_BYTES`` and
appends a truncation marker.

All tests are unit tests using a fake handle with a stub stderr pipe
that records the bytes it was asked to produce. No real subprocess,
no real I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ralph.agents.invoke._completion as completion_mod
from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import BaseExecutionStrategy
from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._completion import CompletionCheckOptions
from ralph.agents.invoke._completion_exit_status import (
    _MAX_STDERR_CAPTURE_BYTES,
    _bounded_read,
    _ReadableTextPipe,
    _truncation_marker,
)
from ralph.process.manager import ManagedPtyProcess

if TYPE_CHECKING:
    from ralph.phases.required_artifacts import RequiredArtifact


class _ChunkedPipe:
    """Stub that returns chunks via a read(n) API like real subprocess pipes."""

    def __init__(self, payload: str, chunk_size: int = 4096) -> None:
        self._payload = payload
        self._offset = 0
        self._chunk_size = chunk_size
        self.read_calls: list[int] = []

    def read(self, size: int = -1) -> str:
        self.read_calls.append(size)
        if size < 0:
            size = len(self._payload) - self._offset
        end = min(self._offset + size, len(self._payload))
        chunk = self._payload[self._offset : end]
        self._offset = end
        return chunk


class _FakeHandle(ManagedPtyProcess):
    """Stub process handle satisfying ManagedPtyProcess for completion tests."""

    def __init__(
        self,
        returncode: int | None = None,
        stderr: _ChunkedPipe | None = None,
        terminal_reason: str | None = None,
    ) -> None:
        self._fake_returncode = returncode
        self.stderr = stderr
        self._terminal_reason = terminal_reason

    @property
    def returncode(self) -> int | None:
        return self._fake_returncode

    @property
    def pid(self) -> int:
        return 0

    def original_terminal_reason(self) -> str | None:
        return self._terminal_reason

    def termination_issuer(self) -> str | None:
        return None


class _StubStrategy(BaseExecutionStrategy):
    def supports_incomplete_exit_reprompt(self) -> bool:
        return True


def _stub_eval_completion(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: RequiredArtifact | None = None,
    run_id: str | None = None,
    sentinel_secret: str | None = None,
    receipt_secret: str | None = None,
) -> CompletionSignals:
    # Minimal stub — the real terminal logic lives in apply_sentinel_signal
    # which is called by _agy_has_terminal_completion_evidence after this.
    return CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
    )


def test_bounded_read_caps_at_max_bytes() -> None:
    """``_bounded_read`` returns at most _MAX_STDERR_CAPTURE_BYTES bytes from
    the pipe (the truncation marker is appended after the cap; the pipe
    itself never contributes more than the cap)."""
    payload = "x" * (_MAX_STDERR_CAPTURE_BYTES * 4)
    pipe = _ChunkedPipe(payload)
    captured = _bounded_read(pipe)
    marker = _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
    assert captured.endswith(marker), f"expected marker at tail; got tail {captured[-100:]!r}"
    prefix = captured[: -len(marker)]
    assert len(prefix) == _MAX_STDERR_CAPTURE_BYTES, (
        f"expected prefix to be capped at {_MAX_STDERR_CAPTURE_BYTES}; got {len(prefix)}"
    )


def test_bounded_read_appends_truncation_marker() -> None:
    """When the pipe has more bytes than the cap, append a truncation marker."""
    payload = "x" * (_MAX_STDERR_CAPTURE_BYTES * 2)
    pipe = _ChunkedPipe(payload)
    captured = _bounded_read(pipe)
    assert captured.endswith(_truncation_marker(_MAX_STDERR_CAPTURE_BYTES)), (
        f"expected truncation marker; got tail {captured[-200:]!r}"
    )


def test_bounded_read_short_payload_unchanged() -> None:
    """When the payload fits within the cap, no marker is appended."""
    payload = "small stderr\n"
    pipe = _ChunkedPipe(payload)
    captured = _bounded_read(pipe)
    assert captured == payload, f"short payload must be returned verbatim; got {captured!r}"


def test_bounded_read_uses_only_capped_chunk_reads() -> None:
    """``_bounded_read`` must NOT make an unbounded ``read(-1)`` call."""
    payload = "x" * (_MAX_STDERR_CAPTURE_BYTES * 3)
    pipe = _ChunkedPipe(payload, chunk_size=4096)
    _bounded_read(pipe)
    for size in pipe.read_calls:
        assert isinstance(size, int)
        assert size > 0, (
            f"unbounded read detected (size={size}); _bounded_read must always "
            f"pass a positive byte limit"
        )


def test_completion_uses_bounded_read_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire ``_bounded_read`` into ``check_process_result`` and assert the
    captured stderr is bounded by the cap with a truncation marker."""
    captured: list[str] = []

    def fake_bounded(pipe: _ReadableTextPipe) -> str:
        out = _bounded_read(pipe)
        captured.append(out)
        return out

    monkeypatch.setattr("ralph.agents.invoke._completion_exit_status._bounded_read", fake_bounded)

    big_stderr = "Y" * (_MAX_STDERR_CAPTURE_BYTES * 5)
    fake_handle = _FakeHandle(returncode=1, stderr=_ChunkedPipe(big_stderr))

    with pytest.raises(Exception) as excinfo:
        completion_mod.check_process_result(fake_handle, "test-agent", None, None)
    _ = excinfo.value
    assert captured, "_bounded_read must have been invoked"
    assert len(captured[0]) <= _MAX_STDERR_CAPTURE_BYTES + len(
        _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
    ), f"captured stderr must be capped+marker, got length {len(captured[0])}"
    assert _truncation_marker(_MAX_STDERR_CAPTURE_BYTES) in captured[0], (
        f"truncation marker missing from captured stderr {captured[0][-200:]!r}"
    )


def test_completion_no_stderr_pipe_preserves_bounded_merged_output() -> None:
    """A PTY failure uses its bounded merged output as the error detail."""
    with pytest.raises(Exception) as excinfo:
        completion_mod.check_process_result(
            _FakeHandle(returncode=1),
            "test-agent",
            ["provider terminal failure"],
            None,
        )

    assert "provider terminal failure" in str(excinfo.value)
    assert "(unable to read stderr)" not in str(excinfo.value)


def test_completion_rejects_missing_terminal_returncode() -> None:
    """Completion must fail closed when lifecycle finalization did not establish an exit."""
    with pytest.raises(AgentInvocationError, match="terminal return code") as exc_info:
        completion_mod.check_process_result(_FakeHandle(returncode=None), "test-agent", None, None)
    assert exc_info.value.agent_name == "test-agent"
    assert exc_info.value.returncode == -1
    assert exc_info.value.failure_origin == "agent"
    assert not isinstance(exc_info.value, RuntimeError)


@pytest.mark.timeout_seconds(5)
def test_completion_evidence_short_circuits_missing_returncode(tmp_path: Path) -> None:
    """Handle with interactive_completion reason and durable evidence returns
    success even when returncode is None (the bug case)."""
    opts = CompletionCheckOptions(
        workspace_path=tmp_path,
        execution_strategy=_StubStrategy(),
        evaluate_completion_fn=_stub_eval_completion,
        captured_session_id="test-run-id",
        _sentinel_check_fn=lambda workspace, run_id: True,
    )

    # Must NOT raise — the bypass short-circuits before the fail-closed guard
    completion_mod.check_process_result(
        _FakeHandle(returncode=None, terminal_reason="interactive_completion"),
        "agy",
        parsed_output=["result SUCCESS"],
        check_options=opts,
    )


@pytest.mark.timeout_seconds(5)
def test_no_terminal_reason_still_fails_closed_with_typed_invocation_error() -> None:
    """Handle without original_terminal_reason still hits the fail-closed guard."""
    with pytest.raises(AgentInvocationError, match="terminal return code") as exc_info:
        completion_mod.check_process_result(
            _FakeHandle(returncode=None), "test-agent", parsed_output=None, check_options=None
        )
    assert exc_info.value.agent_name == "test-agent"
    assert exc_info.value.returncode == -1
    assert exc_info.value.failure_origin == "agent"
    assert not isinstance(exc_info.value, RuntimeError)
