"""Structured classified failure model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .failure_category import FailureCategory
    from .unavailability_reason import UnavailabilityReason


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failure with its category, attribution, and budget-counting decision."""

    category: FailureCategory
    reason: str
    attributed_agent: str | None
    attributed_phase: str
    counts_against_budget: bool
    original_exception: BaseException | None
    raw_message: str
    reset_session: bool = field(default=False)
    # When True, the next recovery attempt should call
    # `RestartAwareMcpBridge.reset_tool_registry()` to rebuild the
    # visible tool list. Set by the failure classifier when the live
    # MCP server reports a missing tool via the
    # "No such tool available: mcp__<server>__<tool>" string (the
    # post-tool-result wedge failure mode) or when a runtime
    # `ToolDispatchError` is raised with an "is not registered" message.
    reset_tool_registry: bool = field(default=False)
    # When True, the agent is considered temporarily unavailable (e.g. out
    # of credits) and the recovery controller should skip it with exponential
    # backoff instead of retrying immediately.
    is_unavailable: bool = field(default=False)
    watchdog_reason: str | None = field(default=None)
    unavailability_reason: UnavailabilityReason | None = field(default=None)
    # The transport-level session id the killed agent was running
    # under. Captured per-line by
    # ``_run_subprocess_and_read_lines`` and threaded through
    # ``AgentInactivityTimeoutError.opts.resumable_session_id`` AND
    # through ``IdleWatchdogKilledError.resumable_session_id`` on the
    # ``__cause__`` chain. Populated by ``FailureClassifier.classify``
    # when the watchdog kill surfaces a usable session id; consumed
    # by ``RecoveryController.handle`` to populate
    # ``state.last_agent_session_id`` so the existing
    # ``_apply_chain_retry`` resume path emits a resume intent with
    # the captured id (instead of starting a fresh session). None
    # means ``unknown / not captured``; the controller MUST NOT set
    # ``last_agent_session_id`` when this is None.
    resumable_session_id: str | None = field(default=None)
