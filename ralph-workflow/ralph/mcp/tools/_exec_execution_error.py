from __future__ import annotations

from typing import Final, Literal

from ralph import timeout_defaults as td
from ralph.mcp.tools.coordination import ToolError

TimeoutCause = Literal[
    "inactivity",
    "hard_cap",
    "stalled",
    "no_progress",
    "abandoned",
    "session_end",
    "restart_lost",
]

_JOB_ONLY_CAUSES: Final[frozenset[str]] = frozenset(
    {"stalled", "no_progress", "abandoned", "session_end", "restart_lost"}
)

_JOB_ACTIONS: Final[str] = "call exec_wait to poll a running job, or exec_cancel to abort it"

_POLL_ACTION: Final[str] = "Call exec_wait while the job runs, or exec_cancel to stop it."

_SPLIT_ACTION: Final[str] = "Split the work into smaller commands"


class ExecutionError(ToolError):
    def __init__(
        self,
        message: str = "",
        *,
        current_bytes: int | None = None,
        cap_bytes: int | None = None,
        removed_paths: int | None = None,
        removed_bytes: int | None = None,
        remaining_bytes: int | None = None,
        workspace_bytes: int | None = None,
        max_workspace_bytes: int | None = None,
        timed_out: bool = False,
        timeout_ms: int | None = None,
        deadline_timeout_ms: int | None = None,
        suggested_timeout_ms: int | None = None,
        timeout_cause: TimeoutCause | None = None,
        partial_output: str | None = None,
        elapsed_ms: int | None = None,
        ceiling_ms: int | None = None,
        job_id: str | None = None,
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.current_bytes = current_bytes
        self.cap_bytes = cap_bytes
        self.removed_paths = removed_paths
        self.removed_bytes = removed_bytes
        self.remaining_bytes = remaining_bytes
        self.workspace_bytes = workspace_bytes
        self.max_workspace_bytes = max_workspace_bytes
        self.timed_out = timed_out
        self.timeout_ms = timeout_ms
        self.deadline_timeout_ms = deadline_timeout_ms
        self.suggested_timeout_ms = suggested_timeout_ms
        self.timeout_cause = timeout_cause
        self.partial_output = partial_output
        self.elapsed_ms = elapsed_ms
        self.ceiling_ms = ceiling_ms
        self.job_id = job_id
        self.diagnostics = diagnostics

    def __str__(self) -> str:
        if self.timed_out:
            return self._render_timeout()

        if self.current_bytes is not None and self.cap_bytes is not None:
            return self._render_cache_full()

        if self.workspace_bytes is not None and self.max_workspace_bytes is not None:
            return self._render_workspace_limit()

        base = super().__str__()
        if self.diagnostics:
            return f"{base}\n  Diagnostics: {self.diagnostics}"
        return base

    def _is_job_kill(self) -> bool:
        if self.timeout_cause in _JOB_ONLY_CAUSES:
            return True
        return self.timeout_cause == "hard_cap" and self.deadline_timeout_ms is None

    def _resolved_elapsed_ms(self) -> int | None:
        if self._is_job_kill():
            return self.elapsed_ms
        return self.deadline_timeout_ms or self.timeout_ms or None

    def _resolved_ceiling_ms(self) -> int | None:
        if self.ceiling_ms is not None:
            return self.ceiling_ms
        if self.timeout_cause == "hard_cap" and not self._is_job_kill():
            return self.deadline_timeout_ms
        return None

    def _ms_or_none(self, value: int | None) -> int | None:
        if value is None or value <= 0:
            return None
        return value

    def _render_timeout(self) -> str:
        lines: list[str] = [self._render_kill_header()]
        if self._is_job_kill():
            lines.extend(self._job_cause_lines())
        else:
            lines.extend(self._in_request_cause_lines())
        if self.partial_output:
            lines.append(f"Partial output before timeout:\n{self.partial_output}")
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)

    def _render_kill_header(self) -> str:
        subject = "Command" if not self._is_job_kill() else "Job"
        if self.job_id is not None:
            subject = f"Job {self.job_id}"
        verb = "timed out" if not self._is_job_kill() else "was killed"
        elapsed_ms = self._ms_or_none(self._resolved_elapsed_ms())
        if elapsed_ms is not None:
            return f"{subject} {verb} after {elapsed_ms}ms (process killed)."
        ceiling_ms = self._ms_or_none(self._resolved_ceiling_ms())
        if ceiling_ms is not None:
            return f"{subject} {verb} at its {ceiling_ms}ms runtime ceiling (process killed)."
        return f"{subject} {verb} before it completed (process killed)."

    def _job_cause_lines(self) -> list[str]:
        cause = self.timeout_cause
        if cause == "stalled":
            return self._stalled_lines()
        if cause == "no_progress":
            return [
                f"Killed for lack of progress: the strongest liveness rule stayed "
                f"suspicious for {int(td.EXEC_NO_PROGRESS_WINDOW_S)}s without recovering.",
                "Check for deadlocks, spins, or blocking calls, or split the work into "
                f"steps that make measurable progress; {_JOB_ACTIONS}.",
            ]
        if cause == "abandoned":
            return [
                f"Killed as abandoned: nobody polled the job with exec_wait for "
                f"{int(td.EXEC_JOB_ABANDON_S)}s, so it was reaped.",
                _POLL_ACTION,
            ]
        if cause == "hard_cap":
            return self._job_hard_cap_lines()
        if cause in {"session_end", "restart_lost"}:
            return [self._lifecycle_loss_cause(cause), self._lifecycle_loss_action(cause)]
        return [
            "The job was killed before it completed.",
            f"{_SPLIT_ACTION}, or re-issue it so exec returns a job_id you can poll; "
            f"{_JOB_ACTIONS}.",
        ]

    def _lifecycle_loss_cause(self, cause: TimeoutCause) -> str:
        if cause == "restart_lost":
            return (
                "Killed because the job was lost across an MCP server restart; in-memory "
                "jobs do not survive a restart and their output is gone."
            )
        return (
            "Killed because the MCP session ended; running jobs cannot outlive the "
            "session that started them."
        )

    def _lifecycle_loss_action(self, cause: TimeoutCause) -> str:
        if cause == "restart_lost":
            return (
                "Re-run the command, or split it into steps that finish within one server lifetime."
            )
        return (
            "Re-run the command in the new session, or split it into steps that finish "
            "within one session."
        )

    def _stalled_lines(self) -> list[str]:
        stall_window_ms = self._ms_or_none(self.timeout_ms)
        if stall_window_ms is None:
            cause = (
                "Killed for stall: accumulated stall pressure crossed the stall window, "
                "which scores liveness (CPU, IO, and process-tree activity) rather than "
                "output silence — a command that keeps printing can still be stalled out."
            )
        else:
            cause = (
                f"Killed for stall: accumulated stall pressure crossed the "
                f"{stall_window_ms}ms stall window, which scores liveness (CPU, IO, and "
                "process-tree activity) rather than output silence — a command that keeps "
                "printing can still be stalled out."
            )
        return [
            cause,
            f"Raise the stall window with timeout_ms, or make the command do measurable "
            f"work per unit time; {_JOB_ACTIONS}.",
        ]

    def _job_hard_cap_lines(self) -> list[str]:
        ceiling_ms = self._ms_or_none(self._resolved_ceiling_ms())
        if ceiling_ms is not None:
            cause = f"Killed at the {ceiling_ms}ms job runtime ceiling."
        else:
            cause = "Killed at the job runtime ceiling."
        return [
            cause,
            f"No timeout_ms value raises that ceiling; raise max_runtime_ms instead (hard "
            f"maximum {int(td.EXEC_JOB_CEILING_MAX_S)}s), or {_SPLIT_ACTION.lower()}; "
            f"{_JOB_ACTIONS}.",
        ]

    def _in_request_cause_lines(self) -> list[str]:
        cause = self.timeout_cause
        if cause == "hard_cap":
            return self._legacy_hard_cap_lines()
        if cause == "inactivity":
            return self._legacy_inactivity_lines()
        return self._ambiguous_lines()

    def _legacy_hard_cap_lines(self) -> list[str]:
        ceiling_ms = self._ms_or_none(self._resolved_ceiling_ms())
        if ceiling_ms is None:
            head = "Killed at the absolute ceiling; a larger timeout_ms cannot extend it."
        else:
            head = (
                f"Killed at the {ceiling_ms}ms absolute ceiling; a larger timeout_ms "
                "cannot extend it."
            )
        return [
            head,
            f"{_SPLIT_ACTION}, or re-issue it so exec returns a job_id you can poll; "
            f"{_JOB_ACTIONS}.",
        ]

    def _legacy_inactivity_lines(self) -> list[str]:
        window_ms = self._ms_or_none(self.timeout_ms)
        if window_ms is None:
            head = (
                "No output was received for the whole inactivity window; every output "
                "chunk resets that window."
            )
        else:
            head = (
                f"No output was received for the {window_ms}ms inactivity window; every "
                "output chunk resets that window."
            )
        lines = [
            head,
            "Raise timeout_ms for a legitimately quiet command, or fix a command stuck "
            "waiting on input, deadlocked, or looping; if it is running as a job, call "
            "exec_wait to poll it or exec_cancel to abort it.",
        ]
        if self.suggested_timeout_ms is not None:
            lines.append(f"Suggested timeout_ms: {self.suggested_timeout_ms}.")
        return lines

    def _ambiguous_lines(self) -> list[str]:
        if self.suggested_timeout_ms is not None:
            longer = (
                "1. The command is legitimately long-running: pass a larger timeout_ms "
                f"(e.g. {self.suggested_timeout_ms}) or run a shorter command."
            )
        else:
            longer = (
                "1. The command is legitimately long-running: pass a larger timeout_ms "
                "or run a shorter command."
            )
        return [
            "Re-issuing the IDENTICAL call will time out again. A timeout has two possible "
            "causes — decide which before retrying:",
            longer,
            "2. The command is genuinely stuck (infinite loop, deadlock, or blocked "
            "waiting on input): raising timeout_ms will only waste more time — fix the "
            f"command itself. Do not retry unchanged; {_JOB_ACTIONS}.",
        ]

    def _render_cache_full(self) -> str:
        lines: list[str] = ["Error: Exec cache exceeds capacity after automatic reset"]
        if self.current_bytes is not None:
            lines.append(f"  Current usage: {self.current_bytes} bytes")
        if self.cap_bytes is not None:
            lines.append(f"  Hard cap: {self.cap_bytes} bytes")
        if self.removed_paths is not None or self.removed_bytes is not None:
            removed_p = self.removed_paths or 0
            removed_b = self.removed_bytes or 0
            remaining = self.remaining_bytes or 0
            lines.append(
                f"  Automatic reset: removed {removed_p} paths ({removed_b} bytes), "
                f"{remaining} bytes remaining"
            )
        lines.append(
            "  Remaining bytes usually indicate active/live exec slots "
            "or filesystem permission issues"
        )
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)

    def _render_workspace_limit(self) -> str:
        lines: list[str] = ["Error: Workspace exceeds safety size limit"]
        if self.workspace_bytes is not None:
            lines.append(f"  Workspace size: {self.workspace_bytes} bytes")
        if self.max_workspace_bytes is not None:
            lines.append(f"  Limit: {self.max_workspace_bytes} bytes")
        lines.append(
            "  Suggested exclusions: node_modules, .venv, .mypy_cache, __pycache__, .tox, .nox"
        )
        lines.append(
            "  Suggestion: Add large generated directories to .gitignore or reduce workspace scope"
        )
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)


__all__ = ["ExecutionError", "TimeoutCause"]
