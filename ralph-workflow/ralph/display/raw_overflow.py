from __future__ import annotations

import atexit
import contextlib
import hashlib
import os
import re
import shlex
import threading
import time
import weakref
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, BinaryIO, Final, cast

from loguru import logger

from ralph.display._raw_log_break import RawLogBreak
from ralph.display.raw_log_breaks import (
    HARNESS_PTY_INPUT_ECHO_LINES,
    MAX_REPORTED_BREAKS,
    PTY_EXIT_COMMAND,
    TURN_BOUNDARY_MARKER,
    detect_raw_log_breaks,
    is_harness_input_echo,
    is_interactive_pty_transport,
    iter_capture_lines,
    nul_separated_chunks,
)
from ralph.display.record_writer import safe_id_for

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import AgentConfig

_ID_DIGEST_CHARS: Final = 6

_UNSAFE_ID_RUN: Final = re.compile(r"[^0-9A-Za-z._-]+")

DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024
_BUFFER_BYTES = 64 * 1024
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
CONDENSED_LOG_SUFFIX: Final = ".overflow"


_REGISTRY_LOCK = threading.Lock()
_REGISTRY: weakref.WeakValueDictionary[str, RawOverflowLog] = weakref.WeakValueDictionary()


_PATH_STATE_LOCK = threading.Lock()
_PATH_STATE: dict[str, int] = {}
_CAP_WARNED: set[str] = set()


def _resume_path_state(key: str) -> tuple[bool, int]:
    with _PATH_STATE_LOCK:
        if key not in _PATH_STATE:
            return True, 0
        return False, _PATH_STATE[key]


_PROCESS_STARTED_AT: Final = time.time()

_OUR_PATHS: set[str] = set()
_OUR_PATHS_LOCK = threading.Lock()


def _claim_path_for_this_process(key: str) -> bool:
    with _OUR_PATHS_LOCK:
        seen = key in _OUR_PATHS
        _OUR_PATHS.add(key)
        return seen


def _another_process_is_writing(path: Path) -> bool:
    try:
        return path.stat().st_mtime >= _PROCESS_STARTED_AT
    except OSError:
        return False


def _is_live_foreign_capture(path: Path, key: str) -> bool:
    if _claim_path_for_this_process(key):
        return False
    if path.exists():
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return True
    except OSError:
        return False
    else:
        os.close(fd)
        return False


def _record_path_bytes(key: str, total: int) -> None:
    with _PATH_STATE_LOCK:
        _PATH_STATE[key] = total


def _claim_cap_warning(key: str) -> bool:
    with _PATH_STATE_LOCK:
        if key in _CAP_WARNED:
            return False
        _CAP_WARNED.add(key)
        return True


def reset_raw_overflow_path_state() -> None:
    with _REGISTRY_LOCK:
        stale = list(_REGISTRY.values())
        _REGISTRY.clear()
    for writer in stale:
        with contextlib.suppress(Exception):
            writer.disable()
    with _PATH_STATE_LOCK:
        _PATH_STATE.clear()
        _CAP_WARNED.clear()


_DISPATCHER_EXECUTABLES: Final = frozenset({"ccs"})


def _signature_token(raw: object) -> str:
    enum_value = cast("object", getattr(raw, "value", None))
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    return repr(raw)


def _invocation_signature(config: AgentConfig, model: str | None) -> str:
    fields = sorted(type(config).model_fields)
    parts = [
        f"{name}={_signature_token(cast('object', getattr(config, name, None)))}" for name in fields
    ]
    parts.append(f"resolved_model={model or ''}")
    return "\x00".join(parts)


def _digest_of(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:_ID_DIGEST_CHARS]


def _model_from_flag(config: AgentConfig, model: str | None) -> str:
    raw = cast("object", getattr(config, "model_flag", None))
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return ""
    values: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            _, separator, tail = token.partition("=")
            if separator and tail:
                values.append(tail)
            continue
        values.append(token)
    distinguishing = [value for value in values if value != model]
    if not distinguishing:
        return ""
    readable = _UNSAFE_ID_RUN.sub("-", "-".join(distinguishing)).strip("-")
    digest = _digest_of(raw)
    return f"{readable}-{digest}" if readable else digest


def raw_log_unit_id_for(config: AgentConfig) -> str:
    try:
        tokens = shlex.split(config.cmd)
    except ValueError:
        return ""
    if not tokens:
        return ""
    raw_model = cast("object", getattr(config, "model", None))
    model = raw_model if isinstance(raw_model, str) and raw_model.strip() else None
    executable = PurePosixPath(tokens[0]).name or tokens[0]
    flags = set(tokens[1:]) | set((config.output_flag or "").split())
    flag_model = _model_from_flag(config, model)

    signature = _invocation_signature(config, model)

    def identified(base: str) -> str:
        readable = f"{base}-{flag_model}" if flag_model else base
        return f"{readable}-{_digest_of(signature)}"

    if executable.lower() == "claude" and ("-p" in flags or "--output-format=stream-json" in flags):
        return identified("claude-headless")
    if executable.lower() in _DISPATCHER_EXECUTABLES:
        alias = next((token for token in tokens[1:] if not token.startswith("-")), "")
        if alias:
            return identified(f"{executable}-{alias}")
    return identified(executable)


def raw_log_path_for(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    condensed: bool = False,
) -> Path:
    suffix = CONDENSED_LOG_SUFFIX if condensed else ""
    return workspace_root / ".agent" / "raw" / f"{safe_id_for(unit_id, model)}{suffix}.log"


