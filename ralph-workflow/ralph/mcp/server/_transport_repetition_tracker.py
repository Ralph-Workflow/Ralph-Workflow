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

This module provides transport-layer repetition detection for MCP server calls.
The tracker is a small, single-purpose, thread-safe dataclass so the production
transport can call it from any thread without locks other than the embedded one.

**The streak outlives the process (E1.7).** A make-before-break
replacement retires one MCP child and promotes another in its place, so
an in-memory counter is the wrong lifetime: every swap would silently
hand a doomed retry storm a fresh budget of ``THRESHOLD`` attempts and
the loop would never break. The tracker therefore persists its streak to
the PARENT-OWNED MCP snapshot directory (``RALPH_MCP_SNAPSHOT_DIR``) --
the directory the lifecycle computes once per bridge run and hands to
every spawn, and the directory the per-stage pause/resume handover
sentinels already live in. A child given that directory reads back what
the retiring child wrote; a child given nothing stays purely in-memory,
exactly as before. See :meth:`TransportRepetitionTracker.from_parent_owned_state`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import threading
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING

from ralph.mcp.protocol.env import MCP_SNAPSHOT_DIR_ENV

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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

#: File name of the carried repetition record inside the parent-owned
#: snapshot directory. A sibling of ``session.json`` and the per-stage
#: ``accept.pause.*`` / ``accept.resume.*`` handover sentinels.
REPETITION_STATE_FILENAME: str = "transport-repetition-state.json"

#: Bounded deadline (seconds) for acquiring the cross-process flock that
#: serialises the carried record's read-modify-write. A retiring child
#: draining an in-flight request can observe a failure concurrently with
#: the promoted candidate, so the merge is not lock-free.
_STATE_LOCK_TIMEOUT_S: float = 0.2

#: Bounded read size (bytes) for the carried record. The record is a
#: fixed three-field object; a larger file is truncated rather than
#: loaded, so a corrupt or hostile file cannot grow the tracker's memory.
_STATE_MAX_BYTES: int = 8192

if not _STATE_LOCK_TIMEOUT_S > 0:
    raise RuntimeError(f"_STATE_LOCK_TIMEOUT_S must be positive (got {_STATE_LOCK_TIMEOUT_S})")

if not _STATE_MAX_BYTES > 0:
    raise RuntimeError(f"_STATE_MAX_BYTES must be positive (got {_STATE_MAX_BYTES})")

#: Strip volatile tokens from a failure signature.
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


def failure_signature(method: str, params: object, failure: BaseException | str) -> str | None:
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


def repetition_state_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Return the parent-owned carried-streak file, or None when unconfigured.

    The lifecycle sets ``RALPH_MCP_SNAPSHOT_DIR`` to
    ``snapshot_dir(workspace_root, bridge_run_id)`` on EVERY spawn of a
    bridge run -- including every make-before-break candidate -- so the
    resolved path is identical across a child replacement by
    construction. The per-stage ``accept.pause.*`` / ``accept.resume.*``
    handover sentinels already live in that same directory, which is
    what makes it the parent-owned location rather than a child-local
    scratch file.

    Returns ``None`` when the parent supplied no snapshot directory (a
    plain standalone server, or a test that never set the variable). The
    caller then keeps a purely in-process streak.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(MCP_SNAPSHOT_DIR_ENV)
    if not raw:
        return None
    return Path(raw) / REPETITION_STATE_FILENAME


def _acquire_state_lock(handle: IO[str], timeout: float) -> bool:
    """Best-effort exclusive flock on ``handle``; False when unavailable.

    Mirrors the wire ledger's append-sidecar policy: contention past the
    deadline degrades to "do not persist this round" rather than blocking
    a request thread on a peer that may be draining indefinitely.
    """
    deadline = _time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if _time.monotonic() >= deadline:
                return False
            # filesystem-poll-ok: bounded retry loop for the cross-process state lock
            _time.sleep(0.01)
        except OSError:
            return False


