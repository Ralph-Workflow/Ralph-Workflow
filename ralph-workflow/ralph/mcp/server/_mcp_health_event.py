
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ralph.agents.invoke._watch_capacity import (
    _read_inotify_watch_limit,
    _read_inotify_watch_user_total,
    call_within_budget,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DECISIONS: deque[RestartDecision] = deque(maxlen=64)

_DURABLE_SINK_LOCK: threading.Lock = threading.Lock()
_DURABLE_SINK_HOLDER: list[Path | None] = [None]


class RestartReason(Enum):

    PROCESS_EXITED = "process_exited"
    PROBE_FAILED = "probe_failed"
    PROBE_DEFERRED_CONTENTION = "probe_deferred_contention"


@dataclass(frozen=True)
class RestartDecision:

    reason: RestartReason
    exit_status: int | None = None
    probe_duration_s: float | None = None
    log_tail: tuple[str, ...] | None = None
    stage: str = ""
    available: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.reason, str):
            try:
                object.__setattr__(self, "reason", RestartReason(self.reason))
            except ValueError:
                raise ValueError(
                    f"Unknown restart reason: {self.reason!r}. Must be one of {[r.value for r in RestartReason]}"
                ) from None
        if self.reason is RestartReason.PROCESS_EXITED and self.exit_status is None:
            raise ValueError("exit_status is required when reason is PROCESS_EXITED")
        if self.reason is RestartReason.PROBE_FAILED and self.probe_duration_s is None:
            raise ValueError("probe_duration_s is required when reason is PROBE_FAILED")
        if self.log_tail is not None and len(self.log_tail) > _LOG_TAIL_LINES:
            object.__setattr__(self, "log_tail", self.log_tail[-_LOG_TAIL_LINES:])


_LOG_TAIL_LINES = 10


def set_durable_sink_path(path: Path | None) -> None:
    _DURABLE_SINK_HOLDER[0] = path


def get_durable_sink_path() -> Path | None:
    return _DURABLE_SINK_HOLDER[0]


def record_restart_decision(
    *,
    reason: RestartReason | str,
    exit_status: int | None = None,
    probe_duration_s: float | None = None,
    log_tail: tuple[str, ...] | None = None,
    stage: str,
    sink_path: Path | None = None,
) -> None:
    resolved_reason = RestartReason(reason) if isinstance(reason, str) else reason
    decision = RestartDecision(
        reason=resolved_reason,
        exit_status=exit_status,
        probe_duration_s=probe_duration_s,
        log_tail=log_tail,
        stage=stage,
    )
    _DECISIONS.append(decision)
    target = sink_path if sink_path is not None else _DURABLE_SINK_HOLDER[0]
    if target is not None:
        try:
            with _DURABLE_SINK_LOCK:
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    target.open(
                        "a", encoding="utf-8"
                    ) as fh
                ):
                    record: dict[str, object] = {
                        "reason": decision.reason.value,
                        "exit_status": decision.exit_status,
                        "probe_duration_s": decision.probe_duration_s,
                        "log_tail": (
                            list(decision.log_tail) if decision.log_tail is not None else None
                        ),
                        "stage": decision.stage,
                    }
                    fh.write(json.dumps(record))
                    fh.write("\n")
        except OSError:
            pass


_EVENTS: deque[tuple[str, bool]] = deque(maxlen=64)


def record_stage(stage: str, *, available: bool) -> None:
    _EVENTS.append((stage, available))


def get_restart_decisions() -> tuple[RestartDecision, ...]:
    return tuple(_DECISIONS)


def read_durable_sink(path: Path | None = None) -> tuple[dict[str, object], ...]:
    sink_path = path if path is not None else _DURABLE_SINK_HOLDER[0]
    if sink_path is None or not sink_path.exists():
        return ()
    try:
        lines: list[str] = []
        with sink_path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                lines.append(line)
    except OSError as exc:
        return ({"reason": "unavailable", "stage": str(exc)},)
    parsed: list[dict[str, object]] = []
    for line in lines:
        try:
            obj: object = json.loads(line)
        except json.JSONDecodeError:
            return ({"reason": "unavailable", "stage": "json_decode_failed"},)
        if not isinstance(obj, dict):
            return ({"reason": "unavailable", "stage": "json_decode_failed"},)
        parsed.append(obj)
    return tuple(parsed)


