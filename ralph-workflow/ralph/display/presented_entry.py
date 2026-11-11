
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.display._channel_prefix_stripper import (
    strip_parser_channel_prefix,
)
from ralph.display._tool_correlation import tool_call_id
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.tool_args import friendly_tool_name

if TYPE_CHECKING:
    from ralph.display.agent_activity_event import AgentActivityEvent


@dataclass(frozen=True)
class PresentedEntry:

    kind: str
    severity: str
    identity: str
    body: str
    timestamp: str | None = None
    phase: str | None = None
    cycle: int | None = None
    iter: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    indent_level: int = 0
    grouping_role: str = "agent_text"

    @property
    def agent(self) -> str:
        return self.identity


def build_presented_entry(
    event: AgentActivityEvent,
    *,
    unit_id: str | None = None,
    timestamp: str | None = None,
    phase: str | None = None,
    cycle: int | None = None,
    iter_: str | None = None,
) -> PresentedEntry:
    identity = unit_id or ""
    metadata: dict[str, object] = {}
    body = ""
    if event.content is not None:
        body = str(event.content)
    body = _strip_live_chrome(
        strip_parser_channel_prefix(body),
        identity,
        preserve_before_pair=event.kind is ActivityEventKind.TOOL_USE,
    )
    if event.kind in {ActivityEventKind.TEXT, ActivityEventKind.THINKING}:
        body = _strip_markdown_emphasis(body)
    if event.metadata:
        metadata.update(event.metadata)
    severity = _derive_severity(event.kind, metadata)
    if event.kind is ActivityEventKind.TOOL_USE:
        body = _tool_call_record_body(body, metadata)
    if event.kind is ActivityEventKind.TOOL_RESULT:
        body = _tool_result_record_body(body, metadata, severity)
    if identity and event.kind in {ActivityEventKind.STATUS, ActivityEventKind.UNKNOWN}:
        body = body.removeprefix(f"{identity} ")
    grouping_role, indent_level = _KIND_TO_GROUPING.get(event.kind, ("agent_text", 0))
    effective_timestamp = timestamp
    if not effective_timestamp:
        event_timestamp = event.timestamp
        if isinstance(event_timestamp, str) and event_timestamp:
            effective_timestamp = event_timestamp
    return PresentedEntry(
        kind=event.kind.value if hasattr(event.kind, "value") else str(event.kind),
        severity=severity,
        identity=identity,
        body=body,
        timestamp=effective_timestamp,
        phase=phase,
        cycle=cycle,
        iter=iter_,
        metadata=metadata,
        indent_level=indent_level,
        grouping_role=grouping_role,
    )


def _strip_markdown_emphasis(body: str) -> str:
    if not body:
        return body

    def unwrapped(match: re.Match[str]) -> str:
        return match.group(2) or match.group(4)

    return re.sub(
        r"(?<!\w)(\*\*|__)(.+?)\1(?!\w)|(?<!\w)([*_])(.+?)\3(?!\w)",
        unwrapped,
        re.sub(r"^#{1,6}\s+", "", body),
    ).lstrip()


def _tool_result_record_body(body: str, metadata: dict[str, object], severity: str) -> str:
    raw_tool = next(
        (
            value
            for key in ("tool_name", "name", "tool")
            if isinstance(value := metadata.get(key), str) and value
        ),
        "tool",
    )
    tool = friendly_tool_name(raw_tool)
    outcome = "failed" if severity == "error" else "ok"
    body = _strip_leading_tokens(body, tool, outcome)
    if tool_call_id(metadata):
        return " ".join(part for part in (outcome, body) if part)
    return " ".join(part for part in (tool, outcome, body) if part)


