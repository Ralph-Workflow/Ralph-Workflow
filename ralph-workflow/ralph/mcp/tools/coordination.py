"""MCP tool call coordination handlers.

Ports the Rust coordination handlers that support progress reporting,
completion declaration, workspace coordination, and environment reads.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal import ImageContent

from .capability_denied_error import CapabilityDeniedError
from .coordination_session_like import CoordinationSessionLike
from .invalid_params_error import InvalidParamsError
from .tool_content import ToolContent
from .tool_error import ToolError
from .tool_result import ContentBlock, ToolResult
from .workspace_like import WorkspaceLike

if TYPE_CHECKING:
    from collections.abc import Callable

RUN_REPORT_PROGRESS_CAPABILITY = "run.report_progress"
ARTIFACT_SUBMIT_CAPABILITY = "artifact.submit"
ENV_READ_CAPABILITY = "env.read"
_COMPLETION_SENTINEL_RELPATHFMT = ".agent/completion_seen_{run_id}.json"


def _timestamp() -> int:
    """Return the current UNIX timestamp in seconds."""
    return int(time.time())


def _parameter_as_string(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _is_approved(outcome: object) -> bool:
    return is_policy_approved(outcome)


def _serialize_payload(payload: object) -> str:
    """Serialize coordination payloads with JSON, fallback to str()."""
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)
    except ValueError:
        return str(payload)


def _write_completion_sentinel(
    workspace: WorkspaceLike | None,
    run_id: str,
    *,
    _write_fn: Callable[[str, str], None] | None = None,
) -> None:
    """Write a run-scoped completion sentinel as best-effort evidence."""
    if workspace is None:
        return
    sentinel_relpath = _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    sentinel_abspath = workspace.absolute_path(sentinel_relpath)
    sentinel_payload: dict[str, str] = {"run_id": run_id}
    payload = json.dumps(sentinel_payload, ensure_ascii=False)
    if _write_fn is not None:
        _write_fn(sentinel_abspath, payload)
        return
    Path(sentinel_abspath).write_text(payload, encoding="utf-8")


#: Stable machine-readable marker appended to every progress report. The idle
#: watchdog's activity classifier keys on this to route repeated progress reports
#: into the repeated-error circuit breaker (so a cosmetic "still stuck" heartbeat
#: cannot keep a wedged agent alive forever). Keep it in sync with any consumer.
PROGRESS_PIPELINE_MARKER = "[Progress event emitted to pipeline]"


def format_progress_text(status: str, note: str, timestamp: int) -> str:
    """Build the progress report response text."""
    return (
        f"Progress reported: status='{status}', note='{note}', timestamp={timestamp}\n"
        f"{PROGRESS_PIPELINE_MARKER}"
    )


def require_capability(session: CoordinationSessionLike, capability: str, action: str) -> None:
    """Require a capability, raising a capability-denied error when missing."""
    outcome = session.check_capability(capability)
    if _is_approved(outcome):
        return

    raise CapabilityDeniedError(f"{action} requires capability '{capability}': {outcome!r}")


def handle_report_progress(
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    now_fn: Callable[[], int] = _timestamp,
) -> ToolResult:
    """Report agent progress to the Ralph pipeline."""
    require_capability(session, RUN_REPORT_PROGRESS_CAPABILITY, "Progress reporting")
    status = _parameter_as_string(params, "status")
    note_value = params.get("note", "")
    note = note_value if isinstance(note_value, str) else ""
    return ToolResult(
        content=[ToolContent.text_content(format_progress_text(status, note, now_fn()))],
        is_error=False,
    )


def handle_declare_complete(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    now_fn: Callable[[], int] = _timestamp,
) -> ToolResult:
    """Declare that the agent has completed its assigned task."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Task completion")
    summary_value = params.get("summary", "No summary provided")
    summary = summary_value if isinstance(summary_value, str) else "No summary provided"
    with contextlib.suppress(OSError):
        _write_completion_sentinel(workspace, session.run_id)
    message = (
        "Task declared complete: "
        f"session_id={session.session_id}, summary='{summary}', timestamp={now_fn()}\n"
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
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    now_fn: Callable[[], int] = _timestamp,
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
        timestamp=now_fn(),
        work_unit_id=work_unit_id,
        payload=payload,
    )
    return ToolResult(content=[ToolContent.text_content(message)], is_error=False)


def handle_read_env(
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    env: dict[str, str] | os._Environ[str] = os.environ,
) -> ToolResult:
    """Read an environment variable by name."""
    require_capability(session, ENV_READ_CAPABILITY, "Environment variable read")
    name = _parameter_as_string(params, "name")
    value = read_env_value(env, name)
    return ToolResult(
        content=[ToolContent.text_content(f"{name}={value}")],
        is_error=False,
    )


def read_env_value(env: dict[str, str] | os._Environ[str], name: str) -> str:
    """Return the value of an environment variable, or '[not found]' if absent."""
    return env.get(name, "[not found]")


__all__ = [
    "ARTIFACT_SUBMIT_CAPABILITY",
    "ENV_READ_CAPABILITY",
    "PROGRESS_PIPELINE_MARKER",
    "RUN_REPORT_PROGRESS_CAPABILITY",
    "CapabilityDeniedError",
    "ContentBlock",
    "CoordinationSessionLike",
    "ImageContent",
    "InvalidParamsError",
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
