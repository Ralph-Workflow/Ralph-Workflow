"""MCP tool call coordination handlers.

Ports the Rust coordination handlers that support progress reporting,
completion declaration, workspace coordination, and environment reads.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

RUN_REPORT_PROGRESS_CAPABILITY = "run.report_progress"
ARTIFACT_SUBMIT_CAPABILITY = "artifact.submit"
ENV_READ_CAPABILITY = "env.read"
_APPROVED_POLICY_OUTCOMES = {"approved", "allow", "allowed"}


class ToolError(Exception):
    """Base error raised by MCP tool handlers."""


class InvalidParamsError(ToolError):
    """Raised when tool parameters are missing or invalid."""


class CapabilityDeniedError(ToolError):
    """Raised when a required session capability is not available."""


@dataclass(frozen=True)
class ToolContent:
    """Single tool response content block."""

    type: str
    text: str

    @classmethod
    def text_content(cls, text: str) -> ToolContent:
        """Create a text content block."""
        return cls(type="text", text=text)

    def to_dict(self) -> dict[str, str]:
        """Serialize the content block to a dictionary."""
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class ToolResult:
    """Serializable MCP tool result."""

    content: list[ToolContent]
    is_error: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result to an MCP-compatible dictionary."""
        return {
            "content": [item.to_dict() for item in self.content],
            "isError": self.is_error,
        }


@runtime_checkable
class SessionLike(Protocol):
    """Minimum session surface required by coordination handlers."""

    session_id: str

    def check_capability(self, capability: str) -> object:
        """Return a policy outcome for the requested capability."""


@runtime_checkable
class WorkspaceLike(Protocol):
    """Placeholder workspace protocol for handler parity."""


def _timestamp() -> int:
    """Return the current UNIX timestamp in seconds."""
    return int(time.time())


def _parameter_as_string(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _is_approved(outcome: object) -> bool:
    if outcome is True:
        return True
    if isinstance(outcome, str):
        return outcome.strip().lower() in _APPROVED_POLICY_OUTCOMES

    for attribute_name in ("name", "value", "status"):
        attribute = getattr(outcome, attribute_name, None)
        if isinstance(attribute, str) and attribute.strip().lower() in _APPROVED_POLICY_OUTCOMES:
            return True

    return False


def _serialize_payload(payload: object) -> str:
    """Serialize coordination payloads with JSON, fallback to str()."""
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)
    except ValueError:
        return str(payload)


def format_progress_text(status: str, note: str, timestamp: int) -> str:
    """Build the progress report response text."""
    return (
        f"Progress reported: status='{status}', note='{note}', timestamp={timestamp}\n"
        "[Progress event emitted to pipeline]"
    )


def require_capability(session: SessionLike, capability: str, action: str) -> None:
    """Require a capability, raising a capability-denied error when missing."""
    outcome = session.check_capability(capability)
    if _is_approved(outcome):
        return

    raise CapabilityDeniedError(f"{action} requires capability '{capability}': {outcome!r}")


def handle_report_progress(
    session: SessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, Any],
) -> ToolResult:
    """Report agent progress to the Ralph pipeline."""
    require_capability(session, RUN_REPORT_PROGRESS_CAPABILITY, "Progress reporting")
    status = _parameter_as_string(params, "status")
    note_value = params.get("note", "")
    note = note_value if isinstance(note_value, str) else ""
    return ToolResult(
        content=[ToolContent.text_content(format_progress_text(status, note, _timestamp()))],
        is_error=False,
    )


def handle_declare_complete(
    session: SessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, Any],
) -> ToolResult:
    """Declare that the agent has completed its assigned task."""
    summary_value = params.get("summary", "No summary provided")
    summary = summary_value if isinstance(summary_value, str) else "No summary provided"
    message = (
        "Task declared complete: "
        f"session_id={session.session_id}, summary='{summary}', timestamp={_timestamp()}\n"
        "[Completion event emitted to pipeline]"
    )
    return ToolResult(content=[ToolContent.text_content(message)], is_error=False)


def format_coordination_text(
    action: str,
    session_id: str,
    timestamp: int,
    work_unit_id: str | None,
    payload: object | None,
) -> str:
    """Format the coordination response text."""
    message = (
        f"Coordination action '{action}' processed: session_id={session_id}, timestamp={timestamp}"
    )
    if work_unit_id is not None:
        message = f"{message}, work_unit_id={work_unit_id}"
    if payload is not None:
        message = f"{message}, payload={_serialize_payload(payload)}"
    return f"{message}\n[Coordination event emitted to pipeline]"


def handle_coordinate(
    session: SessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, Any],
) -> ToolResult:
    """Coordinate parallel worker activities."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Workspace coordination")
    action = _parameter_as_string(params, "action")
    work_unit_value = params.get("work_unit_id")
    work_unit_id = work_unit_value if isinstance(work_unit_value, str) else None
    payload = params.get("payload")
    message = format_coordination_text(
        action=action,
        session_id=session.session_id,
        timestamp=_timestamp(),
        work_unit_id=work_unit_id,
        payload=payload,
    )
    return ToolResult(content=[ToolContent.text_content(message)], is_error=False)


def handle_read_env(
    session: SessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, Any],
) -> ToolResult:
    """Read an environment variable by name."""
    require_capability(session, ENV_READ_CAPABILITY, "Environment variable read")
    name = _parameter_as_string(params, "name")
    value = os.environ.get(name, "[not found]")
    return ToolResult(
        content=[ToolContent.text_content(f"{name}={value}")],
        is_error=False,
    )


__all__ = [
    "ARTIFACT_SUBMIT_CAPABILITY",
    "ENV_READ_CAPABILITY",
    "RUN_REPORT_PROGRESS_CAPABILITY",
    "CapabilityDeniedError",
    "InvalidParamsError",
    "SessionLike",
    "ToolContent",
    "ToolError",
    "ToolResult",
    "WorkspaceLike",
    "format_coordination_text",
    "format_progress_text",
    "handle_coordinate",
    "handle_declare_complete",
    "handle_read_env",
    "handle_report_progress",
    "require_capability",
]