def _release_state_lock(handle: IO[str]) -> None:
    with contextlib.suppress(OSError, ValueError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _decode_carried_state(raw: bytes) -> tuple[str, int, float] | None:
    """Decode a carried record, rejecting anything unexpected.

    Returns ``(signature, streak, seen_wall)`` or ``None``. ``bool`` is an
    ``int`` subclass in Python, so the streak and timestamp are rejected
    explicitly when they arrive as booleans: a fabricated record must
    never be able to trip the breaker.
    """
    try:
        decoded: object = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    signature = decoded.get("last_signature")
    streak = decoded.get("streak")
    seen_wall = decoded.get("last_seen_wall")
    if not isinstance(signature, str) or not isinstance(streak, int):
        return None
    if isinstance(streak, bool) or isinstance(seen_wall, bool):
        return None
    if not isinstance(seen_wall, int | float):
        return None
    return signature, streak, float(seen_wall)


@dataclass
class TransportRepetitionTracker:
    """Track consecutive identical failure signatures at the transport layer.

    Thread-safe. One instance is created per child process so the counter
    surface is consistent across the production transport.

    ``state_path`` extends the lifetime past the process. When set, the
    streak is written to that file after every observation and read back
    before the next one, so a make-before-break replacement carries the
    count into the promoted child instead of resetting it. When None the
    tracker is purely in-memory, the behaviour every existing caller has.
    """

    window_seconds: float = WINDOW_SECONDS
    threshold: int = THRESHOLD
    state_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_signature: str | None = field(default=None, init=False)
    _last_seen_at: float = field(default=0.0, init=False)
    _streak: int = field(default=0, init=False)
    _clock: Callable[[], float] = field(default=_time.monotonic, repr=False)

    @classmethod
    def from_parent_owned_state(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        window_seconds: float = WINDOW_SECONDS,
        threshold: int = THRESHOLD,
    ) -> TransportRepetitionTracker:
        """Build a tracker bound to the parent-owned snapshot directory.

        This is the production construction path: the handler's
        process-lifetime singleton goes through here, so the streak
        survives a child replacement whenever the lifecycle handed this
        child a snapshot directory. ``env`` defaults to the process
        environment, which is where the parent puts it.
        """
        return cls(
            window_seconds=window_seconds,
            threshold=threshold,
            state_path=repetition_state_path(env),
        )

    def observe(self, signature: str) -> bool:
        """Record a failure signature; return True when the breaker trips.

        Returns True on the ``threshold``-th identical signature within
        ``window_seconds`` of the first. The caller writes a 503 +
        ``transport_loop_detected`` frame and short-circuits the response.
        """
        with self._lock:
            path = self.state_path
            if path is None:
                return self._observe_in_memory(signature)
            return self._observe_with_carried_state(signature, path)

    def _observe_in_memory(self, signature: str) -> bool:
        """Advance the streak and report whether the breaker trips."""
        now = self._clock()
        if self._last_signature == signature and (now - self._last_seen_at) <= self.window_seconds:
            self._streak += 1
        else:
            # Different signature OR window expired — reset the streak.
            self._last_signature = signature
            self._streak = 1
        self._last_seen_at = now
        return self._streak >= self.threshold

    def _observe_with_carried_state(self, signature: str, path: Path) -> bool:
        """Merge the on-disk streak, advance it, and write it back.

        Falls back to the in-memory-only path whenever the carried record
        cannot be locked or written: the breaker must keep working even
        when its cross-process channel is unavailable, because a missed
        persistence write costs one retry of slack while an un-tripped
        breaker costs an unbounded loop.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # filesystem-write-ok: persistent advisory-lock sidecar serializes cross-process repetition tracker updates
            handle = path.with_name(path.name + ".lock").open("a", encoding="utf-8")
        except OSError:
            return self._observe_in_memory(signature)
        try:
            if not _acquire_state_lock(handle, _STATE_LOCK_TIMEOUT_S):
                return self._observe_in_memory(signature)
            try:
                self._adopt_carried_state(path)
                tripped = self._observe_in_memory(signature)
                self._write_carried_state(path)
            finally:
                _release_state_lock(handle)
            return tripped
        finally:
            with contextlib.suppress(OSError):
                handle.close()

    def _adopt_carried_state(self, path: Path) -> None:
        """Merge the previous child's streak into this one.

        A newer record for the same signature only ever raises the streak
        (a draining sibling child cannot roll back the promoted child's
        count). A record naming a different signature is adopted only
        when this process has not observed anything yet, so a fresh
        request never inherits an unrelated loop.
        """
        carried = self._read_carried_state(path)
        if carried is None:
            return
        signature, streak, seen_at = carried
        if streak <= 0:
            return
        if self._streak > 0 and (self._last_signature != signature or self._last_seen_at > seen_at):
            return
        self._last_signature = signature
        self._streak = max(self._streak, streak)
        self._last_seen_at = max(self._last_seen_at, seen_at)

    def _read_carried_state(self, path: Path) -> tuple[str, int, float] | None:
        """Read the carried record as ``(signature, streak, local_clock)``.

        The on-disk timestamp is a WALL-clock reading because
        ``time.monotonic`` is not comparable between processes; it is
        converted back into this tracker's clock domain here so the
        window comparison in :meth:`_observe_in_memory` stays
        clock-agnostic. Any unreadable, truncated or wrong-typed record
        is treated as absent: a corrupt file must never fabricate a trip.
        """
        try:
            with path.open("rb") as handle:
                raw = handle.read(_STATE_MAX_BYTES)
        except OSError:
            return None
        record = _decode_carried_state(raw)
        if record is None:
            return None
        signature, streak, seen_wall = record
        age = max(0.0, _time.time() - seen_wall)
        return signature, streak, self._clock() - age

    def _write_carried_state(self, path: Path) -> None:
        """Publish the streak for the next child.

        Written via a temp file plus ``Path.replace`` so a reader in
        another process never observes a half-written record. Called
        with the cross-process lock held.
        """
        payload = {
            "last_signature": self._last_signature,
            "streak": self._streak,
            "last_seen_wall": _time.time(),
        }
        tmp = path.with_name(path.name + ".tmp")
        try:
            # filesystem-write-ok: carried-streak record published by tmp-then-replace so a concurrent child never reads a partial object
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
            tmp.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def reset(self) -> None:
        """Clear the streak. Used by tests and by recovery handlers."""
        with self._lock:
            self._last_signature = None
            self._streak = 0
            self._last_seen_at = 0.0
            if self.state_path is not None:
                with contextlib.suppress(OSError):
                    self.state_path.unlink()

    def snapshot(self) -> dict[str, object]:
        """Return the current in-memory state for diagnostics.

        A tracker built by :meth:`from_parent_owned_state` adopts the
        carried record on its first :meth:`observe`, so this reflects the
        previous child's streak from then on.
        """
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
    "REPETITION_STATE_FILENAME",
    "THRESHOLD",
    "WINDOW_SECONDS",
    "TransportRepetitionTracker",
    "failure_signature",
    "is_infrastructure_fault",
    "repetition_state_path",
    "request_fingerprint",
    "signature_for",
]
