from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field


@dataclass
class McpMetrics:
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
        del request_id, method, session_impl, cause
        with self._lock:
            self.post_header_failures += 1

    def record_terminal_frame(self, method: str) -> None:
        del method
        with self._lock:
            self.terminal_frame_emissions += 1

    def record_health_probe_outcome(self, success: bool) -> None:
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


_default_metrics: McpMetrics | None = None
_default_metrics_holder: list[McpMetrics | None] = [None]
_default_metrics_lock = threading.Lock()


def get_default_metrics() -> McpMetrics:
    if _default_metrics is not None:
        return _default_metrics
    with _default_metrics_lock:
        if _default_metrics is not None:
            return _default_metrics
        metrics = _default_metrics_holder[0]
        if metrics is None:
            metrics = McpMetrics()
            _default_metrics_holder[0] = metrics
        return metrics


def reset_default_metrics() -> None:
    with _default_metrics_lock:
        attr = "_default_metrics"
        setattr(sys.modules[__name__], attr, None)
        _default_metrics_holder[0] = None


__all__ = [
    "McpMetrics",
    "get_default_metrics",
    "reset_default_metrics",
]
