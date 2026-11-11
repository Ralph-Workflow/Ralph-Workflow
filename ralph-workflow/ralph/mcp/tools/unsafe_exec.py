
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.server.exec_sse_streaming import get_current_progress_subscription
from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools._exec_job_tools import get_exec_job_registry
from ralph.mcp.tools._exec_jobs import (
    ExecJobError,
    ExecJobRegistry,
    JobSnapshot,
    serialize_liveness_verdict,
)
from ralph.mcp.tools._exec_output_spill import format_or_spill
from ralph.mcp.tools._exec_vcs_scanner import (
    _VCS_COMMANDS,
    _scan_text_for_vcs_violation,
    exec_usage_hints,
    find_vcs_usage_in_scripts,
)
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.exec import (
    _shell_command_segments,
    _workspace_root,
    parse_exec_max_runtime,
    parse_exec_timeout,
    resolve_spill_dir,
    run_command,
)
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.tools._exec_run_deps import ExecRunDeps

PROCESS_EXEC_UNBOUNDED_CAPABILITY: Final = "ProcessExecUnbounded"


def _enforce_vcs_blacklist(command: str, workspace: object) -> str | None:
    segments = _shell_command_segments(command)
    for segment_command, segment_args in segments:
        reason = _scan_text_for_vcs_violation(" ".join([segment_command, *segment_args]))
        if reason is not None:
            raise CapabilityDeniedError(f"Command '{segment_command}': {reason}")
    script_hit = find_vcs_usage_in_scripts(segments, _workspace_root(workspace))
    if script_hit is None:
        return None
    script, _word = script_hit
    return f"Warning: script '{script}' contains version-control commands"


def handle_unsafe_exec(
    session: CoordinationSessionLike,
    workspace: object,
    params: Mapping[str, object],
    deps: ExecRunDeps | None = None,
    *,
    registry: ExecJobRegistry | None = None,
) -> ToolResult:
    require_capability(session, PROCESS_EXEC_UNBOUNDED_CAPABILITY, "Unsafe command execution")

    command_value = params.get("command")
    if not isinstance(command_value, str) or not command_value.strip():
        raise InvalidParamsError("'command' must be a non-empty string")

    command = command_value.strip()
    if "\x00" in command:
        raise InvalidParamsError("'command' must not contain an embedded NUL")
    script_warning = _enforce_vcs_blacklist(command, workspace)

    timeout_ms = parse_exec_timeout(params)

    if deps is not None and deps.runner is not None:
        try:
            output = run_command("sh", ["-c", command], workspace, timeout_ms, deps=deps)
        except ExecutionError as exc:
            if not exc.timed_out:
                raise
            return ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
    else:
        effective_registry = (
            registry
            if registry is not None
            else get_exec_job_registry(
                process_manager=deps.process_manager if deps else None,
                spill_dir=resolve_spill_dir(workspace, deps),
            )
        )
        cwd_prov = (
            deps.cwd_provider if deps is not None and deps.cwd_provider is not None else Path.cwd
        )
        cwd = _workspace_root(workspace, cwd_provider=cwd_prov)
        max_runtime_ms = parse_exec_max_runtime(params)
        current_sub = get_current_progress_subscription()
        try:
            job_id = effective_registry.start(
                ["sh", "-c", command],
                cwd=str(cwd),
                timeout_ms=timeout_ms,
                max_runtime_ms=max_runtime_ms,
                capability=PROCESS_EXEC_UNBOUNDED_CAPABILITY,
                progress=current_sub,
            )
            res = effective_registry.wait(
                job_id,
                wait_ms=EXEC_SOFT_REPLY_BUDGET_MS,
                capability=PROCESS_EXEC_UNBOUNDED_CAPABILITY,
                progress=current_sub,
            )
        except ExecJobError as exc:
            raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc
        except Exception as exc:
            raise ExecutionError(f"Failed to execute '{command}': {exc}") from exc

        if isinstance(res, JobSnapshot):
            snapshot_payload: dict[str, object] = {
                "job_id": res.job_id,
                "running": res.running,
                "elapsed_s": res.elapsed_s,
                "output": res.output,
                "verdict": serialize_liveness_verdict(res.verdict),
                "instruction": "call exec_wait; do not re-run",
            }
            return ToolResult(
                content=[ToolContent.json_content(snapshot_payload)],
                is_error=False,
            )

        if res.kill_cause is not None:
            verdict_dict = serialize_liveness_verdict(res.verdict)
            verdict_str = json.dumps(verdict_dict) if verdict_dict is not None else None
            kill_exc = ExecutionError(
                f"Failed to execute '{command}': job {res.job_id} terminated ({res.kill_cause})",
                timed_out=True,
                timeout_ms=timeout_ms,
                timeout_cause=res.kill_cause,
                elapsed_ms=int(res.elapsed_s * 1000),
                ceiling_ms=res.ceiling_ms,
                job_id=res.job_id,
                partial_output=str(res.output.get("stdout", ""))
                or str(res.output.get("stderr", ""))
                or None,
                diagnostics=verdict_str,
            )
            return ToolResult(
                content=[ToolContent.text_content(str(kill_exc))],
                is_error=True,
            )

        stdout_bytes = str(res.output.get("stdout", "")).encode("utf-8")
        stderr_bytes = str(res.output.get("stderr", "")).encode("utf-8")
        output = _CompletedProcessAdapter(
            stdout=stdout_bytes,
            stderr=stderr_bytes,
            returncode=res.returncode or 0,
            truncated=bool(res.output.get("truncated", False)),
        )

    stdout = output.stdout.decode("utf-8", errors="replace")
    stderr = output.stderr.decode("utf-8", errors="replace")
    text = (
        f"Command: {command}\n"
        f"Exit code: {output.returncode}\n\n"
        f"Stdout:\n{stdout}\n\n"
        f"Stderr:\n{stderr}"
    )
    hints = exec_usage_hints(_shell_command_segments(command))
    if script_warning is not None:
        hints.insert(0, script_warning)
    if hints:
        text = f"{text}\n\n" + "\n\n".join(hints)
    return format_or_spill(
        text,
        returncode=output.returncode,
        truncated=output.truncated,
        spill_dir=resolve_spill_dir(workspace, deps),
    )


__all__ = [
    "PROCESS_EXEC_UNBOUNDED_CAPABILITY",
    "_VCS_COMMANDS",
    "handle_unsafe_exec",
]
