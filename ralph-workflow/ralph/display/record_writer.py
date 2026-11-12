from __future__ import annotations

import contextlib
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from ralph.display.line_sanitizer import strip_terminal_control

_DEFAULT_BUFFER_CAP: Final[int] = 512

_BUFFER_FLUSH_THRESHOLD: Final[int] = 480

_HHMMSS_LEN: Final[int] = 8

_INDENT_WIDTH: Final[int] = 2


@dataclass(frozen=True)
class _SafeId:
    raw: str

    @property
    def value(self) -> str:
        out: list[str] = []
        prev_underscore = False
        for ch in self.raw.strip():
            if ch.isalnum() or ch in ".-":
                out.append(ch)
                prev_underscore = False
            elif not prev_underscore:
                out.append("_")
                prev_underscore = True
        safe = "".join(out).strip("_")
        return safe or "unknown"


def safe_id_is_lossless(raw: str) -> bool:
    if not raw:
        return False
    return _SafeId(raw).value == raw


def safe_id_for(agent: str, model: str | None = None) -> str:
    if model:
        return _SafeId(f"{agent}_{model}").value
    return _SafeId(agent).value


def rendered_record_path(workspace_root: Path, agent: str, model: str | None = None) -> Path:
    root = Path(workspace_root) / ".agent" / "raw"
    return root / f"{safe_id_for(agent, model)}.rendered.log"


def _format_entry_line(entry: object) -> str:
    timestamp = _strip_ansi(_extract_field(entry, "timestamp"))
    phase = _strip_ansi(_extract_field(entry, "phase"))
    cycle = _strip_ansi(_extract_field(entry, "cycle"))
    iter_ = _strip_ansi(_extract_field(entry, "iter"))
    agent = _strip_ansi(_extract_field(entry, "agent"))
    severity = _strip_ansi(_extract_field(entry, "severity"))
    body = _extract_field(entry, "body")
    indent_level = _extract_indent_level(entry)
    grouping_role = _strip_ansi(_extract_field(entry, "grouping_role")) or "agent_text"

    hh_mm_ss = _to_hh_mm_ss(timestamp)
    indent_prefix = " " * (_INDENT_WIDTH * max(0, indent_level))
    parts = [f"[{hh_mm_ss}]"]
    if grouping_role == "phase_header":
        if phase:
            from ralph.display.parallel_display import phase_label

            parts.append(phase_label(phase))
        if cycle:
            parts.append(f"cycle={cycle}")
        if iter_:
            parts.append(f"iter={iter_}")
        if agent:
            parts.append(f"agent={agent}")
    else:
        if severity and severity != "info":
            parts.append(f"severity={severity}")
        body_str = _flatten_body(body)
        if body_str:
            parts.append(body_str)
    parts.append(f"role={grouping_role}")
    first_line = indent_prefix + " ".join(parts)
    continuation_prefix = " " * (_INDENT_WIDTH * (max(0, indent_level) + 1))
    body_lines = _body_lines(body)
    return "\n".join((first_line, *(continuation_prefix + line for line in body_lines[1:])))


def _strip_ansi(value: object) -> str:
    if value is None:
        return ""
    try:
        return strip_terminal_control(str(value))
    except Exception:
        return ""


def _extract_field(entry: object, name: str) -> object:
    if entry is None:
        return None
    if isinstance(entry, dict):
        value: object = entry.get(name)
        return value
    raw: object = getattr(entry, name, None)
    return raw


def _extract_indent_level(entry: object) -> int:
    raw = _extract_field(entry, "indent_level")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    return 0


def _to_hh_mm_ss(timestamp: object) -> str:
    if not timestamp:
        return "??:??:??"
    try:
        text = str(timestamp)
    except Exception:
        return "??:??:??"
    try:
        head = text.split("T", 1)[1] if "T" in text else text
        time_part = head.split("+", 1)[0].split("-", 1)[0].split("Z", 1)[0]
        hh_mm_ss = time_part[:_HHMMSS_LEN]
        if len(hh_mm_ss) == _HHMMSS_LEN and hh_mm_ss[2] == ":" and hh_mm_ss[5] == ":":
            return hh_mm_ss
    except Exception:
        pass
    return "??:??:??"


def _body_lines(body: object) -> tuple[str, ...]:
    if body is None:
        return ()
    text = (
        body
        if isinstance(body, str)
        else " ".join(str(item) for item in body)
        if isinstance(body, Iterable)
        else str(body)
    )
    return tuple(
        strip_terminal_control(line.replace("\t", " ")).rstrip()
        for line in text.replace("\r\n", "\n").split("\n")
    )


def _flatten_body(body: object) -> str:
    return _body_lines(body)[0].strip() if _body_lines(body) else ""


class RenderedRecordWriter:
    __slots__ = (
        "_agent",
        "_buffer",
        "_disabled",
        "_lock",
        "_model",
        "_on_error",
        "_path",
        "_workspace_root",
    )

    def __init__(
        self,
        workspace_root: Path,
        agent: str,
        *,
        model: str | None = None,
        on_error: Callable[[Exception], object] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._agent = agent
        self._model = model
        self._path = rendered_record_path(self._workspace_root, agent, model)
        self._buffer: deque[str] = deque(maxlen=512)
        self._disabled = False
        self._lock = threading.Lock()
        self._on_error = on_error

    @property
    def path(self) -> Path:
        return self._path

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def pending_lines(self) -> int:
        return len(self._buffer)

    @property
    def buffer_capacity(self) -> int:
        return self._buffer.maxlen or _DEFAULT_BUFFER_CAP

    def append(self, entry: object) -> None:
        line = _format_entry_line(entry)
        should_flush = False
        with self._lock:
            if self._disabled:
                return
            self._buffer.append(line)
            if len(self._buffer) >= _BUFFER_FLUSH_THRESHOLD:
                should_flush = True
        if should_flush:
            self.flush()

    def extend(self, entries: Iterable[object]) -> None:
        for entry in entries:
            self.append(entry)

    def flush(self) -> int:
        error: OSError | None = None
        with self._lock:
            if self._disabled or not self._buffer:
                return 0
            lines = list(self._buffer)
            self._buffer.clear()
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fp:
                    fp.write("\n".join(lines))
                    fp.write("\n")
            except OSError as exc:
                self._disabled = True
                error = exc
        if error is not None:
            if self._on_error is not None:
                with contextlib.suppress(Exception):
                    self._on_error(error)
            return 0
        return len(lines)

    def disable(self) -> None:
        with self._lock:
            self._disabled = True
            self._buffer.clear()

    def _set_path_for_testing(self, path: Path) -> None:
        self._path = path


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


__all__ = [
    "RenderedRecordWriter",
    "rendered_record_path",
    "safe_id_for",
    "safe_id_is_lossless",
    "utc_now_iso",
]
