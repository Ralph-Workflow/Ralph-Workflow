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

A signature is NOT the failure text alone. :func:`failure_signature` is
the seam the transport must use: it binds the failure to the request
that produced it (method + tool name + arguments) and it declines to
count failures that are Ralph's OWN infrastructure fault. Both halves
are load-bearing, and both were learned the hard way:

* Keying on the error frame alone made three reads of three DIFFERENT
  files collapse onto one signature, because a corrupt SQLite cache
  killed ``read_file`` before the path mattered and every call reported
  the same words. Three distinct calls is normal agent behaviour; the
  breaker answered the third with ``transport_loop_detected``.
* Counting infrastructure faults at all is wrong even when the call IS
  repeated. An agent retrying a tool that is broken server-side is
  behaving correctly, and tripping the breaker there converts a
  recoverable server fault into a dead agent while replacing the real
  cause (``database disk image is malformed``) with a useless breaker
  message nobody can act on. Those failures pass straight through so
  the agent sees the truth.

A genuine loop -- the same call, the same arguments, the same
non-infrastructure failure, over and over -- still trips unchanged.

This module is the transport-layer complement to
``ralph/pipeline/_retry_progress_guard.py`` (the agent-recovery layer).
Both layers share the same intent (cap identical-failure loops) and the
same stripping vocabulary; the tracker is a small, single-purpose,
thread-safe dataclass so the production transport can call it from any
thread without locks other than the embedded one.
"""

from __future__ import annotations

import hashlib
import json
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


#: Failure text that means RALPH's storage or host resources broke, not
#: that the caller is looping. Every entry names a concrete fault the
#: process itself suffered -- a corrupt or unopenable SQLite cache, a
#: locked database, an exhausted or read-only filesystem, an exhausted
#: descriptor table. Retrying against one of these is the correct thing
#: for an agent to do, and the correct thing for us to do is hand back
#: the real error so the operator can see WHICH resource died.
#:
#: Substring matching is safe here in a way it is NOT in
#: ``ralph.pipeline.conflict_resolution.attempt_fault``: that module
#: scans the AGENT's output, where an agent discussing this repository
#: can quote a marker and get itself killed. This module matches the
#: error frame the SERVER is about to emit -- text Ralph produced, from
#: an exception Ralph caught. An agent cannot inject it.
#:
#: Deliberately excluded: "file not found", "permission denied",
#: "no such file" and friends. Those are provoked by the caller's own
#: arguments, so a caller repeating one really is looping.
INFRASTRUCTURE_FAULT_MARKERS: tuple[str, ...] = (
    "database disk image is malformed",
    "database or disk is full",
    "database is locked",
    "database table is locked",
    "file is not a database",
    "unable to open database file",
    "attempt to write a readonly database",
    "disk i/o error",
    "input/output error",
    "no space left on device",
    "read-only file system",
    "too many open files",
    "cannot allocate memory",
)


def is_infrastructure_fault(text: str) -> bool:
    """Return True when ``text`` reports a Ralph-side infrastructure fault.

    A True answer means the failure is the server's own broken storage or
    exhausted host resources, so the caller must NOT count it toward the
    repetition bound and must surface the underlying error unchanged.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in INFRASTRUCTURE_FAULT_MARKERS)


def _canonical_params(params: object) -> str:
    """Return a stable, order-independent rendering of a request's params."""
    try:
        return json.dumps(params, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        # Circular or otherwise unrenderable params: fall back to repr so
        # the fingerprint stays total rather than raising into the
        # transport's error path.
        return repr(params)


def request_fingerprint(method: str, params: object) -> str:
    """Return a bounded identity for one request: its method and its arguments.

    Two calls to the same tool with different arguments are DIFFERENT
    calls and must not share a repetition key. The params are canonicalized
    (key order does not matter), run through :func:`signature_for` so a
    volatile token embedded in the arguments cannot let a real loop evade
    the bound, then hashed so an arbitrarily large arguments blob cannot
    grow the tracker's retained state.
    """
    canonical = signature_for(f"{method}\x1f{_canonical_params(params)}")
    digest = hashlib.blake2b(canonical.encode("utf-8", "replace"), digest_size=16)
    return f"{method}#{digest.hexdigest()}"


def failure_signature(
    method: str, params: object, failure: BaseException | str
) -> str | None:
    """Return the repetition key for a failed request, or None to skip it.

    ``None`` means "do not observe this failure": it is a Ralph-side
    infrastructure fault, and the transport must write the real error
    frame through to the agent instead of counting it toward the breaker.

    Otherwise the key binds the request identity to the normalized
    failure text, so the breaker trips only on the same call, with the
    same arguments, failing the same way.
    """
    signature = signature_for(failure)
    if is_infrastructure_fault(signature):
        return None
    return f"{request_fingerprint(method, params)}|{signature}"


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
    "INFRASTRUCTURE_FAULT_MARKERS",
    "THRESHOLD",
    "WINDOW_SECONDS",
    "TransportRepetitionTracker",
    "failure_signature",
    "is_infrastructure_fault",
    "request_fingerprint",
    "signature_for",
]
