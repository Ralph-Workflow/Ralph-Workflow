"""Small exit-status decisions shared by completion checking."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from ralph.recovery.failure_details import contains_casefolded_marker
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS

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
    """Return whether text carries a known credential/authentication failure marker."""
    return contains_casefolded_marker([text], _CREDENTIALS_FAILURE_SUBSTRINGS)


def terminal_returncode(handle: object) -> int:
    """Return the finalized process status or fail closed on an incomplete lifecycle."""
    returncode = cast("object", getattr(handle, "returncode", None))
    if not isinstance(returncode, int):
        raise RuntimeError("process lifecycle ended without a terminal return code")
    return returncode


def credentials_failure_needs_broken_exit(
    stderr_text: str,
    elapsed_seconds: float | None,
) -> bool:
    """Return whether an early credential failure lacks enough activity evidence."""
    return looks_like_credentials_failure(stderr_text) and (
        elapsed_seconds is None or elapsed_seconds < BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    )


_MAX_STDERR_CAPTURE_BYTES: int = 64 * 1024


@runtime_checkable
class _CapturedStderrHandle(Protocol):
    @property
    def _ralph_bounded_stderr(self) -> str: ...


@runtime_checkable
class _ReadableTextPipe(Protocol):
    """Minimal typed boundary for a captured text stderr pipe."""

    def read(self, size: int = -1, /) -> str: ...


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
    """Return the bounded stderr captured from a subprocess handle, if available.

    Stderr is diagnostic evidence only. A closed or malformed transport pipe
    must not replace the original process-exit classification, so read errors
    deliberately degrade to an empty diagnostic.
    """
    if isinstance(handle, _CapturedStderrHandle):
        return handle._ralph_bounded_stderr
    try:
        stderr_pipe: object = getattr(handle, "stderr", None)
        return _bounded_read(stderr_pipe) if isinstance(stderr_pipe, _ReadableTextPipe) else ""
    except Exception:
        return ""