def get_or_create_raw_overflow_log(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    condensed: bool = False,
    max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> RawOverflowLog:
    key_path = raw_log_path_for(workspace_root, unit_id, model=model, condensed=condensed)
    key = str(key_path.resolve(strict=False))
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing
        instance = RawOverflowLog(
            workspace_root,
            unit_id,
            model=model,
            condensed=condensed,
            max_bytes=max_bytes,
            flush_interval_seconds=flush_interval_seconds,
            now=now,
        )
        _REGISTRY[key] = instance
        return instance


def _forget_raw_overflow_log(key_path: str) -> None:
    with _REGISTRY_LOCK:
        _REGISTRY.pop(key_path, None)


def _finalize_close_raw_overflow_log(handle_box: list[BinaryIO | None]) -> None:
    handle = handle_box[0]
    if handle is None:
        return
    handle_box[0] = None
    with contextlib.suppress(OSError, ValueError):
        handle.close()


def close_all_raw_overflow_logs() -> None:
    snapshot: list[RawOverflowLog] = []
    with _REGISTRY_LOCK:
        snapshot = list(_REGISTRY.values())
    for instance in snapshot:
        with contextlib.suppress(Exception):
            instance.close()
        with contextlib.suppress(Exception):
            instance.disable()


atexit.register(close_all_raw_overflow_logs)


class RawOverflowLog:
    def __init__(
        self,
        workspace_root: Path,
        unit_id: str,
        *,
        model: str | None = None,
        condensed: bool = False,
        max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = raw_log_path_for(workspace_root, unit_id, model=model, condensed=condensed)
        self._lock = threading.Lock()
        self._path_key = str(self.path.resolve(strict=False))
        first_open, already_written = _resume_path_state(self._path_key)
        self._first_write = first_open
        self._needs_live_claim = first_open
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        self._bytes_written = 0
        self._file_bytes = already_written
        self._flush_interval = max(flush_interval_seconds, 0.0)
        self._now = now
        self._handle_box: list[BinaryIO | None] = [None]
        self._last_flush = now()
        weakref.finalize(
            self,
            _finalize_close_raw_overflow_log,
            self._handle_box,
        )

    @property
    def _fh(self) -> BinaryIO | None:
        return self._handle_box[0]

    @_fh.setter
    def _fh(self, handle: BinaryIO | None) -> None:
        self._handle_box[0] = handle

    @property
    def is_open(self) -> bool:
        handle = self._handle_box[0]
        return handle is not None and not handle.closed

    def disable(self) -> None:
        with self._lock:
            self._close_locked()
            self._disabled = True

    def append_bytes(self, raw: bytes, *, counts_as_liveness: bool = True) -> bool:
        return self._write(raw.rstrip(b"\n") + b"\n", counts_as_liveness=counts_as_liveness)

    def append(self, line: str, *, counts_as_liveness: bool = True) -> bool:
        with self._lock:
            if self._disabled:
                return False
        return self._write(
            (line.rstrip("\n") + "\n").encode("utf-8"),
            counts_as_liveness=counts_as_liveness,
        )

    def _write(self, encoded: bytes, *, counts_as_liveness: bool) -> bool:
        with self._lock:
            if self._disabled:
                return False
            try:
                if self._file_bytes + len(encoded) > self._max_bytes:
                    self._close_locked()
                    self._disabled = True
                    if _claim_cap_warning(self._path_key):
                        logger.warning(
                            "raw log {path} reached its {cap}-byte cap and is no "
                            "longer being written; the remainder of this unit's "
                            "output is not captured",
                            path=self.path,
                            cap=self._max_bytes,
                        )
                    return False
                if self._fh is None:
                    if self._needs_live_claim:
                        self._first_write = not _is_live_foreign_capture(self.path, self._path_key)
                        self._needs_live_claim = False
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    if not self._first_write and not self.path.exists():
                        self._file_bytes = 0
                    mode = "wb" if self._first_write else "ab"
                    handle_obj: object = self.path.open(mode, buffering=_BUFFER_BYTES)
                    self._fh = cast("BinaryIO", handle_obj)
                    self._first_write = False
                fh: BinaryIO | None = self._fh
                if fh is None:
                    return False
                fh.write(encoded)
                if counts_as_liveness:
                    self._bytes_written += len(encoded)
                self._file_bytes += len(encoded)
                _record_path_bytes(self._path_key, self._file_bytes)
                if self._now() - self._last_flush >= self._flush_interval:
                    fh.flush()
                    self._last_flush = self._now()
                return True
            except (OSError, PermissionError):
                self._close_locked()
                self._disabled = True
                return False

    def flush(self) -> None:
        with self._lock:
            handle = self._fh
            if handle is not None and self.is_open:
                try:
                    handle.flush()
                    self._last_flush = self._now()
                except (OSError, PermissionError):
                    self._close_locked()
                    self._disabled = True

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError, PermissionError):
                self._fh.close()
            self._fh = None

    def relative_reference(self, workspace_root: Path) -> str:
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()

    @property
    def size_bytes(self) -> int:
        return self._bytes_written

    @property
    def is_disabled(self) -> bool:
        return self._disabled


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_MAX_OVERFLOW_FILE_BYTES",
    "HARNESS_PTY_INPUT_ECHO_LINES",
    "MAX_REPORTED_BREAKS",
    "PTY_EXIT_COMMAND",
    "TURN_BOUNDARY_MARKER",
    "RawLogBreak",
    "RawOverflowLog",
    "close_all_raw_overflow_logs",
    "detect_raw_log_breaks",
    "is_harness_input_echo",
    "is_interactive_pty_transport",
    "iter_capture_lines",
    "nul_separated_chunks",
    "raw_log_path_for",
]
