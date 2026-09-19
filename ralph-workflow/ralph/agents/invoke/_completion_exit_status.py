"""Small exit-status decisions shared by completion checking."""

from __future__ import annotations

from typing import cast

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
