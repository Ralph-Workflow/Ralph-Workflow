
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.mcp.multimodal import ImageContent
from ralph.mcp.tools._validation_retry_hints import validation_retry_hint_file

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
ARTIFACT_PLAN_WRITE_CAPABILITY = "artifact.plan_write"
ENV_READ_CAPABILITY = "env.read"
COMPLETION_SENTINEL_RELPATHFMT = ".agent/completion_seen_{run_id}.json"

_BROKER_SECRET_ENV_NAMES: frozenset[str] = frozenset(
    {
        "RALPH_BROKER_SECRET",
        "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT",
        "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL",
    }
)

_BROKER_SECRET_DENIED_TEXT = "[redacted: broker-owned secret]"


def _timestamp() -> int:
    return int(time.time())


def _parameter_as_string(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _is_approved(outcome: object) -> bool:
    return is_policy_approved(outcome)


def _serialize_payload(payload: object) -> str:
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
    sentinel_hmac: str | None = None,
) -> bool:
    if workspace is None:
        return False
    sentinel_payload: dict[str, str] = {"run_id": run_id}
    if sentinel_hmac is not None:
        digest = hmac.new(
            sentinel_hmac.encode(),
            run_id.encode(),
            hashlib.sha256,
        ).hexdigest()
        sentinel_payload["hmac"] = digest
    payload = json.dumps(sentinel_payload, ensure_ascii=False)
    if _write_fn is not None:
        try:
            sentinel_relpath = COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
            sentinel_abspath = workspace.absolute_path(sentinel_relpath)
        except Exception:
            sentinel_abspath = f".agent/completion_seen_{run_id}.json"
        _write_fn(sentinel_abspath, payload)
        return True

    root_value: object | None = getattr(workspace, "root", None)
    if not isinstance(root_value, Path):
        return False

    db_written = False
    db: RunStateDB | None = None
    try:
        db = RunStateDB(root_value)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        try:
            hmac_hex_value: str | None = sentinel_payload.get("hmac")
            db.upsert_completion_sentinel(run_id, hmac_hex_value)
            db_written = True
        except (OSError, RuntimeError, sqlite3.Error):
            pass
        finally:
            with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
                db.close()

    if db_written:
        return True

    return _write_legacy_sentinel_fallback(root_value, run_id, payload)


def _write_legacy_sentinel_fallback(workspace_root: Path, run_id: str, payload: str) -> bool:
    sentinel_path = workspace_root / COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    try:
        sentinel_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(DEFAULT_FILE_BACKEND, sentinel_path, payload, encoding="utf-8")
    except OSError:
        try:
            return sentinel_path.read_text(encoding="utf-8") == payload
        except (OSError, UnicodeDecodeError):
            return False
    return True


PROGRESS_PIPELINE_MARKER = "[Progress event emitted to pipeline]"


def format_progress_text(status: str, note: str, timestamp: int) -> str:
    return (
        f"Progress reported: status='{status}', note='{note}', timestamp={timestamp}\n"
        f"{PROGRESS_PIPELINE_MARKER}"
    )


def require_capability(session: CoordinationSessionLike, capability: str, action: str) -> None:
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
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Task completion")
    summary_value = params.get("summary", "No summary provided")
    summary = summary_value if isinstance(summary_value, str) else "No summary provided"
    partial_reason_value = params.get("partial_reason")
    partial_reason = partial_reason_value if isinstance(partial_reason_value, str) else None
    broker_secret: str | None = getattr(session, "broker_secret", None)
    sentinel_written = _write_completion_sentinel(
        workspace, session.run_id, sentinel_hmac=broker_secret
    )
    if not sentinel_written:
        error_message = (
            "Task completion rejected: durable completion sentinel could "
            "not be persisted (neither the RunStateDB row nor the legacy "
            f".agent/completion_seen_<run_id>.json file was written). "
            f"session_id={session.session_id}, run_id={session.run_id}"
        )
        return ToolResult(
            content=[ToolContent.text_content(error_message)],
            is_error=True,
        )
    from ralph.recovery.retry_prompt import (
        VALIDATION_FAILURE_BANNER,
    )

    retry_hint_path = (
        validation_retry_hint_file(session, workspace) if workspace is not None else None
    )
    if retry_hint_path is not None and retry_hint_path.exists():
        retry_hint = retry_hint_path.read_text(encoding="utf-8")
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"{VALIDATION_FAILURE_BANNER}\n"
                    f"{retry_hint}\n"
                    "The completion gate will re-validate the submitted artifact."
                )
            ],
            is_error=True,
        )
    message = (
        "Task declared complete: "
        f"session_id={session.session_id}, summary='{_quotable_summary(summary)}', "
        + (f"partial_reason='{_quotable_summary(partial_reason)}', " if partial_reason else "")
        + f"timestamp={now_fn()}\n"
        "[Completion event emitted to pipeline]"
    )
    return ToolResult(content=[ToolContent.text_content(message)], is_error=False)


def _quotable_summary(summary: str) -> str:
    flattened = _CONTROL_RUN.sub(" ", summary)
    return flattened.replace("'", '"')


_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f]+")


def format_coordination_text(
    action: str,
    session_id: str,
    timestamp: int,
    work_unit_id: str | None,
    payload: object | None,
) -> str:
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
    require_capability(session, ARTIFACT_PLAN_WRITE_CAPABILITY, "Workspace coordination")
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
    require_capability(session, ENV_READ_CAPABILITY, "Environment variable read")
    name = _parameter_as_string(params, "name")
    value = read_env_value(env, name)
    return ToolResult(
        content=[ToolContent.text_content(f"{name}={value}")],
        is_error=False,
    )


def read_env_value(env: dict[str, str] | os._Environ[str], name: str) -> str:
    if name in _BROKER_SECRET_ENV_NAMES:
        return _BROKER_SECRET_DENIED_TEXT
    return env.get(name, "[not found]")


__all__ = [
    "ARTIFACT_PLAN_WRITE_CAPABILITY",
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
