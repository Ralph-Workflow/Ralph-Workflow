from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from ralph.recovery.failure_details import contains_casefolded_marker
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS

from ._agent_invocation_error import AgentInvocationError

_CREDENTIALS_FAILURE_SUBSTRINGS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "api key",
    "apikey",
    "authentication",
    "credentials",
    "openai_api_key",
    "anthropic_api_key",
    "please set",
    "missing key",
    "invalid key",
    "expired key",
)


def looks_like_credentials_failure(text: str) -> bool:
    return contains_casefolded_marker([text], _CREDENTIALS_FAILURE_SUBSTRINGS)


@runtime_checkable
class _ReturncodeHandle(Protocol):
    @property
    def returncode(self) -> object: ...


@runtime_checkable
class _StderrHandle(Protocol):
    @property
    def stderr(self) -> object: ...


def terminal_returncode(handle: object, *, agent_name: str) -> int:
    returncode = cast("object", getattr(handle, "returncode", None))
    if not isinstance(returncode, int):
        raise AgentInvocationError(
            agent_name=agent_name,
            returncode=-1,
            stderr="process lifecycle ended without a terminal return code",
            failure_origin="agent",
        )
    return returncode


def credentials_failure_needs_broken_exit(
    stderr_text: str,
    elapsed_seconds: float | None,
) -> bool:
    return looks_like_credentials_failure(stderr_text) and (
        elapsed_seconds is None or elapsed_seconds < BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    )


_MAX_STDERR_CAPTURE_BYTES: int = 64 * 1024


@runtime_checkable
class _ReadableTextPipe(Protocol):
    def read(self, size: int = -1, /) -> str: ...


@runtime_checkable
class _BoundedStderrHolder(Protocol):
    @property
    def _ralph_bounded_stderr(self) -> object: ...


def _bounded_stderr_text(handle: object) -> str | None:
    if not isinstance(handle, _BoundedStderrHolder):
        return None
    try:
        captured = handle._ralph_bounded_stderr
    except AttributeError:
        return None
    return captured if isinstance(captured, str) else None


def _truncation_marker(capped_bytes: int) -> str:
    return f"\n[stderr truncated: more than {capped_bytes} bytes]"


def _bounded_read(pipe: _ReadableTextPipe) -> str:
    chunk = pipe.read(_MAX_STDERR_CAPTURE_BYTES)
    if len(chunk) >= _MAX_STDERR_CAPTURE_BYTES:
        probe = pipe.read(1)
        if probe:
            chunk = chunk + _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
    return chunk


def read_bounded_stderr(handle: object) -> str:
    captured = _bounded_stderr_text(handle)
    if captured is not None:
        return captured
    if not isinstance(handle, _StderrHandle):
        return ""
    try:
        stderr_pipe = handle.stderr
        return _bounded_read(stderr_pipe) if isinstance(stderr_pipe, _ReadableTextPipe) else ""
    except (OSError, RuntimeError, ValueError, AttributeError, UnicodeDecodeError):
        return ""
