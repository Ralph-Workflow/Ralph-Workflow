"""ExecutionError for the exec MCP tool."""

from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):
    """Raised when the exec subprocess cannot be started or times out.

    Optional keyword fields enable structured, agent-actionable error messages
    for cache-full and workspace-limit scenarios.
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
        suggested_timeout_ms: int | None = None,
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
        self.suggested_timeout_ms = suggested_timeout_ms
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

    def _render_timeout(self) -> str:
        ms = self.timeout_ms if self.timeout_ms is not None else "?"
        lines: list[str] = [f"Command timed out after {ms}ms (process killed)."]
        lines.append(
            "Re-issuing the IDENTICAL call will time out again. A timeout has two"
            " possible causes — decide which before retrying:"
        )
        if self.suggested_timeout_ms is not None:
            lines.append(
                f"1. The command is legitimately long-running: pass a larger timeout_ms"
                f" (e.g. {self.suggested_timeout_ms}) or run a shorter command."
            )
        else:
            lines.append(
                "1. The command is legitimately long-running: pass a larger timeout_ms"
                " or run a shorter command."
            )
        lines.append(
            "2. The command is genuinely stuck (infinite loop, deadlock, or blocked"
            " waiting on input): raising timeout_ms will only waste more time — fix the"
            " command itself. Do not retry unchanged."
        )
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)

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
