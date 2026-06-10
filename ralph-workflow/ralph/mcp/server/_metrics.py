"""MCP server observability metrics.

A small, thread-safe counter surface for the post-header failure path, the
streaming terminal-frame path, and the health-probe path. The metrics are
exposed via :func:`snapshot` so the supervisor and operators can read a
stable, named counter set.

The metrics object is constructible with no IO; tests inject a fresh
instance. The default process-wide singleton (:func:`get_default_metrics`)
is used by the production transport.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class McpMetrics:
    """Thread-safe counter surface for the MCP server transport."""

    post_header_failures: int = 0
    terminal_frame_emissions: int = 0
    health_probe_outcomes: dict[str, int] = field(
        default_factory=lambda: {"success": 0, "failure": 0}
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_post_header_failure(
        self,
        *,
        request_id: object,
        method: str,
        session_impl: str,
        cause: str,
    ) -> None:
        """Increment the post-header failure counter."""
        del request_id, method, session_impl, cause
        with self._lock:
            self.post_header_failures += 1

    def record_terminal_frame(self, method: str) -> None:
        """Increment the streaming terminal-frame counter."""
        del method
        with self._lock:
            self.terminal_frame_emissions += 1

    def record_health_probe_outcome(self, success: bool) -> None:
        """Increment the health probe success/failure counter."""
        with self._lock:
            if success:
                self.health_probe_outcomes["success"] = (
                    self.health_probe_outcomes.get("success", 0) + 1
                )
            else:
                self.health_probe_outcomes["failure"] = (
                    self.health_probe_outcomes.get("failure", 0) + 1
                )

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable snapshot of the counter surface."""
        with self._lock:
            return {
                "post_header_failures": self.post_header_failures,
                "terminal_frame_emissions": self.terminal_frame_emissions,
                "health_probe_outcomes": dict(self.health_probe_outcomes),
            }


_default_metrics: McpMetrics | None = None
_default_metrics_lock = threading.Lock()


def get_default_metrics() -> McpMetrics:
    """Return the process-wide default McpMetrics instance."""
    global _default_metrics  # noqa: PLW0603
    with _default_metrics_lock:
        if _default_metrics is None:
            _default_metrics = McpMetrics()
        return _default_metrics


def reset_default_metrics() -> None:
    """Reset the process-wide default metrics. Test-only."""
    global _default_metrics  # noqa: PLW0603
    with _default_metrics_lock:
        _default_metrics = None


__all__ = [
    "McpMetrics",
    "get_default_metrics",
    "reset_default_metrics",
]
