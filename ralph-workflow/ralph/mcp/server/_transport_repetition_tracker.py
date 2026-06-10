"""Transport-level repetition tracker for the production HTTP handler.

A streamable-HTTP client cannot distinguish a bodyless stream-close from a
still-running call, so the same client retries an identical -32001-class
failure forever. The transport-level breaker here observes each failure's
stripped signature, and when the same signature appears ``THRESHOLD`` times
within ``WINDOW_SECONDS``, the next attempt returns a
``transport_loop_detected`` error frame (HTTP 503) instead of the
silent bodyless hang.

The signature function strips volatile tokens (UUIDs, request_ids, and
timestamps) so a doomed retry that prints a changing token cannot evade
the bound. Only NON-volatile content differing between attempts resets the
streak.

This module is the transport-layer complement to
``ralph/pipeline/_retry_progress_guard.py`` (the agent-recovery layer).
Both layers share the same intent (cap identical-failure loops) and the
same stripping vocabulary; the tracker is a small, single-purpose,
thread-safe dataclass so the production transport can call it from any
thread without locks other than the embedded one.
"""

from __future__ import annotations

import re
import threading
import time as _time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Time window (seconds) over which ``THRESHOLD`` identical signatures
#: trip the breaker. Aligns with the agent-recovery layer's window.
WINDOW_SECONDS: float = 60.0

#: Number of identical signatures within the window that trip the breaker.
THRESHOLD: int = 3

#: HTTP status for the breaker response (503 + JSON-RPC -32001 frame).
BREAKER_STATUS: int = 503

#: Error code on the breaker JSON-RPC frame.
BREAKER_CODE: int = -32001

#: Error message on the breaker JSON-RPC frame.
BREAKER_MESSAGE: str = "transport_loop_detected"

#: Strip volatile tokens from a failure signature. Mirrors the vocabulary
#: in :mod:`ralph.pipeline._retry_progress_guard` so the two layers
#: recognize the same patterns.
_VOLATILE_UUID_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
_VOLATILE_TIMESTAMP = re.compile(
    r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b"
    r"|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_VOLATILE_REQUEST_ID = re.compile(r"\brequest[_-]?id[=:]\s*\S+", re.IGNORECASE)


def signature_for(exc: BaseException | str) -> str:
    """Return a normalized signature for a failure, stripping volatile tokens.

    Accepts an exception (uses ``type(exc).__name__`` + ``str(exc)``) or a
    pre-built string. The returned signature is what the tracker compares
    between attempts.
    """
    text = f"{type(exc).__name__}:{exc}" if isinstance(exc, BaseException) else exc
    text = _VOLATILE_UUID_HEX.sub("<uuid>", text)
    text = _VOLATILE_TIMESTAMP.sub("<ts>", text)
    text = _VOLATILE_REQUEST_ID.sub("request_id=<id>", text)
    return text.lower()


@dataclass
class TransportRepetitionTracker:
    """Track consecutive identical failure signatures at the transport layer.

    Thread-safe. A new instance is created per process so the counter
    surface is consistent across the production transport.
    """

    window_seconds: float = WINDOW_SECONDS
    threshold: int = THRESHOLD
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_signature: str | None = field(default=None, init=False)
    _last_seen_at: float = field(default=0.0, init=False)
    _streak: int = field(default=0, init=False)
    _clock: Callable[[], float] = field(default=_time.monotonic, repr=False)

    def observe(self, signature: str) -> bool:
        """Record a failure signature; return True when the breaker trips.

        Returns True on the ``threshold``-th identical signature within
        ``window_seconds`` of the first. The caller writes a 503 +
        ``transport_loop_detected`` frame and short-circuits the response.
        """
        now = self._clock()
        with self._lock:
            if (
                self._last_signature == signature
                and (now - self._last_seen_at) <= self.window_seconds
            ):
                self._streak += 1
            else:
                # Different signature OR window expired — reset the streak.
                self._last_signature = signature
                self._streak = 1
            self._last_seen_at = now
            return self._streak >= self.threshold

    def reset(self) -> None:
        """Clear the streak. Used by tests and by recovery handlers."""
        with self._lock:
            self._last_signature = None
            self._streak = 0
            self._last_seen_at = 0.0

    def snapshot(self) -> dict[str, object]:
        """Return the current state for diagnostics."""
        with self._lock:
            return {
                "last_signature": self._last_signature,
                "streak": self._streak,
                "threshold": self.threshold,
                "window_seconds": self.window_seconds,
            }


__all__ = [
    "BREAKER_CODE",
    "BREAKER_MESSAGE",
    "BREAKER_STATUS",
    "THRESHOLD",
    "WINDOW_SECONDS",
    "TransportRepetitionTracker",
    "signature_for",
]
