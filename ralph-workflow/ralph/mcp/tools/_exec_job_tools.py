"""MCP tool handlers for exec job monitoring and cancellation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import ralph.timeout_defaults as td
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.server.exec_sse_streaming import get_current_progress_subscription
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobSnapshot,
    serialize_liveness_verdict,
)
from ralph.mcp.tools._exec_jobs import (
    get_exec_job_registry as _get_exec_job_registry_from_jobs,
)
from ralph.mcp.tools._exec_jobs import (
    reset_exec_job_registry as _reset_exec_job_registry_from_jobs,
)
from ralph.mcp.tools._exec_jobs import (
    set_exec_job_registry as _set_exec_job_registry_from_jobs,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ralph.process.manager import ProcessManager

EXEC_WAIT_TOOL: Final[str] = "exec_wait"
EXEC_CANCEL_TOOL: Final[str] = "exec_cancel"

PROCESS_EXEC_BOUNDED_CAPABILITY: Final[str] = McpCapability.PROCESS_EXEC_BOUNDED.value
PROCESS_EXEC_UNBOUNDED_CAPABILITY: Final[str] = McpCapability.PROCESS_EXEC_UNBOUNDED.value


def get_exec_job_registry(
    *,
    process_manager: ProcessManager | None = None,
    clock: Callable[[], float] | None = None,
    spill_dir: Path | None = None,
    sample_interval_s: float = td.EXEC_LIVENESS_SAMPLE_INTERVAL_S,
) -> ExecJobRegistry:
    """Return the process-local ExecJobRegistry singleton, creating it if needed.

    Thin re-export of :func:`ralph.mcp.tools._exec_jobs.get_exec_job_registry`
    so existing callers (``exec.py``, ``unsafe_exec.py``, this module's
    handlers) can keep importing it from ``_exec_job_tools`` without a
    find/replace. All four functions (``get_exec_job_registry``,
    ``set_exec_job_registry``, ``reset_exec_job_registry``,
    ``attach_progress_subscription``) are owned by ``_exec_jobs`` so the
    child MCP server has exactly one shared registry rather than the two
    independent singletons that previously existed here and in
    ``exec_sse_streaming``.

    The ``sample_interval_s`` argument is preserved for backward
    compatibility; ``_exec_jobs.get_exec_job_registry`` uses
    ``td.EXEC_LIVENESS_SAMPLE_INTERVAL_S`` directly, so tests that need a
    custom interval should construct an ``ExecJobRegistry`` and install it
    via :func:`set_exec_job_registry` rather than passing this kwarg.
    """
    del spill_dir, sample_interval_s
    return _get_exec_job_registry_from_jobs(
        process_manager=process_manager,
        clock=clock,
    )


def set_exec_job_registry(registry: ExecJobRegistry | None) -> None:
    """Inject or clear the singleton (test seam). Thin re-export."""
    _set_exec_job_registry_from_jobs(registry)


def reset_exec_job_registry() -> None:
    """Clear the singleton (test seam). Thin re-export.

    NB: the prior version of this module accepted an optional ``registry``
    argument to either install a fresh instance or clear the slot. Callers
    that previously did ``reset_exec_job_registry(my_registry)`` must now
    use :func:`set_exec_job_registry` explicitly.
    """
    _reset_exec_job_registry_from_jobs()


def _resolve_session_capability_for_job(
    session: CoordinationSessionLike,
    registry: ExecJobRegistry,
    job_id: str,
    action: str,
) -> str:
    """Validate session capabilities and resolve matching capability for the target job."""
    has_bounded = is_policy_approved(session.check_capability(PROCESS_EXEC_BOUNDED_CAPABILITY))
    has_unbounded = is_policy_approved(session.check_capability(PROCESS_EXEC_UNBOUNDED_CAPABILITY))

    if not has_bounded and not has_unbounded:
        require_capability(session, PROCESS_EXEC_BOUNDED_CAPABILITY, action)

    if has_bounded and not has_unbounded:
        return PROCESS_EXEC_BOUNDED_CAPABILITY
    if has_unbounded and not has_bounded:
        return PROCESS_EXEC_UNBOUNDED_CAPABILITY

    # Session holds both capabilities: match the job's registered capability
    with registry._lock:
        if job_id in registry._running_jobs:
            return registry._running_jobs[job_id].capability
        if job_id in registry._finished_capabilities:
            return registry._finished_capabilities[job_id]

    return PROCESS_EXEC_BOUNDED_CAPABILITY


def handle_exec_wait(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    *,
    registry: ExecJobRegistry | None = None,
) -> ToolResult:
    """Wait on an active exec job by job_id."""
    del workspace
    job_id = params.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise InvalidParamsError("'job_id' must be a non-empty string")

    wait_ms_raw = params.get("wait_ms", EXEC_SOFT_REPLY_BUDGET_MS)
    if isinstance(wait_ms_raw, (int, float)) and wait_ms_raw > 0:
        wait_ms = min(int(wait_ms_raw), EXEC_SOFT_REPLY_BUDGET_MS)
    else:
        wait_ms = EXEC_SOFT_REPLY_BUDGET_MS

    reg = registry if registry is not None else get_exec_job_registry()
    eff_cap = _resolve_session_capability_for_job(session, reg, job_id, "Exec job wait")
    current_sub = get_current_progress_subscription()

    try:
        res = reg.wait(job_id, wait_ms=wait_ms, capability=eff_cap, progress=current_sub)
    except ExecJobError as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    if isinstance(res, JobSnapshot):
        snapshot_payload: dict[str, object] = {
            "job_id": res.job_id,
            "running": res.running,
            "elapsed_s": res.elapsed_s,
            "output": res.output,
            "verdict": serialize_liveness_verdict(res.verdict),
            "instruction": res.instruction,
        }
        return ToolResult(
            content=[ToolContent.json_content(snapshot_payload)],
            is_error=False,
        )

    # Completed JobResult
    if res.kill_cause is not None:
        verdict_dict = serialize_liveness_verdict(res.verdict)
        verdict_str = json.dumps(verdict_dict) if verdict_dict is not None else None
        exec_err = ExecutionError(
            f"Job {job_id} terminated: {res.kill_cause}",
            timed_out=True,
            timeout_cause=res.kill_cause,
            # Real measurements, so the message names what actually happened:
            # how long the job ran and which ceiling was in force.
            elapsed_ms=int(res.elapsed_s * 1000),
            ceiling_ms=res.ceiling_ms,
            job_id=job_id,
            partial_output=str(res.output.get("stdout", ""))
            or str(res.output.get("stderr", ""))
            or None,
            diagnostics=verdict_str,
        )
        return ToolResult(content=[ToolContent.text_content(str(exec_err))], is_error=True)

    if res.cancelled:
        cancel_payload: dict[str, object] = {
            "job_id": res.job_id,
            "cancelled": True,
            "elapsed_s": res.elapsed_s,
            "returncode": res.returncode,
            "output": res.output,
        }
        return ToolResult(content=[ToolContent.json_content(cancel_payload)], is_error=False)

    final_payload: dict[str, object] = {
        "job_id": res.job_id,
        "running": False,
        "returncode": res.returncode,
        "elapsed_s": res.elapsed_s,
        "output": res.output,
        "verdict": serialize_liveness_verdict(res.verdict),
    }
    is_error = res.returncode is not None and res.returncode != 0
    return ToolResult(content=[ToolContent.json_content(final_payload)], is_error=is_error)


def handle_exec_cancel(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    *,
    registry: ExecJobRegistry | None = None,
) -> ToolResult:
    """Cancel an active or completed exec job by job_id."""
    del workspace
    job_id = params.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise InvalidParamsError("'job_id' must be a non-empty string")

    reg = registry if registry is not None else get_exec_job_registry()
    eff_cap = _resolve_session_capability_for_job(session, reg, job_id, "Exec job cancellation")

    try:
        res = reg.cancel(job_id, capability=eff_cap)
    except ExecJobError as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    cancel_payload: dict[str, object] = {
        "job_id": res.job_id,
        "cancelled": True,
        "elapsed_s": res.elapsed_s,
        "returncode": res.returncode,
        "output": res.output,
    }
    return ToolResult(content=[ToolContent.json_content(cancel_payload)], is_error=False)


__all__ = [
    "EXEC_CANCEL_TOOL",
    "EXEC_WAIT_TOOL",
    "get_exec_job_registry",
    "handle_exec_cancel",
    "handle_exec_wait",
    "reset_exec_job_registry",
]
