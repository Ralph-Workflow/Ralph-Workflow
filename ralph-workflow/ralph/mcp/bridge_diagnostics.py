"""Public diagnostics for the restart-aware MCP bridge.

The bridge exposes one immutable diagnostics snapshot instead of asking callers
to inspect private counters.  The snapshot is intended for operators,
supervisors, and black-box tests: every field describes state that affects the
bridge's public behaviour (restart budget, probe classification, carried tool
catalogue, and handover timing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ProbeFailureClass = Literal["timeout", "saturated", "fatal"]


def classify_probe_failure(
    exc: Exception | None,
    *,
    overrun: bool = False,
) -> ProbeFailureClass:
    """Classify one health-probe outcome for the bridge's public API.

    ``timeout`` outcomes may be deferred while the host is contended.
    ``saturated`` is a busy signal, not a failed server.  Every other outcome
    is ``fatal`` and must not be hidden behind contention deferral.
    """
    if exc is None:
        return "timeout" if overrun else "fatal"
    exc_text = str(exc).lower()
    if "saturated_dispatch" in exc_text or "_saturated_dispatch" in exc_text:
        return "saturated"
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, BrokenPipeError)) or (
        "connection reset" in exc_text
        or "connection refused" in exc_text
        or "broken pipe" in exc_text
        or isinstance(exc, (ValueError, json.JSONDecodeError, UnicodeDecodeError))
        or any(code in exc_text for code in ("500", "502", "503", "504"))
        or "malformed" in exc_text
        or "spawn_id_mismatch" in exc_text
        or "protocol" in exc_text
    ):
        return "fatal"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if any(marker in exc_text for marker in ("timed out", "timeout", "probe failed")):
        return "timeout"
    return "fatal"


def bound_dominates(bound: float, components: tuple[float, ...]) -> bool:
    """Return whether ``bound`` dominates every positive duration in ``components``.

    This is the arithmetic-dominance predicate used by the lifecycle's
    import-time restart-budget guard.  It is public so the same rule can be
    evaluated without reaching into the lifecycle module's private helpers.
    """
    if not components or not bound > 0.0:
        return False
    return all(component > 0.0 for component in components) and bound >= sum(components)


@dataclass(frozen=True)
class BridgeDiagnostics:
    """Immutable snapshot of restart-aware bridge state and operational limits."""

    restart_count: int
    restart_attempt_count: int
    generation: int
    consecutive_probe_failures: int
    consecutive_probe_successes: int
    deferred_probe_failures: int
    first_probe_deferred: bool
    session_id: str | None
    broker_fingerprint: str | None
    tool_set_hash: str | None
    tool_set_entries: tuple[dict[str, object], ...]
    tool_set_baseline_captured: bool
    tool_set_baseline_failed: bool
    draining_process_count: int
    probe_deferral_count_ceiling: int
    probe_deferral_wall_time_ceiling_s: float
    probe_deadline_slack_s: float
    tool_registry_max_resets: int
    respawn_alias_verify_timeout_s: float
    initial_tool_hash_timeout_s: float
    candidate_deadline_s: float
    preflight_deadline_s: float
    candidate_startup_deadline_s: float
    handover_ack_timeout_s: float
    handover_kill_wait_s: float
    handover_drain_s: float
    max_restarts: int
    cooldown_seconds: float
    worst_case_terminal_exhaustion_dead_child_s: float
    candidate_delay_bound_alive_child_s: float
    failed_attempt_cleanup_s: float
    bound_a_components_s: tuple[float, ...]
    bound_b_components_s: tuple[float, ...]


__all__ = [
    "BridgeDiagnostics",
    "ProbeFailureClass",
    "bound_dominates",
    "classify_probe_failure",
]