def _tool_call_record_body(body: str, metadata: dict[str, object]) -> str:
    body = body.rstrip()
    input_obj = metadata.get("input", metadata.get("args"))
    has_structured_target = isinstance(input_obj, dict) and any(
        key in {"path", "command", "pattern"} and isinstance(value, str) and value
        for key, value in input_obj.items()
    )
    if has_structured_target and not body.endswith(" ↳"):
        body = body.removeprefix("↳ ").removesuffix(" ↳").rstrip()
    raw_tool = next(
        (
            value
            for key in ("tool_name", "name", "tool")
            if isinstance(value := metadata.get(key), str) and value
        ),
        "",
    )
    tool = friendly_tool_name(raw_tool) if raw_tool else ""
    call_id = tool_call_id(metadata)
    target = metadata.get("target")
    suffixes = [f"call_id={call_id}" if call_id else ""]
    if isinstance(target, str) and target:
        suffixes.append(target)
    suffix = " ".join(part for part in suffixes if part)
    for marker in ("▸", ">"):
        if marker in body:
            return f"{body[body.index(marker) :]} {suffix}".rstrip()
    if not tool:
        return f"{body} {suffix}".rstrip()
    if body.startswith("(") or not body.casefold().startswith(tool.casefold()):
        return f"{tool} {body} {suffix}".rstrip()
    return body if not suffix or suffix in body else f"{body} {suffix}"


def _strip_leading_tokens(body: str, tool: str, outcome: str) -> str:
    while body:
        for token in (tool, outcome):
            prefix = f"{token} "
            if body.casefold().startswith(prefix.casefold()):
                body = body[len(prefix) :]
                break
            if body.casefold() == token.casefold():
                return ""
        else:
            return body
    return body


_LIVE_BADGE_PREFIX = re.compile(r"^[✓✗⚠\u2139◐]\s+(?:PASS|FAIL|WARN|INFO|RUN)(?:\s+|$)")
_LIVE_BADGE_ONLY = re.compile(r"^(?:\d{2}:\d{2}:\d{2}\s+)?\S+$")
_LIVE_BADGE_IDENTITY_ONLY = re.compile(r"^\d{2}:\d{2}:\d{2}\s+[^\s]+$")


def _strip_live_chrome(body: str, identity: str, *, preserve_before_pair: bool = False) -> str:
    match = _LIVE_BADGE_PREFIX.match(body)
    identity_prefix = f"{identity} "
    if match is None:
        return body
    remainder = body[match.end() :].strip()
    if "↳" in remainder and not preserve_before_pair:
        return remainder.split("↳", 1)[1].strip()
    if identity and remainder == identity:
        return ""
    if _LIVE_BADGE_IDENTITY_ONLY.fullmatch(remainder):
        return ""
    return remainder.removeprefix(identity_prefix) if identity else remainder


def _derive_severity(kind: ActivityEventKind, metadata: dict[str, object]) -> str:
    if kind is ActivityEventKind.ERROR:
        return "error"
    if kind is ActivityEventKind.TOOL_RESULT:
        if outcome_is_failure(metadata):
            return "error"
        return "info"
    if kind is ActivityEventKind.UNKNOWN:
        return "info"
    return "info"


def outcome_is_failure(metadata: dict[str, object]) -> bool:
    if not metadata:
        return False
    flag = metadata.get("is_error")
    if isinstance(flag, bool) and flag:
        return True
    for key in ("exit_code", "status", "error_code"):
        if _code_value_is_failure(metadata.get(key)):
            return True
    return bool(metadata.get("error"))


def _code_value_is_failure(value: object) -> bool:
    if isinstance(value, bool):
        return value is False
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        stripped = value.strip()
        return bool(stripped) and stripped != "0"
    return False




_KIND_TO_GROUPING: dict[ActivityEventKind, tuple[str, int]] = {
    ActivityEventKind.TEXT: ("agent_text", 0),
    ActivityEventKind.THINKING: ("reasoning", 1),
    ActivityEventKind.STATUS: ("status_line", 0),
    ActivityEventKind.TOOL_USE: ("tool_call", 0),
    ActivityEventKind.TOOL_RESULT: ("tool_result", 1),
    ActivityEventKind.ERROR: ("error", 0),
    ActivityEventKind.LIFECYCLE: ("phase_header", 0),
    ActivityEventKind.HEARTBEAT: ("heartbeat", 0),
    ActivityEventKind.PROGRESS: ("progress", 1),
    ActivityEventKind.SUBAGENT_PROGRESS: ("progress", 1),
    ActivityEventKind.UNKNOWN: ("unrecognized", 0),
}


__all__ = ["PresentedEntry", "build_presented_entry", "outcome_is_failure"]
