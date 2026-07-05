"""MCP tool call coordination handlers.

Ports the Rust coordination handlers that support progress reporting,
completion declaration, workspace coordination, and environment reads.

Exported surface:

- ``handle_report_progress`` — emits a ``run.report_progress`` event to
  the pipeline. Used by the agent to publish heartbeat / status updates
  while it is still working. The text response is suffixed with
  ``PROGRESS_PIPELINE_MARKER`` so the idle watchdog can key on it.
- ``handle_declare_complete`` — finalizes the run with an
  ``artifact.submit``-gated completion sentinel. Writes
  ``.agent/completion_seen_<run_id>.json`` (HMAC-signed when the broker
  secret is provided) so the failure classifier / recovery controller
  can verify the completion signal even if the MCP JSON-RPC envelope is
  lost.
- ``handle_coordinate`` — ``artifact.plan_write``-gated workspace
  coordination: planning-drain agents publish actions / work-unit
  payloads that the parent process observes. The text response is
  suffixed with ``[Coordination event emitted to pipeline]``.
- ``handle_read_env`` — ``env.read``-gated environment variable read.
  Returns ``<name>=<value>`` or ``<name>=[not found]``. The agent
  diagnostic / orchestrator uses this to inspect the run environment.
- ``require_capability`` — the canonical capability check used by every
  public handler in this module (and re-exported for ``exec.py``,
  ``git_read.py``, ``websearch.py``, and ``webvisit.py``).
- ``format_progress_text`` / ``format_coordination_text`` /
  ``_write_completion_sentinel`` — formatting and persistence helpers.

Trust boundary: every public handler is gated on a ``McpCapability``
declared by the agent session. The four capability strings
(``RUN_REPORT_PROGRESS_CAPABILITY``, ``ARTIFACT_SUBMIT_CAPABILITY``,
``ARTIFACT_PLAN_WRITE_CAPABILITY``, ``ENV_READ_CAPABILITY``) are the
contract between the agent's session declaration and the handler-side
default-deny check.

Side effects: ``handle_declare_complete`` writes a completion sentinel
to ``.agent/``; the other handlers are pure with respect to the
workspace and only emit pipeline events. No subprocess is spawned, no
network call is made.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.artifacts.state_db import RunStateDB
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
# Coordinate is planning/coordination-only — gated on plan_write so it matches the
# coordinate tool spec's advertised capability (planning drains only), not the
# broader artifact.submit held by every drain.
ARTIFACT_PLAN_WRITE_CAPABILITY = "artifact.plan_write"
ENV_READ_CAPABILITY = "env.read"
COMPLETION_SENTINEL_RELPATHFMT = ".agent/completion_seen_{run_id}.json"


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
    sentinel_hmac: str | None = None,
) -> None:
    """Write a run-scoped completion sentinel as best-effort evidence.

    When ``sentinel_hmac`` is provided the sentinel payload includes
    an ``hmac`` field binding the run id to a broker-owned secret so a
    model that can write under ``.agent/`` cannot forge a valid
    sentinel. ``_check_completion_sentinel`` in
    ``ralph.agents.completion_signals`` verifies the HMAC the same way
    when the secret is provided.

    Storage (RFC-013 P3): sentinels are written to ``.agent/state.db``
    via ``RunStateDB`` (one row per ``run_id``). The legacy ``.agent/
    completion_seen_<run_id>.json`` file path is NOT written during
    production rollout; it is preserved only as a read-fallback in
    ``_check_completion_sentinel`` so an in-flight run surviving an
    upgrade still passes its completion gate. When ``_write_fn`` is
    provided the test seam captures the payload without performing any
    disk or DB I/O.
    """
    if workspace is None:
        return
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
        # Test seam: keep the file-format assertion path working without
        # touching the real workspace or DB.
        try:
            sentinel_relpath = COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
            sentinel_abspath = workspace.absolute_path(sentinel_relpath)
        except Exception:
            sentinel_abspath = f".agent/completion_seen_{run_id}.json"
        _write_fn(sentinel_abspath, payload)
        return

    root_value: object | None = getattr(workspace, "root", None)
    if isinstance(root_value, Path):
        db = RunStateDB(root_value)
        try:
            hmac_hex_value: str | None = sentinel_payload.get("hmac")
            db.upsert_completion_sentinel(run_id, hmac_hex_value)
        finally:
            db.close()


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
    """Report agent progress to the Ralph pipeline.

    Args:
        session: Agent session; must declare ``run.report_progress``.
        _workspace: Unused; kept for tool-handler signature parity.
        params: Mapping with required ``status`` (string) and optional
            ``note`` (string, defaults to empty).
        now_fn: Optional injected wall-clock provider for the
            ``unix_ts`` suffix in the formatted text. Defaults to
            ``_timestamp``.

    Returns:
        A ``ToolResult`` whose text content is the formatted progress
        line (suffixed with ``PROGRESS_PIPELINE_MARKER`` so the idle
        watchdog can key on it).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``run.report_progress``.

    Side effects:
        Pure with respect to the workspace. Emits a
        ``run.report_progress`` event to the pipeline. No subprocess,
        no network call.
    """
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
    """Declare that the agent has completed its assigned task.

    Args:
        session: Agent session; must declare ``artifact.submit`` and
            carry a valid ``run_id`` used to name the sentinel file.
        workspace: Workspace surface whose root resolves
            ``.agent/completion_seen_<run_id>.json``.
        params: Mapping with optional ``summary`` (string, defaults to
            ``"No summary provided"``).
        now_fn: Optional injected wall-clock provider for the timestamp
            in the response. Defaults to ``_timestamp``.

    Returns:
        A ``ToolResult`` whose text content is the completion summary
        line (suffixed with ``[Completion event emitted to pipeline]``).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``artifact.submit``.

    Side effects:
        Writes ``.agent/completion_seen_<run_id>.json`` (HMAC-signed
        when the broker secret is provided) so the failure classifier /
        recovery controller can verify the completion signal even if
        the MCP JSON-RPC envelope is lost. ``OSError`` from the sentinel
        write is suppressed (best-effort) so a transient filesystem
        issue cannot mask the completion event.
    """
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Task completion")
    summary_value = params.get("summary", "No summary provided")
    summary = summary_value if isinstance(summary_value, str) else "No summary provided"
    # RFC-013 P3: thread the broker-owned secret through the live
    # write path so the sentinel payload includes an HMAC binding the
    # run id to the secret. ``session.broker_secret`` is ``None`` when
    # the broker has not configured HMAC enforcement; the underlying
    # ``_write_completion_sentinel`` treats ``sentinel_hmac=None`` as
    # "no HMAC" (pre-P3 contract).
    broker_secret: str | None = getattr(session, "broker_secret", None)
    # Best-effort: a transient filesystem / DB issue cannot mask the
    # completion event. ``sqlite3.Error`` covers RunStateDB failure
    # modes (locked / corrupt / unsupported) under RFC-013 P3.
    with contextlib.suppress(OSError, sqlite3.Error):
        _write_completion_sentinel(
            workspace, session.run_id, sentinel_hmac=broker_secret
        )
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
    """Coordinate parallel worker activities.

    Args:
        session: Agent session; must declare ``artifact.plan_write``.
        _workspace: Unused; kept for tool-handler signature parity.
        params: Mapping with required ``action`` (string), optional
            ``work_unit_id`` (string) and ``payload`` (object).
        now_fn: Optional injected wall-clock provider for the timestamp
            in the formatted text. Defaults to ``_timestamp``.

    Returns:
        A ``ToolResult`` whose text content is the formatted
        coordination line (suffixed with
        ``[Coordination event emitted to pipeline]``).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``artifact.plan_write``.

    Side effects:
        Pure with respect to the workspace. Emits a workspace
        coordination event for the planning drain to observe. No
        subprocess, no network call.
    """
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
    """Read an environment variable by name.

    Args:
        session: Agent session; must declare ``env.read``.
        _workspace: Unused; kept for tool-handler signature parity.
        params: Mapping with required ``name`` (string).
        env: Optional injected environment mapping. Defaults to
            ``os.environ`` (read-only ``os._Environ[str]``).

    Returns:
        A ``ToolResult`` whose text content is ``<name>=<value>`` or
        ``<name>=[not found]`` when the variable is absent.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``env.read``.

    Side effects:
        Pure read of the injected ``env`` mapping. No subprocess, no
        network call, no workspace writes.
    """
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
