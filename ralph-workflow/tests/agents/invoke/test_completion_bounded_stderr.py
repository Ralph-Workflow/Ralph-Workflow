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

import pytest

import ralph.agents.invoke._completion as completion_mod
from ralph.agents.invoke._completion import (
    _MAX_STDERR_CAPTURE_BYTES,
    _bounded_read,
    _truncation_marker,
)


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
    """Wire ``_bounded_read`` into ``_check_process_result`` and assert the
    captured stderr is bounded by the cap with a truncation marker."""
    captured: list[str] = []

    def fake_bounded(pipe: object) -> str:
        out = _bounded_read(pipe)
        captured.append(out)
        return out

    monkeypatch.setattr("ralph.agents.invoke._completion._bounded_read", fake_bounded)

    big_stderr = "Y" * (_MAX_STDERR_CAPTURE_BYTES * 5)

    class _FakeHandle:
        returncode = 1

        def __init__(self) -> None:
            self.stderr = _ChunkedPipe(big_stderr)

    fake_handle = _FakeHandle()

    with pytest.raises(Exception) as excinfo:
        completion_mod._check_process_result(fake_handle, "test-agent", None, None)
    _ = excinfo.value
    assert captured, "_bounded_read must have been invoked"
    assert len(captured[0]) <= _MAX_STDERR_CAPTURE_BYTES + len(
        _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
    ), f"captured stderr must be capped+marker, got length {len(captured[0])}"
    assert _truncation_marker(_MAX_STDERR_CAPTURE_BYTES) in captured[0], (
        f"truncation marker missing from captured stderr {captured[0][-200:]!r}"
    )


def test_completion_no_stderr_pipe_does_not_crash() -> None:
    """When the handle has no stderr pipe, the bounded helper must NOT be
    invoked and the legacy fallback ``'(unable to read stderr)'`` is used."""

    class _FakeHandle:
        returncode = 1
        stderr = None

    with pytest.raises(Exception) as excinfo:
        completion_mod._check_process_result(_FakeHandle(), "test-agent", None, None)
    assert "(unable to read stderr)" in str(excinfo.value), (
        f"expected fallback message; got {excinfo.value!r}"
    )
