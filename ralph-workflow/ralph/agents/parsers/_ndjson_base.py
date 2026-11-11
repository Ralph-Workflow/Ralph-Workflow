
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ._event_classification import is_lifecycle_event
from ._template import ParserTemplateBase
from .agent_output_line import AgentOutputLine
from .base import extract_error_message

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Literal

    from ralph.agents.idle_watchdog import SubagentPidRegistry

    _SubagentSourceLabel = Literal[
        "opencode",
        "claude",
        "pi",
        "agy",
        "generic",
        "claude_interactive",
        "codex",
        "nanocoder",
        "gemini",
        "cursor",
        "kimi",
    ]


def _extract_pid_from_obj(obj: dict[str, object]) -> int | None:
    for key in ("pid", "child_pid", "subagent_pid"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
    nested_meta = obj.get("metadata")
    if isinstance(nested_meta, dict):
        nested_pid = _extract_pid_from_obj(cast("dict[str, object]", nested_meta))
        if nested_pid is not None:
            return nested_pid
    part = obj.get("part")
    if isinstance(part, dict):
        state = cast("dict[str, object]", part).get("state")
        if isinstance(state, dict):
            state_pid = _extract_pid_from_obj(cast("dict[str, object]", state))
            if state_pid is not None:
                return state_pid
    return None


__all__ = ["NdjsonParserBase"]


def _extract_source_timestamp(obj: dict[str, object]) -> str | None:
    for key in ("timestamp", "time", "ts"):
        raw = obj.get(key)
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int | float):
            epoch = _epoch_to_iso(float(raw))
            if epoch is not None:
                return epoch
            continue
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = raw.strip()
        try:
            datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            continue
        return candidate
    return None


_EPOCH_MILLISECONDS_BOUND = 1e11

_EPOCH_SECONDS_FLOOR = 946_684_800.0


def _epoch_to_iso(value: float) -> str | None:
    seconds = value / 1000.0 if abs(value) >= _EPOCH_MILLISECONDS_BOUND else value
    if seconds < _EPOCH_SECONDS_FLOOR:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


class NdjsonParserBase(ParserTemplateBase):

    def __init__(self) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = None
        self._subagent_source_label: str | None = None

    def _try_register_subagent_pid_from_obj(self, obj: dict[str, object]) -> None:
        registry = self._subagent_pid_registry
        if registry is None:
            return
        if self._subagent_source_label is None:
            return
        pid = _extract_pid_from_obj(obj)
        if pid is None or pid <= 0:
            return
        try:
            registry.register(
                pid,
                source=cast(
                    "_SubagentSourceLabel", self._subagent_source_label
                ),
            )
        except ValueError:
            return

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = self._strip_data_prefix(line)
        if not stripped:
            return

        yield from self._classify_after_strip(stripped)

    def _strip_data_prefix(self, line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ""
        if stripped.startswith("data:"):
            return stripped[5:].strip()
        return stripped

    def _classify_after_strip(self, stripped: str) -> Iterator[AgentOutputLine]:
        if stripped == "[DONE]":
            yield AgentOutputLine(type="stop", raw=stripped)
            return

        try:
            parsed: object = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            yield from self._classify_non_json_line(stripped)
            return

        if not isinstance(parsed, dict):
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
            return

        yield from self._classify_parsed_json_object(cast("dict[str, object]", parsed), stripped)

    def _classify_parsed_json_object(
        self, obj: dict[str, object], stripped: str
    ) -> Iterator[AgentOutputLine]:
        source_timestamp = _extract_source_timestamp(obj)

        self._try_register_subagent_pid_from_obj(obj)

        if "error" in obj:
            yield from self._flush_before_error(obj, stripped)
            error_msg = extract_error_message(obj)
            yield AgentOutputLine(
                type="error",
                content=error_msg,
                raw=stripped,
                metadata=obj,
                timestamp=source_timestamp,
            )
            return

        event_type = str(obj.get("type", ""))
        lifecycle_result: Iterator[AgentOutputLine] | None = None
        if event_type == "content_block_start" and isinstance(obj.get("content_block"), dict):
            pass
        elif event_type and is_lifecycle_event(event_type):
            lifecycle_result = self._handle_lifecycle_event(obj, event_type)

        yield from self._dispatch_with_timestamp(obj, stripped, source_timestamp, lifecycle_result)

    def _dispatch_with_timestamp(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None,
        lifecycle_result: Iterator[AgentOutputLine] | None,
    ) -> Iterator[AgentOutputLine]:
        if lifecycle_result is not None:
            yielded = lifecycle_result
        else:
            yielded = self._dispatch_json_object(obj, raw, source_timestamp)
        if source_timestamp is None:
            yield from yielded
            return
        for line in yielded:
            if isinstance(line, AgentOutputLine) and line.timestamp is None:
                yield replace(line, timestamp=source_timestamp)
            else:
                yield line

    def _flush_before_error(
        self,
        _obj: dict[str, object],
        _raw: str,
    ) -> Iterator[AgentOutputLine]:
        return iter(())

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        return iter(())

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))
        yield AgentOutputLine(
            type=event_type,
            raw=raw,
            metadata=obj,
            timestamp=source_timestamp,
        )

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=stripped, raw=stripped)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        return iter(())
