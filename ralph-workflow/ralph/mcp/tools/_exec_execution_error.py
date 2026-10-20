"""ExecutionError for the exec MCP tool."""

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

#: Causes only ``ExecJobRegistry`` can raise. Each one names a liveness or
#: lifecycle decision, never output silence, so they must never be rendered
#: with the legacy in-request wording.
_JOB_ONLY_CAUSES: Final[frozenset[str]] = frozenset(
    {"stalled", "no_progress", "abandoned", "session_end", "restart_lost"}
)

#: The two job-management tools that actually exist. Every job-path message
#: may name these and nothing else — there is no background/submit-and-poll
#: mode in this server (an exec call that outlives the soft-reply budget
#: already returns a ``job_id`` that ``exec_wait`` polls).
_JOB_ACTIONS: Final[str] = "call exec_wait to poll a running job, or exec_cancel to abort it"

#: Polling is the only defence against the abandonment window.
_POLL_ACTION: Final[str] = "Call exec_wait while the job runs, or exec_cancel to stop it."

#: What to do when a ceiling cannot be raised.
_SPLIT_ACTION: Final[str] = "Split the work into smaller commands"


class ExecutionError(ToolError):
    """Raised when the exec subprocess cannot be started or is killed.

    Optional keyword fields enable structured, agent-actionable error messages
    for cache-full and workspace-limit scenarios.

    Timeout messages must stay *cause-accurate*: the exec family has two kill
    paths that mean different things by the same numbers.

    - **Legacy in-request kill** (``ralph.mcp.tools.exec.run_command``): the
      subprocess ran inside one tool call under an output-silence inactivity
      window, inside the absolute ``EXEC_MAX_TIMEOUT_MS`` envelope. There
      ``timeout_ms`` really is how long the caller allowed the command to run,
      and output silence really is the kill signal.
    - **Job kill** (``ExecJobRegistry``, reported through ``exec_wait`` /
      ``exec_cancel`` / the job branch of ``exec``): the command lives across
      calls under sampled liveness assessment. There ``timeout_ms`` is the
      *stall window* — a tolerance for stall pressure, not a runtime — and
      ``hard_cap`` is the job ceiling (``EXEC_JOB_CEILING_S``, raiseable per
      call via ``max_runtime_ms``), not ``EXEC_MAX_TIMEOUT_MS``.

    So ``timeout_ms`` is never rendered as elapsed time on a job kill, and
    ``elapsed_ms`` / ``ceiling_ms`` carry the real numbers when the caller has
    them. When neither is available the renderer omits the number rather than
    emitting a placeholder.
    """

    def __init__(
        self,
        message: str = "",
        *,
        # Cache-full fields
        current_bytes: int | None = None,
        cap_bytes: int | None = None,
        removed_paths: int | None = None,
        removed_bytes: int | None = None,
        remaining_bytes: int | None = None,
        # Workspace-limit fields
        workspace_bytes: int | None = None,
        max_workspace_bytes: int | None = None,
        # Timeout fields
        timed_out: bool = False,
        timeout_ms: int | None = None,
        deadline_timeout_ms: int | None = None,
        suggested_timeout_ms: int | None = None,
        timeout_cause: TimeoutCause | None = None,
        partial_output: str | None = None,
        # Real job-kill measurements. ``elapsed_ms`` is wall-clock runtime at
        # the kill; ``ceiling_ms`` is the runtime ceiling that fired (the
        # effective ``max_runtime_ms``, else ``EXEC_JOB_CEILING_S``). Callers
        # that hold them pass them; otherwise the renderer names the limit
        # without a number instead of inventing one.
        elapsed_ms: int | None = None,
        ceiling_ms: int | None = None,
        # The job handle the agent would pass to exec_wait / exec_cancel. A
        # kill error that hides it leaves the agent no way to act on the job
        # it just lost.
        job_id: str | None = None,
        # Diagnostics summary line
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
        """Render the appropriate structured template based on populated fields."""
        # Timeout template
        if self.timed_out:
            return self._render_timeout()

        # Cache-full template
        if self.current_bytes is not None and self.cap_bytes is not None:
            return self._render_cache_full()

        # Workspace-limit template
        if self.workspace_bytes is not None and self.max_workspace_bytes is not None:
            return self._render_workspace_limit()

        # Fallback: bare message
        base = super().__str__()
        if self.diagnostics:
            return f"{base}\n  Diagnostics: {self.diagnostics}"
        return base

    # -- timeout helpers ---------------------------------------------------

    def _is_job_kill(self) -> bool:
        """True when the kill came from ``ExecJobRegistry``.

        ``hard_cap`` is the only ambiguous cause: the legacy path raises it
        alongside ``deadline_timeout_ms`` (the absolute envelope that fired),
        the job path does not. ``inactivity`` is legacy-only, and the five
        causes in ``_JOB_ONLY_CAUSES`` can only come from the job registry.
        """
        if self.timeout_cause in _JOB_ONLY_CAUSES:
            return True
        return self.timeout_cause == "hard_cap" and self.deadline_timeout_ms is None

    def _resolved_elapsed_ms(self) -> int | None:
        """Real wall-clock runtime at the kill, or ``None`` when unknown.

        On a job kill ``timeout_ms`` is the stall window and must never be
        presented as elapsed time, so only ``elapsed_ms`` qualifies. On a
        legacy in-request kill the expired window is the runtime the caller
        asked for, so it stays usable.

        The serialized liveness verdict in ``diagnostics`` also carries real
        ``elapsed_s``, but parsing it here would couple the renderer to another
        module's payload shape across an untyped ``json.loads`` boundary, so
        the job handlers pass ``elapsed_ms`` explicitly instead.
        """
        if self._is_job_kill():
            return self.elapsed_ms
        return self.deadline_timeout_ms or self.timeout_ms or None

    def _resolved_ceiling_ms(self) -> int | None:
        """The runtime ceiling that fired, or ``None`` when unknown."""
        if self.ceiling_ms is not None:
            return self.ceiling_ms
        if self.timeout_cause == "hard_cap" and not self._is_job_kill():
            return self.deadline_timeout_ms
        return None

    def _ms_or_none(self, value: int | None) -> int | None:
        """Normalize a non-positive duration to "unknown"."""
        if value is None or value <= 0:
            return None
        return value

    # -- timeout rendering -------------------------------------------------

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
        """Open with the one fact that is true on both paths: the process died.

        The in-request path keeps its established "timed out" wording (its
        caller asked for a timeout and got one); the job path says "killed"
        because no single timeout expired — a liveness or lifecycle decision
        ended the job. Only a duration the renderer actually holds is named.
        """
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
        """Cause-accurate wording for an ``ExecJobRegistry`` kill.

        Each cause names what the liveness/lifecycle decision actually was.
        None of them is output silence, so none of them may claim silence.
        """
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
        """Name the server-lifecycle event that destroyed the job."""
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
        """Name the only useful recovery when the job could not survive."""
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
        # The ceiling is always named, not only when it is the sole datum: the
        # elapsed runtime in the header says how long the job ran, while this
        # number says which limit fired (the per-call ``max_runtime_ms``, else
        # the 60-minute default) — the fact the agent needs to size the retry.
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
        """Wording for the legacy in-request kill inside one tool call."""
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
        """A cause-less in-request timeout: both readings must be surfaced.

        The command may be legitimately long (raise the limit) OR genuinely
        stuck (raising the limit only wastes more time). Naming one of them
        would be a guess, so the message hands the agent the decision.
        """
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
