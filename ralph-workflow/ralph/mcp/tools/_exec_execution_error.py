"""ExecutionError for the exec MCP tool."""

from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):
    """Raised when the exec subprocess cannot be started or times out.

    Optional keyword fields enable structured, agent-actionable error messages
    for cache-full, cleanup-cooldown, and workspace-limit scenarios.
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
        # Cleanup-cooldown fields
        consecutive_failures: int | None = None,
        cooldown_remaining_s: float | None = None,
        last_error: str | None = None,
        # Workspace-limit fields
        workspace_bytes: int | None = None,
        max_workspace_bytes: int | None = None,
        # Diagnostics summary line
        diagnostics: str | None = None,
    ) -> None:
        super().__init__(message)
        self.current_bytes = current_bytes
        self.cap_bytes = cap_bytes
        self.removed_paths = removed_paths
        self.removed_bytes = removed_bytes
        self.remaining_bytes = remaining_bytes
        self.consecutive_failures = consecutive_failures
        self.cooldown_remaining_s = cooldown_remaining_s
        self.last_error = last_error
        self.workspace_bytes = workspace_bytes
        self.max_workspace_bytes = max_workspace_bytes
        self.diagnostics = diagnostics

    def __str__(self) -> str:
        """Render the appropriate structured template based on populated fields."""
        # Cache-full template
        if self.current_bytes is not None and self.cap_bytes is not None:
            return self._render_cache_full()

        # Cleanup-cooldown template
        if self.consecutive_failures is not None and self.cooldown_remaining_s is not None:
            return self._render_cleanup_cooldown()

        # Workspace-limit template
        if self.workspace_bytes is not None and self.max_workspace_bytes is not None:
            return self._render_workspace_limit()

        # Fallback: bare message
        base = super().__str__()
        if self.diagnostics:
            return f"{base}\n  Diagnostics: {self.diagnostics}"
        return base

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
                f"  Cleanup+reset: removed {removed_p} paths ({removed_b} bytes), "
                f"{remaining} bytes remaining"
            )
        lines.append(
            "  Remaining bytes usually indicate active/live exec slots, "
            "unacquirable locks, or filesystem permission issues"
        )
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)

    def _render_cleanup_cooldown(self) -> str:
        lines: list[str] = ["Error: Exec cache cleanup has failed repeatedly"]
        if self.consecutive_failures is not None:
            lines.append(f"  Consecutive failures: {self.consecutive_failures}")
        if self.cooldown_remaining_s is not None:
            lines.append(f"  Cooldown remaining: {self.cooldown_remaining_s:.0f}s")
        if self.last_error:
            lines.append(f"  Last error: {self.last_error}")
        lines.append(
            "  Wait for cooldown to expire then retry; check for active/live exec slots, "
            "permission issues, or stale locks if the problem persists"
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
            "  Suggested exclusions: node_modules, .venv, .mypy_cache, "
            "__pycache__, .tox, .nox"
        )
        lines.append(
            "  Suggestion: Add large generated directories to .gitignore "
            "or reduce workspace scope"
        )
        if self.diagnostics:
            lines.append(f"  Diagnostics: {self.diagnostics}")
        return "\n".join(lines)
