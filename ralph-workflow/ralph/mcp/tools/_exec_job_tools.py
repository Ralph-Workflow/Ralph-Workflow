"""MCP tool handlers for exec job monitoring and cancellation."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Final

from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobSnapshot,
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


class _JobRegistryState:
    """Holder for the global ExecJobRegistry instance."""

    instance: ExecJobRegistry | None = None


_registry_state = _JobRegistryState()
_REGISTRY_LOCK: Final[threading.Lock] = threading.Lock()


def get_exec_job_registry(
    *,
    process_manager: ProcessManager | None = None,
    clock: Callable[[], float] | None = None,
    spill_dir: Path | None = None,
    sample_interval_s: float = 0.05,
) -> ExecJobRegistry:
    """Return the global ExecJobRegistry singleton, creating it if needed."""
    if _registry_state.instance is None:
        with _REGISTRY_LOCK:
            if _registry_state.instance is None:
                _registry_state.instance = ExecJobRegistry(
                    process_manager=process_manager,
                    clock=clock,
                    spill_dir=spill_dir,
                    sample_interval_s=sample_interval_s,
                )
    return _registry_state.instance


def reset_exec_job_registry(registry: ExecJobRegistry | None = None) -> None:
    """Reset or replace the global ExecJobRegistry singleton."""
    with _REGISTRY_LOCK:
        _registry_state.instance = registry


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

    try:
        res = reg.wait(job_id, wait_ms=wait_ms, capability=eff_cap)
    except ExecJobError as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    if isinstance(res, JobSnapshot):
        verdict_dict: dict[str, object] | None = None
        if res.verdict is not None:
            verdict_dict = {
                "score": res.verdict.score,
                "pressure": res.verdict.pressure,
                "verdict_kind": res.verdict.verdict_kind,
                "suspicious": res.verdict.suspicious,
            }
        snapshot_payload: dict[str, object] = {
            "job_id": res.job_id,
            "running": res.running,
            "elapsed_s": res.elapsed_s,
            "output": res.output,
            "verdict": verdict_dict,
            "instruction": res.instruction,
        }
        return ToolResult(
            content=[ToolContent.json_content(snapshot_payload)],
            is_error=False,
        )

    # Completed JobResult
    if res.kill_cause is not None:
        exec_err = ExecutionError(
            f"Job {job_id} terminated: {res.kill_cause}",
            timed_out=True,
            timeout_cause=res.kill_cause,
            partial_output=str(res.output.get("stdout", ""))
            or str(res.output.get("stderr", ""))
            or None,
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

    verdict_dict_final: dict[str, object] | None = None
    if res.verdict is not None:
        verdict_dict_final = {
            "score": res.verdict.score,
            "pressure": res.verdict.pressure,
            "verdict_kind": res.verdict.verdict_kind,
            "suspicious": res.verdict.suspicious,
        }

    final_payload: dict[str, object] = {
        "job_id": res.job_id,
        "running": False,
        "returncode": res.returncode,
        "elapsed_s": res.elapsed_s,
        "output": res.output,
        "verdict": verdict_dict_final,
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
