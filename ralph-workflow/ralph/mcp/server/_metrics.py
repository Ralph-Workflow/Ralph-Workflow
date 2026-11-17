from __future__ import annotations

import threading


class McpMetrics:
    __slots__ = (
        "_lock",
        "health_probe_outcomes",
        "post_header_failures",
        "terminal_frame_emissions",
    )

    def __init__(self) -> None:
        self.post_header_failures = 0
        self.terminal_frame_emissions = 0
        self.health_probe_outcomes: dict[str, int] = {"success": 0, "failure": 0}
        self._lock = threading.Lock()

    def record_post_header_failure(
        self,
        *,
        request_id: object,
        method: str,
        session_impl: str,
        cause: str,
    ) -> None:
        del request_id, method, session_impl, cause
        with self._lock:
            self.post_header_failures += 1

    def record_terminal_frame(self, method: str) -> None:
        del method
        with self._lock:
            self.terminal_frame_emissions += 1

    def record_health_probe_outcome(self, success: object) -> None:
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
        with self._lock:
            return {
                "post_header_failures": self.post_header_failures,
                "terminal_frame_emissions": self.terminal_frame_emissions,
                "health_probe_outcomes": dict(self.health_probe_outcomes),
            }


class _DefaultMetricsState:
    __slots__ = ("lock", "metrics")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.metrics: McpMetrics | None = None


_default_metrics_state = _DefaultMetricsState()


def get_default_metrics() -> McpMetrics:
    with _default_metrics_state.lock:
        metrics = _default_metrics_state.metrics
        if metrics is not None:
            return metrics
        metrics = McpMetrics()
        _default_metrics_state.metrics = metrics
        return metrics


def reset_default_metrics() -> None:
    with _default_metrics_state.lock:
        _default_metrics_state.metrics = None


__all__ = [
    "McpMetrics",
    "get_default_metrics",
    "reset_default_metrics",
]
