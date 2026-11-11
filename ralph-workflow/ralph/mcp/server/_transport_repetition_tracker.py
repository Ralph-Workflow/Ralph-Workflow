
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

WINDOW_SECONDS: float = 60.0

THRESHOLD: int = 3

BREAKER_STATUS: int = 503

BREAKER_CODE: int = -32001

BREAKER_MESSAGE: str = "transport_loop_detected"

REPETITION_STATE_FILENAME: str = "transport-repetition-state.json"

_STATE_LOCK_TIMEOUT_S: float = 0.2

_STATE_MAX_BYTES: int = 8192

if not _STATE_LOCK_TIMEOUT_S > 0:
    raise RuntimeError(f"_STATE_LOCK_TIMEOUT_S must be positive (got {_STATE_LOCK_TIMEOUT_S})")

if not _STATE_MAX_BYTES > 0:
    raise RuntimeError(f"_STATE_MAX_BYTES must be positive (got {_STATE_MAX_BYTES})")

_VOLATILE_UUID_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
_VOLATILE_TIMESTAMP = re.compile(
    r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\b"
    r"|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_VOLATILE_REQUEST_ID = re.compile(r"\brequest[_-]?id[=:]\s*\S+", re.IGNORECASE)


def signature_for(exc: BaseException | str) -> str:
    text = f"{type(exc).__name__}:{exc}" if isinstance(exc, BaseException) else exc
    text = _VOLATILE_UUID_HEX.sub("<uuid>", text)
    text = _VOLATILE_TIMESTAMP.sub("<ts>", text)
    text = _VOLATILE_REQUEST_ID.sub("request_id=<id>", text)
    return text.lower()


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
    lowered = text.lower()
    return any(marker in lowered for marker in INFRASTRUCTURE_FAULT_MARKERS)


def _canonical_params(params: object) -> str:
    try:
        return json.dumps(params, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        return repr(params)


def request_fingerprint(method: str, params: object) -> str:
    canonical = signature_for(f"{method}\x1f{_canonical_params(params)}")
    digest = hashlib.blake2b(canonical.encode("utf-8", "replace"), digest_size=16)
    return f"{method}#{digest.hexdigest()}"


def failure_signature(method: str, params: object, failure: BaseException | str) -> str | None:
    signature = signature_for(failure)
    if is_infrastructure_fault(signature):
        return None
    return f"{request_fingerprint(method, params)}|{signature}"


def repetition_state_path(env: Mapping[str, str] | None = None) -> Path | None:
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(MCP_SNAPSHOT_DIR_ENV)
    if not raw:
        return None
    return Path(raw) / REPETITION_STATE_FILENAME


def _acquire_state_lock(handle: IO[str], timeout: float) -> bool:
    deadline = _time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if _time.monotonic() >= deadline:
                return False
            _time.sleep(0.01)
        except OSError:
            return False


def _release_state_lock(handle: IO[str]) -> None:
    with contextlib.suppress(OSError, ValueError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _decode_carried_state(raw: bytes) -> tuple[str, int, float] | None:
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
        return cls(
            window_seconds=window_seconds,
            threshold=threshold,
            state_path=repetition_state_path(env),
        )

    def observe(self, signature: str) -> bool:
        with self._lock:
            path = self.state_path
            if path is None:
                return self._observe_in_memory(signature)
            return self._observe_with_carried_state(signature, path)

    def _observe_in_memory(self, signature: str) -> bool:
        now = self._clock()
        if self._last_signature == signature and (now - self._last_seen_at) <= self.window_seconds:
            self._streak += 1
        else:
            self._last_signature = signature
            self._streak = 1
        self._last_seen_at = now
        return self._streak >= self.threshold

    def _observe_with_carried_state(self, signature: str, path: Path) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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
        payload = {
            "last_signature": self._last_signature,
            "streak": self._streak,
            "last_seen_wall": _time.time(),
        }
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
            tmp.replace(path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def reset(self) -> None:
        with self._lock:
            self._last_signature = None
            self._streak = 0
            self._last_seen_at = 0.0
            if self.state_path is not None:
                with contextlib.suppress(OSError):
                    self.state_path.unlink()

    def snapshot(self) -> dict[str, object]:
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