def clear_durable_sink(path: Path | None = None) -> None:
    sink_path = path if path is not None else _DURABLE_SINK_HOLDER[0]
    if sink_path is None:
        return
    try:
        sink_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def configure_bridge_sink(root: Path, bridge_id: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9_-]+", bridge_id):
        safe_id = bridge_id
    else:
        safe_id = hashlib.sha256(bridge_id.encode("utf-8")).hexdigest()[:16]
    sink_path = root / ".agent" / f"mcp-health-events-{safe_id}.jsonl"
    set_durable_sink_path(sink_path)
    return sink_path


_HOST_CONTENTION_SIGNALS: deque[tuple[float, str]] = deque(maxlen=64)

CONTENDED_WATCH_RATIO: float = 0.85

if not (0.0 < CONTENDED_WATCH_RATIO < 1.0):
    raise RuntimeError(f"CONTENDED_WATCH_RATIO must be in (0.0, 1.0) (got {CONTENDED_WATCH_RATIO})")


def record_host_contention_signal(
    reason: str,
    *,
    _now: Callable[[], float] | None = None,
) -> None:
    now_fn: Callable[[], float] = _now if _now is not None else time.monotonic
    _HOST_CONTENTION_SIGNALS.append((now_fn(), reason))


def pop_recent_host_contention_signals(
    window_s: float,
    *,
    _now: Callable[[], float] | None = None,
) -> tuple[tuple[float, str], ...]:
    now = (_now if _now is not None else time.monotonic)()
    return tuple((ts, reason) for ts, reason in _HOST_CONTENTION_SIGNALS if now - ts <= window_s)


def pop_recent_host_contention_signals_extended(
    window_s: float,
    *,
    _now: Callable[[], float] | None = None,
) -> tuple[tuple[float, str], ...]:
    now = (_now if _now is not None else time.monotonic)()
    kept: deque[tuple[float, str]] = deque(
        (ts, reason) for ts, reason in _HOST_CONTENTION_SIGNALS if now - ts <= window_s
    )
    _HOST_CONTENTION_SIGNALS.clear()
    for item in kept:
        _HOST_CONTENTION_SIGNALS.append(item)
    return tuple(kept)


def clear_host_contention_signals() -> None:
    _HOST_CONTENTION_SIGNALS.clear()


_clear_host_contention_signals = clear_host_contention_signals


def current_host_is_contended(
    *,
    budget_seconds: float = 1.0,
    watch_limit_reader: Callable[[], int | None] | None = None,
    watch_user_total_reader: Callable[[], int | None] | None = None,
) -> bool:
    limit_fn = watch_limit_reader if watch_limit_reader is not None else _read_inotify_watch_limit
    user_total_fn = (
        watch_user_total_reader
        if watch_user_total_reader is not None
        else _read_inotify_watch_user_total
    )

    def _probe() -> bool:
        limit = limit_fn()
        if limit is None or limit <= 0:
            return False
        total = user_total_fn()
        if total is None:
            return False
        return (total / limit) >= CONTENDED_WATCH_RATIO

    return call_within_budget(_probe, fallback=False, budget_seconds=budget_seconds)


clear_host_contention_signals = _clear_host_contention_signals

__all__ = [
    "CONTENDED_WATCH_RATIO",
    "RestartDecision",
    "RestartReason",
    "clear_durable_sink",
    "clear_host_contention_signals",
    "configure_bridge_sink",
    "current_host_is_contended",
    "get_durable_sink_path",
    "get_restart_decisions",
    "pop_recent_host_contention_signals",
    "pop_recent_host_contention_signals_extended",
    "read_durable_sink",
    "record_host_contention_signal",
    "record_restart_decision",
    "record_stage",
    "set_durable_sink_path",
]
